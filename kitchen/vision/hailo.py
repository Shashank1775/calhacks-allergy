"""Hailo-8 backend: runs the YOLOv8 HEFs with HailoRT and hands back exactly what the ONNX models return.

Owner: vision (agents)
Step:  vision
Reads: a deploy bundle, data/models/<name>/ (manifest.json, det.hef, pose.hef; built by bundle.py); letterboxed frames
       as the ONNX models take them (float32 [1, 3, H, W], 0 to 1)
Emits: HailoSession, a drop-in for onnxruntime.InferenceSession whose run() returns the ONNX-shaped output
       ([1, 4 + classes, anchors] for detection, [1, 56, anchors] for pose), so detect.py runs one post-processing path
       for both backends; `python -m kitchen.vision.hailo --bundle <dir>` is the device check the owner runs on the Pi
Rules: fail closed: a device error or timeout raises; it never returns an empty or made-up output, so frames stop,
       /health goes stale and the gate faults; a detection HEF whose class count isn't the bundle manifest's raises
       at startup; hailo_platform is imported only when the device is opened, so the laptop, CI and every other
       module never need it

The two HEFs differ. The detection HEF from Hailo's Model Zoo runs NMS inside HailoRT and returns, per class, rows of
[y_min, x_min, y_max, x_max, score] normalized to the input. Its classes come in the output order of the ONNX it was
compiled from, the order of the manifest's class_names, so class i keeps score row 4 + i. The pose HEF returns nine
raw head outputs (box distributions, person score and keypoints at strides 8, 16 and 32) that are decoded on the host
with the same math as Ultralytics' ONNX export.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from kitchen.vision.detect import detector_classes

# YOLOv8 heads: three strides, 16 distance bins per box side (distribution focal loss), 17 keypoints of (x, y, visibility).
STRIDES = (8, 16, 32)
DFL_BINS = 16
KEYPOINTS = 17
POSE_CHANNELS = 4 + 1 + 3 * KEYPOINTS

# HailoRT's NMS drops boxes below this score. It sits below any threshold detect.py uses, so detect.py's own
# threshold decides what counts, exactly as it does for the ONNX output.
NMS_MIN_SCORE = 0.05

# One frame should take a few milliseconds; a run this slow means the device is stuck, so it raises (fail closed).
RUN_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class TensorInfo:
    """Name and shape of a network input or output, like the NodeArg onnxruntime's get_inputs() returns."""

    name: str
    shape: list[int]


def anchor_count(height: int, width: int) -> int:
    """How many columns YOLOv8 outputs for an input size: one per grid cell, over the three strides."""
    return sum((height // stride) * (width // stride) for stride in STRIDES)


def detections_to_onnx(per_class: list, height: int, width: int) -> np.ndarray:
    """Turn the detection HEF's NMS output into the [1, 4 + classes, anchors] array the detection ONNX returns.

    Each surviving box becomes one column: center x, center y, width and height in input pixels, then its score in
    its class's row. Every other column stays zero, so it falls below any score threshold. Running detect.py's NMS on
    boxes HailoRT already suppressed keeps them, so the result matches the ONNX path.
    """
    anchors = anchor_count(height, width)
    out = np.zeros((1, 4 + len(per_class), anchors), dtype=np.float32)
    column = 0
    for class_id, rows in enumerate(per_class):
        for y_min, x_min, y_max, x_max, score in np.asarray(rows, dtype=np.float32).reshape(-1, 5):
            if column == anchors:
                raise ValueError(f"the detection HEF returned more than {anchors} boxes")
            out[0, 0, column] = (x_min + x_max) / 2 * width
            out[0, 1, column] = (y_min + y_max) / 2 * height
            out[0, 2, column] = (x_max - x_min) * width
            out[0, 3, column] = (y_max - y_min) * height
            out[0, 4 + class_id, column] = score
            column += 1
    return out


def _softmax(x: np.ndarray) -> np.ndarray:
    """Softmax over the last axis, shifted by the max so large logits don't overflow."""
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Logistic function."""
    return 1 / (1 + np.exp(-x))


def _heads_at(heads: dict, rows: int, cols: int) -> dict[int, np.ndarray]:
    """The raw head outputs with a rows x cols grid, keyed by channel count and flattened to [rows * cols, channels].

    HailoRT's output names depend on how the HEF was compiled, so heads are found by shape: 64 channels are the box
    distributions, 1 is the person score and 51 are the keypoints.
    """
    found = {}
    for raw in heads.values():
        grid = np.asarray(raw, dtype=np.float32)
        grid = grid.reshape(grid.shape[-3:])
        if grid.shape[:2] == (rows, cols):
            found[grid.shape[2]] = grid.reshape(rows * cols, -1)
    if set(found) != {4 * DFL_BINS, 1, 3 * KEYPOINTS}:
        raise ValueError(f"the pose HEF has no recognizable heads at {rows}x{cols}: channels {sorted(found)}")
    return found


def pose_heads_to_onnx(heads: dict, height: int, width: int) -> np.ndarray:
    """Decode the pose HEF's nine raw heads into the [1, 56, anchors] array the pose ONNX returns.

    Same math as Ultralytics' export, so the columns line up one for one: strides 8, 16 then 32, each grid row-major.
    Rows 0 to 3 are the box (center x, center y, width, height in input pixels), row 4 the person score, then
    17 keypoints of (x, y, visibility). The HEF applies the score's sigmoid on the chip; the keypoint visibility's
    sigmoid is applied here.
    """
    columns = []
    for stride in STRIDES:
        rows, cols = height // stride, width // stride
        found = _heads_at(heads, rows, cols)
        grid_y, grid_x = np.divmod(np.arange(rows * cols), cols)

        # Each box side is a distribution over 16 bins; its expected bin times the stride is the distance from the
        # cell center to that side (left, top, right, bottom).
        distance = _softmax(found[4 * DFL_BINS].reshape(-1, 4, DFL_BINS)) @ np.arange(DFL_BINS) * stride
        center_x = (grid_x + 0.5) * stride
        center_y = (grid_y + 0.5) * stride
        x1, y1 = center_x - distance[:, 0], center_y - distance[:, 1]
        x2, y2 = center_x + distance[:, 2], center_y + distance[:, 3]
        box = np.stack([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, found[1][:, 0]], axis=1)

        raw_keypoints = found[3 * KEYPOINTS].reshape(-1, KEYPOINTS, 3)
        keypoints = np.stack(
            [
                (raw_keypoints[..., 0] * 2 + grid_x[:, None]) * stride,
                (raw_keypoints[..., 1] * 2 + grid_y[:, None]) * stride,
                _sigmoid(raw_keypoints[..., 2]),
            ],
            axis=-1,
        )
        columns.append(np.concatenate([box, keypoints.reshape(-1, 3 * KEYPOINTS)], axis=1))
    return np.concatenate(columns).T[None].astype(np.float32)


def to_hailo_input(tensor: np.ndarray, height: int, width: int) -> np.ndarray:
    """Turn the ONNX input (float32 [1, 3, H, W], 0 to 1) into the HEF input (uint8 [H, W, 3]).

    The frame started as uint8 before detect.py divided it by 255, so this is lossless. The HEF normalizes on the chip.
    """
    if tensor.shape != (1, 3, height, width):
        raise ValueError(f"expected a [1, 3, {height}, {width}] input, got {list(tensor.shape)}")
    pixels = np.rint(np.clip(tensor[0], 0, 1) * 255).astype(np.uint8)
    return np.ascontiguousarray(pixels.transpose(1, 2, 0))


class HailoSession:
    """A drop-in for onnxruntime.InferenceSession over one YOLOv8 HEF: the same get_inputs(), get_outputs() and run().

    `network` runs one uint8 frame on the device and returns its raw outputs by name (HailoNetwork on the Pi; a fake in
    tests). Detection or pose is told apart by whether the HEF ends in NMS.
    """

    def __init__(self, network):
        self._network = network
        self.height, self.width = network.input_shape[:2]
        if network.is_nms:
            self.kind = "detect"
            channels = 4 + network.num_classes
        else:
            self.kind = "pose"
            channels = POSE_CHANNELS
        self._inputs = [TensorInfo("images", [1, 3, self.height, self.width])]
        self._outputs = [TensorInfo("output0", [1, channels, anchor_count(self.height, self.width)])]

    def get_inputs(self) -> list[TensorInfo]:
        """The one input, named and shaped like the Ultralytics ONNX input."""
        return self._inputs

    def get_outputs(self) -> list[TensorInfo]:
        """The one output, named and shaped like the Ultralytics ONNX output."""
        return self._outputs

    def run(self, output_names, input_feed: dict) -> list[np.ndarray]:
        """Run one frame and return [output] as onnxruntime would. Any device error propagates (fail closed)."""
        (tensor,) = input_feed.values()
        raw = self._network.infer(to_hailo_input(np.asarray(tensor), self.height, self.width))
        if self.kind == "detect":
            (per_class,) = raw.values()
            return [detections_to_onnx(per_class, self.height, self.width)]
        return [pose_heads_to_onnx(raw, self.height, self.width)]


class HailoNetwork:
    """One HEF configured on the shared Hailo-8, run synchronously one frame at a time. Only exists on the Pi."""

    def __init__(self, vdevice, hef_path: Path):
        from hailo_platform import HEF, FormatType

        self._vdevice = vdevice  # keeps the device open as long as a network uses it
        self._model = vdevice.create_infer_model(str(hef_path))
        self._model.set_batch_size(1)
        self._model.input().set_format_type(FormatType.UINT8)
        for output in self._model.outputs:
            output.set_format_type(FormatType.FLOAT32)

        output_info = HEF(str(hef_path)).get_output_vstream_infos()[0]
        self.is_nms = "NMS" in str(output_info.format.order)
        self.num_classes = output_info.nms_shape.number_of_classes if self.is_nms else 0
        if self.is_nms:
            self._model.output().set_nms_score_threshold(NMS_MIN_SCORE)
        self.input_shape = tuple(self._model.input().shape)

        self._configured = self._model.configure().__enter__()

    def infer(self, frame: np.ndarray) -> dict:
        """Run one uint8 [H, W, 3] frame and return each output's buffer by name. Raises on any device error."""
        buffers = {output.name: np.empty(output.shape, dtype=np.float32) for output in self._model.outputs}
        bindings = self._configured.create_bindings(output_buffers=buffers)
        bindings.input().set_buffer(frame)
        self._configured.run([bindings], RUN_TIMEOUT_MS)
        return {name: bindings.output(name).get_buffer() for name in buffers}


def check_detection_classes(session: HailoSession, classes: int) -> None:
    """Refuse a detection HEF that scores another number of classes than the bundle manifest says (fail closed).

    A stock 80-class HEF left in a custom bundle, or a custom HEF in a stock one, would put each class's boxes under
    another class's name, so this runs at startup, before any frame.
    """
    hef_classes = session.get_outputs()[0].shape[1] - 4
    if hef_classes != classes:
        raise ValueError(
            f"the det HEF scores {hef_classes} classes, but the bundle manifest has {classes}; "
            "rebuild the HEFs with the bundle's compile.sh"
        )


def read_manifest(bundle_dir: Path) -> dict:
    """The bundle's manifest.json, written by bundle.py."""
    return json.loads((Path(bundle_dir) / "manifest.json").read_text())


def open_sessions(bundle_dir: Path) -> dict[str, HailoSession]:
    """Open the Hailo-8 once and load every network in a bundle: {"det": HailoSession, "pose": HailoSession}.

    Both networks share one device through HailoRT's round-robin scheduler. The detection HEF must score as many
    classes as the manifest names (check_detection_classes), so bad class names or a mismatched HEF raise here.
    """
    from hailo_platform import HailoSchedulingAlgorithm, VDevice

    bundle_dir = Path(bundle_dir)
    networks = read_manifest(bundle_dir)["networks"]
    classes = detector_classes(networks["det"])

    params = VDevice.create_params()
    params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
    vdevice = VDevice(params)
    sessions = {
        name: HailoSession(HailoNetwork(vdevice, bundle_dir / network["hef"])) for name, network in networks.items()
    }
    check_detection_classes(sessions["det"], classes.count)
    return sessions


def frames_per_second(sessions: list[HailoSession], frames: np.ndarray, count: int) -> float:
    """Frames per second when every frame goes through each session in turn (det + pose is what the Pi must hold)."""
    start = time.perf_counter()
    for i in range(count):
        tensor = frames[i % len(frames)].transpose(2, 0, 1)[None].astype(np.float32) / 255
        for session in sessions:
            session.run(None, {"images": tensor})
    return count / (time.perf_counter() - start)


def check_device(bundle_dir: Path, count: int) -> dict:
    """The owner's on-Pi check: versions, shapes, the top score on the first frame, and FPS for det, pose and both."""
    import hailo_platform

    bundle_dir = Path(bundle_dir)
    manifest = read_manifest(bundle_dir)
    frames = np.load(bundle_dir / manifest["parity"]["inputs"])
    sessions = open_sessions(bundle_dir)

    report = {"hailort": hailo_platform.__version__, "bundle_expects_hailort": manifest["hailo"]["hailort"]}
    first = frames[0].transpose(2, 0, 1)[None].astype(np.float32) / 255
    for name, session in sessions.items():
        output = session.run(None, {"images": first})[0]
        # Detection has one score row per class from row 4 on; pose has one score row, then keypoints.
        scores = output[0, 4:5] if session.kind == "pose" else output[0, 4:]
        report[name] = {
            "kind": session.kind,
            "output_shape": list(output.shape),
            "top_score_first_frame": round(float(scores.max()), 3),
            "fps_alone": round(frames_per_second([session], frames, count), 1),
        }
    report["fps_det_and_pose"] = round(frames_per_second(list(sessions.values()), frames, count), 1)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m kitchen.vision.hailo --bundle data/models/<name> [--frames 300]` prints the device check."""
    parser = argparse.ArgumentParser(description=check_device.__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=300)
    args = parser.parse_args(argv)
    report = check_device(args.bundle, args.frames)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
