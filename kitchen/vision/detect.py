"""Detection: YOLOv8 detection and pose on one frame, with the pre- and post-processing both backends share
(letterbox, decoding, NMS, keypoints, class map).

Owner: vision (agents)
Step:  vision
Reads: SourceFrame from camera.py; the deploy bundle in settings.bundle_dir (manifest.json, det.onnx and pose.onnx, or
       the HEFs through hailo.py); VISION_DETECTOR=onnx|hailo (default onnx)
Emits: Detector.detect(frame) -> (list[Detection], list[Person]), boxes and keypoints normalized to the original frame
Rules: fail closed: a missing or broken bundle raises at startup and a wrong-shaped output or one with NaN or inf values
       raises per frame, so there is never a detector that silently returns nothing; detections and keypoints are only ever decoded, never invented
       or interpolated; backends only run the network and one post-processing path serves both, so local results
       predict Hailo results; onnxruntime and hailo_platform are imported only when a detector is opened

The letterbox is kitchen/shared/clips.py's (letterbox_frame and letterbox_layout at settings.input_size, gray 114), so
the detector, the change mask and the clip builder all place a frame inside its square the same way.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from kitchen.shared.clips import letterbox_frame, letterbox_layout
from kitchen.vision.common import COCO_OBJECTS, KEYPOINTS, Box, Detection, Keypoint, Person, SourceFrame, VisionSettings

# Ultralytics output rows: box (center x, center y, width, height in input pixels), then 80 COCO class scores for
# detection, or one person score and 17 keypoints of (x, y, visibility) for pose.
COCO_CLASSES = 80
POSE_KEYPOINTS = 17
DET_CHANNELS = 4 + COCO_CLASSES
POSE_CHANNELS = 4 + 1 + 3 * POSE_KEYPOINTS

BACKENDS = ("onnx", "hailo")


@dataclass(frozen=True)
class Letterbox:
    """Where the original frame sits inside the square network input, in input pixels."""

    content_width: int
    content_height: int
    left: int
    top: int


def letterbox_for(frame_width: int, frame_height: int, input_size: int) -> Letterbox:
    """The letterbox a frame of this size gets at this input size, from the shared clips.py layout."""
    return Letterbox(*letterbox_layout(frame_width, frame_height, input_size))


def to_input_tensor(rgb: np.ndarray, input_size: int) -> np.ndarray:
    """Letterbox a uint8 RGB frame to the network input: float32 [1, 3, S, S], RGB, 0 to 1.

    Uses clips.py's letterbox_frame, which refuses frames smaller than the input rather than upscaling them.
    """
    square = letterbox_frame(rgb, input_size)
    return np.ascontiguousarray(square.transpose(2, 0, 1)[None], dtype=np.float32) / 255


def _to_frame_x(x: np.ndarray, layout: Letterbox) -> np.ndarray:
    """Input-pixel x to normalized original-frame x, clipped to [0, 1]."""
    return np.clip((x - layout.left) / layout.content_width, 0.0, 1.0)


def _to_frame_y(y: np.ndarray, layout: Letterbox) -> np.ndarray:
    """Input-pixel y to normalized original-frame y, clipped to [0, 1]."""
    return np.clip((y - layout.top) / layout.content_height, 0.0, 1.0)


def _corner_boxes(columns: np.ndarray) -> np.ndarray:
    """[N, 4] center-x, center-y, width, height to [N, 4] x1, y1, x2, y2 (still in input pixels)."""
    cx, cy, w, h = columns[:, 0], columns[:, 1], columns[:, 2], columns[:, 3]
    return np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)


def _to_frame_boxes(boxes: np.ndarray, layout: Letterbox) -> np.ndarray:
    """[N, 4] input-pixel corner boxes to normalized original-frame boxes, clipped to [0, 1]."""
    return np.stack(
        [
            _to_frame_x(boxes[:, 0], layout),
            _to_frame_y(boxes[:, 1], layout),
            _to_frame_x(boxes[:, 2], layout),
            _to_frame_y(boxes[:, 3], layout),
        ],
        axis=1,
    )


def iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    """IoU of one corner box [4] with each of [N, 4] corner boxes."""
    top_left = np.maximum(box[:2], others[:, :2])
    bottom_right = np.minimum(box[2:], others[:, 2:])
    overlap = np.prod(np.clip(bottom_right - top_left, 0, None), axis=1)
    area = np.prod(np.clip(box[2:] - box[:2], 0, None))
    other_areas = np.prod(np.clip(others[:, 2:] - others[:, :2], 0, None), axis=1)
    return overlap / np.maximum(area + other_areas - overlap, 1e-9)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression: indices kept, best score first.

    Ties keep their original order (stable sort), so the same output always gives the same detections.
    """
    order = np.argsort(-scores, kind="stable")
    keep = []
    while len(order):
        best = order[0]
        keep.append(int(best))
        rest = order[1:]
        order = rest[iou(boxes[best], boxes[rest]) < iou_threshold]
    return keep


def _columns(output: np.ndarray, channels: int, network: str) -> np.ndarray:
    """One Ultralytics output [1, channels, anchors] as [anchors, channels]; a wrong shape or a NaN or inf raises.

    Both raise (fail closed). A NaN score fails every score floor, so an unfilled or broken output would otherwise
    decode to an empty kitchen, and a NaN keypoint with a good visibility would reach tracking as a confident wrist.
    """
    output = np.asarray(output, dtype=np.float32)
    if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] != channels:
        raise ValueError(f"{network} output must be [1, {channels}, anchors], got {list(output.shape)}")
    if not np.isfinite(output).all():
        raise ValueError(f"{network} output has NaN or inf values")
    return output[0].T


def _has_area(box: np.ndarray) -> bool:
    """Whether a clipped box still covers part of the frame. A box wholly inside the letterbox padding doesn't."""
    return bool(box[2] > box[0] and box[3] > box[1])


def decode_objects(output: np.ndarray, frame_size: tuple[int, int], settings: VisionSettings) -> list[Detection]:
    """Turn the detection network's [1, 84, anchors] output into the knives, spoons, forks and bowls in one frame.

    Each anchor takes its best class over all 80 (as Ultralytics does), so an anchor that is mostly a person or a cup
    is dropped rather than relabeled as a tool. Then the score floor, per-class NMS, and the boxes mapped back through
    the letterbox to the original frame (frame_size is its (width, height)). Best confidence first.
    """
    columns = _columns(output, DET_CHANNELS, "detection")
    scores = columns[:, 4:].max(axis=1)
    classes = columns[:, 4:].argmax(axis=1)
    wanted = np.isin(classes, list(COCO_OBJECTS)) & (scores >= settings.object_min_score)
    boxes, scores, classes = _corner_boxes(columns[wanted]), scores[wanted], classes[wanted]

    layout = letterbox_for(*frame_size, settings.input_size)
    found = []
    for class_id in sorted(set(classes.tolist())):
        same = np.flatnonzero(classes == class_id)
        for i in same[nms(boxes[same], scores[same], settings.nms_iou)]:
            box = _to_frame_boxes(boxes[i : i + 1], layout)[0]
            if _has_area(box):
                found.append(Detection(COCO_OBJECTS[class_id], _box(box), float(scores[i])))
    return sorted(found, key=lambda detection: -detection.confidence)


def decode_people(output: np.ndarray, frame_size: tuple[int, int], settings: VisionSettings) -> list[Person]:
    """Turn the pose network's [1, 56, anchors] output into the people in one frame, with elbows and wrists.

    Score floor, NMS, then the box and the four KEYPOINTS mapped back to the original frame. Each keypoint keeps the
    network's own visibility as its confidence: a hidden wrist stays a low-confidence wrist for tracking.py to reject,
    never a guessed position. Best confidence first.
    """
    columns = _columns(output, POSE_CHANNELS, "pose")
    wanted = columns[:, 4] >= settings.person_min_score
    columns = columns[wanted]
    boxes, scores = _corner_boxes(columns), columns[:, 4]

    layout = letterbox_for(*frame_size, settings.input_size)
    found = []
    for i in nms(boxes, scores, settings.nms_iou):
        box = _to_frame_boxes(boxes[i : i + 1], layout)[0]
        if not _has_area(box):
            continue
        keypoints = {name: _keypoint(columns[i], index, layout) for name, index in KEYPOINTS.items()}
        found.append(Person(box=_box(box), confidence=float(scores[i]), **keypoints))
    return sorted(found, key=lambda person: -person.confidence)


def _keypoint(column: np.ndarray, index: int, layout: Letterbox) -> Keypoint:
    """Keypoint `index` of one pose column, normalized to the original frame, with its visibility as confidence."""
    x, y, visibility = column[5 + 3 * index : 8 + 3 * index]
    return (float(_to_frame_x(x, layout)), float(_to_frame_y(y, layout)), float(visibility))


def _box(box: np.ndarray) -> Box:
    """A numpy box as the plain-float tuple the shapes in common.py use."""
    x1, y1, x2, y2 = (float(v) for v in box)
    return (x1, y1, x2, y2)


class Detector:
    """Runs detection and pose on frames through two sessions, with the one post-processing path both backends share.

    `sessions` is {"det": session, "pose": session}, where a session is anything with onnxruntime's get_inputs() and
    run(None, {name: tensor}): an onnxruntime.InferenceSession, a hailo.HailoSession or a fake in tests. `backend`
    ("onnx" or "hailo") is what /health reports.
    """

    def __init__(self, sessions: dict, settings: VisionSettings, backend: str):
        if set(sessions) != {"det", "pose"}:
            raise ValueError(f"a detector needs exactly the det and pose sessions, got {sorted(sessions)}")
        if backend not in BACKENDS:
            raise ValueError(f"unknown detector backend {backend!r}; expected one of {BACKENDS}")
        size = settings.input_size
        for name, session in sessions.items():
            shape = list(session.get_inputs()[0].shape)
            if shape != [1, 3, size, size]:
                raise ValueError(f"the {name} network takes {shape}, but VISION_INPUT_SIZE is {size}")
        self.sessions = sessions
        self.settings = settings
        self.backend = backend

    def detect(self, frame: SourceFrame) -> tuple[list[Detection], list[Person]]:
        """Detect the objects and people in one frame. Any session error propagates: no frame, not an empty one."""
        height, width = frame.rgb.shape[:2]
        tensor = to_input_tensor(frame.rgb, self.settings.input_size)
        objects = decode_objects(self._run("det", tensor), (width, height), self.settings)
        people = decode_people(self._run("pose", tensor), (width, height), self.settings)
        return objects, people

    def _run(self, name: str, tensor: np.ndarray) -> np.ndarray:
        """Run one session on the input tensor and return its first output."""
        session = self.sessions[name]
        return session.run(None, {session.get_inputs()[0].name: tensor})[0]


def read_bundle_manifest(bundle_dir: Path) -> dict:
    """The bundle's manifest.json, checked to name a det and a pose network. A missing or broken bundle raises."""
    path = Path(bundle_dir) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no deploy bundle at {bundle_dir} (missing manifest.json); build one with kitchen.vision.bundle "
            "or set VISION_BUNDLE_DIR"
        )
    manifest = json.loads(path.read_text())
    networks = manifest.get("networks", {})
    if not {"det", "pose"} <= set(networks):
        raise ValueError(f"the bundle at {bundle_dir} must have det and pose networks, has {sorted(networks)}")
    return manifest


def open_onnx_sessions(bundle_dir: Path, networks: dict) -> dict:
    """One ONNX Runtime CPU session per network (det, pose), from the bundle's ONNX files."""
    paths = {name: Path(bundle_dir) / networks[name]["onnx"] for name in ("det", "pose")}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"the bundle at {bundle_dir} is missing {path.name}")

    import onnxruntime

    return {
        name: onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        for name, path in paths.items()
    }


def open_detector(settings: VisionSettings) -> Detector:
    """Open the detector settings.detector names, on the bundle in settings.bundle_dir. Called once at startup.

    Fail closed: an unknown backend, a missing bundle, a missing network file or a model of the wrong input size raises
    here, so the vision service never starts with a detector that can't see.
    """
    if settings.detector not in BACKENDS:
        raise ValueError(f"VISION_DETECTOR must be one of {BACKENDS}, got {settings.detector!r}")
    bundle_dir = Path(settings.bundle_dir)
    networks = read_bundle_manifest(bundle_dir)["networks"]
    if settings.detector == "hailo":
        from kitchen.vision.hailo import open_sessions

        sessions = open_sessions(bundle_dir)
    else:
        sessions = open_onnx_sessions(bundle_dir, networks)
    return Detector(sessions, settings, settings.detector)
