"""ONNX-vs-HEF parity: run the bundle's parity frames through one backend, then compare two backends' detections.

Owner: vision (agents)
Step:  vision
Reads: data/models/<name>/ (parity/inputs.npy, det.onnx and pose.onnx on the laptop, det.hef and pose.hef on the Pi)
Emits: parity/<backend>.npz (detections per network and frame) and a JSON report; `compare` exits 1 on disagreement
Rules: measures the networks and nothing else: one small reference decode (score filter + per-class NMS) runs on both
       outputs, so detect.py's real post-processing is not involved; tolerances are flags with the defaults below

Usage:
  laptop:  uv run python -m kitchen.vision.parity run --bundle data/models/kitchen-v1 --backend onnx
  Pi:      python -m kitchen.vision.parity run --bundle data/models/kitchen-v1 --backend hailo
  laptop:  uv run python -m kitchen.vision.parity compare data/models/kitchen-v1/parity/onnx.npz \
               data/models/kitchen-v1/parity/hailo.npz
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

POSE_CHANNELS = 56

# Reference decode: what counts as a detection in both outputs.
MIN_SCORE = 0.25
NMS_IOU = 0.45

# Pass when the HEF finds nearly the same boxes as the ONNX, in nearly the same places, with nearly the same scores.
# Quantizing to 8 bits moves scores a little, so boxes near MIN_SCORE can appear in only one output.
MATCH_IOU = 0.5
MIN_RECALL = 0.9
MIN_PRECISION = 0.9
MIN_MEAN_IOU = 0.8
MAX_MEAN_SCORE_DIFF = 0.1


def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of [N, 4] and [M, 4] boxes (x1, y1, x2, y2) as an [N, M] array."""
    top_left = np.maximum(a[:, None, :2], b[None, :, :2])
    bottom_right = np.minimum(a[:, None, 2:4], b[None, :, 2:4])
    overlap = np.prod(np.clip(bottom_right - top_left, 0, None), axis=2)
    area_a = np.prod(a[:, 2:4] - a[:, :2], axis=1)
    area_b = np.prod(b[:, 2:4] - b[:, :2], axis=1)
    return overlap / np.maximum(area_a[:, None] + area_b[None, :] - overlap, 1e-9)


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Indices kept by greedy non-maximum suppression, best score first."""
    order = list(np.argsort(-scores))
    keep = []
    while order:
        best = order.pop(0)
        keep.append(int(best))
        if order:
            overlaps = iou(boxes[[best]], boxes[order])[0]
            order = [i for i, overlap in zip(order, overlaps, strict=True) if overlap < threshold]
    return keep


def decode(output: np.ndarray, min_score: float = MIN_SCORE, nms_iou: float = NMS_IOU) -> np.ndarray:
    """Reference decode of one ONNX-shaped output ([1, C, anchors]) into detection rows.

    Each row is x1, y1, x2, y2, score, class, then the 51 keypoint values for pose. Detection is told from pose by
    its channel count (pose has 56).
    """
    columns = np.asarray(output, dtype=np.float32)[0].T
    if columns.shape[1] == POSE_CHANNELS:
        scores, classes, extra = columns[:, 4], np.zeros(len(columns)), columns[:, 5:]
    else:
        scores, classes, extra = columns[:, 4:].max(axis=1), columns[:, 4:].argmax(axis=1), columns[:, :0]
    kept = scores >= min_score
    cx, cy, w, h = columns[kept, :4].T
    rows = np.column_stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, scores[kept], classes[kept], extra[kept]])

    survivors = []
    for class_id in np.unique(rows[:, 5]):
        same_class = rows[rows[:, 5] == class_id]
        survivors.extend(same_class[nms(same_class[:, :4], same_class[:, 4], nms_iou)])
    width = 6 + extra.shape[1]
    return np.array(survivors, dtype=np.float32).reshape(-1, width)


def onnx_sessions(bundle_dir: Path) -> dict:
    """The laptop backend: one ONNX Runtime session per network in the bundle."""
    import onnxruntime

    networks = json.loads((bundle_dir / "manifest.json").read_text())["networks"]
    return {
        name: onnxruntime.InferenceSession(str(bundle_dir / network["onnx"]), providers=["CPUExecutionProvider"])
        for name, network in networks.items()
    }


def run_backend(sessions: dict, frames: np.ndarray) -> dict[str, np.ndarray]:
    """Run every parity frame (uint8 [N, H, W, 3]) through every session; returns {"<network>_<frame>": rows}."""
    detections = {}
    for name, session in sessions.items():
        input_name = session.get_inputs()[0].name
        for i, frame in enumerate(frames):
            tensor = frame.transpose(2, 0, 1)[None].astype(np.float32) / 255
            detections[f"{name}_{i}"] = decode(session.run(None, {input_name: tensor})[0])
    return detections


def match(a: np.ndarray, b: np.ndarray, match_iou: float) -> list[tuple[int, int, float]]:
    """Greedy one-to-one matching of same-class rows by IoU: (index in a, index in b, IoU), best IoU first."""
    if len(a) == 0 or len(b) == 0:
        return []
    overlaps = iou(a[:, :4], b[:, :4])
    overlaps[a[:, 5][:, None] != b[:, 5][None, :]] = 0
    pairs = []
    while True:
        i, j = np.unravel_index(np.argmax(overlaps), overlaps.shape)
        if overlaps[i, j] < match_iou:
            return pairs
        pairs.append((int(i), int(j), float(overlaps[i, j])))
        overlaps[i, :] = 0
        overlaps[:, j] = 0


def compare(reference: dict, candidate: dict, match_iou: float = MATCH_IOU) -> dict:
    """Compare a candidate backend's detections against the reference's, per network, and say whether they agree."""
    report = {}
    for name in sorted({key.rsplit("_", 1)[0] for key in reference}):
        keys = [key for key in reference if key.rsplit("_", 1)[0] == name]
        matched = in_reference = in_candidate = 0
        ious, score_diffs, keypoint_errors = [], [], []
        for key in keys:
            a, b = reference[key], candidate[key]
            pairs = match(a, b, match_iou)
            matched += len(pairs)
            in_reference += len(a)
            in_candidate += len(b)
            for i, j, overlap in pairs:
                ious.append(overlap)
                score_diffs.append(abs(float(a[i, 4] - b[j, 4])))
                if a.shape[1] > 6:
                    keypoint_errors.append(float(np.abs(a[i, 6::3] - b[j, 6::3]).mean()))
        result = {
            "frames": len(keys),
            "reference_detections": in_reference,
            "candidate_detections": in_candidate,
            "recall": matched / in_reference if in_reference else 1.0,
            "precision": matched / in_candidate if in_candidate else 1.0,
            "mean_iou": float(np.mean(ious)) if ious else 1.0,
            "mean_score_diff": float(np.mean(score_diffs)) if score_diffs else 0.0,
        }
        if keypoint_errors:
            result["mean_keypoint_x_error_px"] = float(np.mean(keypoint_errors))
        result["agree"] = (
            result["recall"] >= MIN_RECALL
            and result["precision"] >= MIN_PRECISION
            and result["mean_iou"] >= MIN_MEAN_IOU
            and result["mean_score_diff"] <= MAX_MEAN_SCORE_DIFF
        )
        report[name] = result
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: `run` writes parity/<backend>.npz; `compare` prints the report and exits 1 if any network disagrees."""
    parser = argparse.ArgumentParser(description="ONNX-vs-HEF parity check for a Hailo deploy bundle.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--bundle", type=Path, required=True)
    run.add_argument("--backend", choices=["onnx", "hailo"], required=True)
    check = commands.add_parser("compare")
    check.add_argument("reference", type=Path)
    check.add_argument("candidate", type=Path)
    check.add_argument("--match-iou", type=float, default=MATCH_IOU)
    args = parser.parse_args(argv)

    if args.command == "run":
        if args.backend == "hailo":
            from kitchen.vision.hailo import open_sessions

            sessions = open_sessions(args.bundle)
        else:
            sessions = onnx_sessions(args.bundle)
        frames = np.load(args.bundle / "parity" / "inputs.npy")
        out = args.bundle / "parity" / f"{args.backend}.npz"
        np.savez(out, **run_backend(sessions, frames))
        print(f"wrote {out}")
        return 0

    report = compare(dict(np.load(args.reference)), dict(np.load(args.candidate)), args.match_iou)
    print(json.dumps(report, indent=2))
    return 0 if all(result["agree"] for result in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
