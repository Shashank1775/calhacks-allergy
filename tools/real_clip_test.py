"""Real-clip test: turn a labeled kitchen video into interaction clips with the shared builder, and score the LSTM.

Owner: platform (agents)
Step:  ConvLSTM evaluation on real footage (how to record and label: docs/real-clip-test.md)
Reads: a labels CSV and the videos it names; the stock YOLOv8s detection and pose ONNX bundle; an LSTM checkpoint
Emits: <out>/clips/<event>.npz and clips.jsonl, predictions.csv, summary.json, previews/<event>.png
Rules: frames go through the same letterbox, change mask and clip builder the model trained on; boxes come from the
       detector, never from the labels (except a container box a person types in when the detector can't find it);
       the labels only judge the model; the contact head is judged by the clip contract's contact (touched, or a
       hover: an overhead camera can't tell resting over the food from touching it)

Run from the repo root (needs the ml extra; torch only to score):
    uv run --all-extras --with torch python tools/real_clip_test.py --labels labels.csv --out data/real_clips/take1
    uv run --all-extras python tools/real_clip_test.py --labels labels.csv --out data/real_clips/take1 --no-score
"""

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Run as a script from anywhere: make the repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitchen.shared.change_mask import DEFAULT_BLUR_SIGMA_PX, DEFAULT_DECAY, DEFAULT_GAIN, change_mask  # noqa: E402
from kitchen.shared.clips import ClipStep, build_clip, letterbox_frame  # noqa: E402
from kitchen.shared.models import validate  # noqa: E402

STEPS = 16  # clip length, as in training: about one second
STEP_MS = 62.5
TOUCH_STEP = 7  # the labeled moment lands on step 7 of 16, where touches land in the sim clips
CLIP_SIZE = 128
CHANGE_MASK_PARAMS = {"gain": DEFAULT_GAIN, "blur_sigma_px": DEFAULT_BLUR_SIGMA_PX, "decay": DEFAULT_DECAY}
DEFAULT_THRESHOLDS = (0.17, 0.085)  # boxpooled-v1's thresholds that caught 99% of val's real cases

KINDS = ("scoop", "deposit", "wipe", "touch", "hover", "pass_over", "motion_nearby", "approach_leave", "other")
TOOL_CLASSES = {42: "fork", 43: "knife", 44: "spoon"}  # COCO classes the stock detector knows
CONTAINER_CLASSES = {41: "cup", 45: "bowl"}
LEFT_WRIST, RIGHT_WRIST, LEFT_ELBOW, RIGHT_ELBOW = 9, 10, 7, 8  # COCO pose keypoints
MIN_KEYPOINT = 0.3
MAX_JUMP = 0.15  # a tracked box moves at most this fraction of the frame between steps
CONTAINER_HOLD = 3  # a missed container keeps its last box this many steps, as a tracker would

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Label:
    """One row of the labeling sheet: what a person saw happen, and when."""

    event: str
    video: str
    t_s: float
    kind: str
    actor: str
    touched: bool
    moved: bool
    container_box: Box | None
    notes: str


# ---------------------------------------------------------------------------------------------------- labels


def yes_no(value: str, column: str, event: str) -> bool:
    """`y` or `n` (any case) as a bool."""
    answer = value.strip().lower()
    if answer not in ("y", "n"):
        raise ValueError(f"event {event}: {column} must be y or n, got {value!r}")
    return answer == "y"


def read_labels(path: Path) -> list[Label]:
    """Read and check the labeling sheet (columns in tools/real_clip_labels_template.csv)."""
    labels = []
    with open(path, newline="") as sheet:
        for row in csv.DictReader(sheet):
            event = row["event"].strip()
            if not event:
                continue
            kind, actor = row["kind"].strip(), row["actor"].strip()
            if kind not in KINDS:
                raise ValueError(f"event {event}: kind must be one of {', '.join(KINDS)}, got {kind!r}")
            if actor not in ("tool", "hand"):
                raise ValueError(f"event {event}: actor must be tool or hand, got {actor!r}")
            box_text = (row.get("container_box") or "").split()
            box = tuple(float(v) for v in box_text) if box_text else None
            if box is not None and len(box) != 4:
                raise ValueError(f"event {event}: container_box needs 4 numbers, got {row['container_box']!r}")
            touched = yes_no(row["touched"], "touched", event)
            moved = yes_no(row["moved"], "moved", event)
            if moved and not touched:
                raise ValueError(f"event {event}: food can only move between them if they touched")
            labels.append(
                Label(event, row["video"].strip(), float(row["t_s"]), kind, actor, touched, moved, box, row["notes"])
            )
    if len({label.event for label in labels}) != len(labels):
        raise ValueError("event names must be unique")
    return labels


def contact_for(label: Label) -> bool:
    """Contact as interaction_clip defines it: they touched, or the tool came to rest over the food (a hover).

    The sheet records what really happened, so a hover is touched = n. An overhead camera can't see that 1-2 cm, so
    the clips the model trained on call a hover contact, and so does every judgment here (it leans toward HOLD).
    """
    return label.touched or label.kind == "hover"


def action_for(label: Label) -> str:
    """The interaction action the labels describe."""
    if label.moved:
        return "transfer.wipe" if label.kind == "wipe" else "transfer.scoop"
    return "transfer.contact" if contact_for(label) else "unimportant"


# ---------------------------------------------------------------------------------------------------- frames


def frame_indices(t_s: float, fps: float) -> list[int]:
    """The STEPS + 1 video frames of one clip: every `stride`-th frame, with the labeled moment on step TOUCH_STEP.

    A whole-frame stride keeps the gaps even (67 ms at 30 fps), close to the ~62 ms the model trained on; picking the
    nearest frame to each 62.5 ms tick instead would mix 33 and 67 ms gaps.
    """
    stride = max(1, round(fps * STEP_MS / 1000))
    moment = round(t_s * fps)
    return [moment + (k - TOUCH_STEP) * stride for k in range(STEPS + 1)]


def read_frames(video: Path, t_s: float) -> tuple[list[np.ndarray], list[int], tuple[int, int]]:
    """The clip's RGB frames around the labeled moment, their times in ms, and the video's (width, height)."""
    import cv2

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise FileNotFoundError(f"can't open video {video}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps < 1000 / STEP_MS:
        raise ValueError(f"{video.name} is {fps:.0f} fps; clips need at least {1000 / STEP_MS:.0f}")

    indices = frame_indices(t_s, fps)
    if indices[0] < 0 or indices[-1] >= count:
        raise ValueError(f"the clip at {t_s:.2f} s runs past the start or end of {video.name}")
    frames, t_ms = [], []
    for index in indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, bgr = capture.read()
        if not ok:
            raise ValueError(f"can't read frame {index} of {video.name}")
        frames.append(bgr[..., ::-1].copy())
        t_ms.append(round(index * 1000 / fps))
    height, width, _ = frames[0].shape
    return frames, t_ms, (width, height)


# ---------------------------------------------------------------------------------------------------- detection


def detector_input_layout(width: int, height: int, size: int) -> tuple[float, int, int]:
    """How kitchen.vision.bundle.letterbox fits a frame into the detector's square: (scale, left, top)."""
    scale = min(size / height, size / width)
    return scale, (size - round(width * scale)) // 2, (size - round(height * scale)) // 2


def to_source(x: float, y: float, width: int, height: int, size: int) -> tuple[float, float]:
    """A point in the detector's letterboxed input, as a fraction of the source frame."""
    scale, left, top = detector_input_layout(width, height, size)
    return (x - left) / scale / width, (y - top) / scale / height


def box_to_source(row: np.ndarray, width: int, height: int, size: int) -> Box:
    """A detection's x1, y1, x2, y2 in the detector's input, as a box normalized to the source frame."""
    x1, y1 = to_source(row[0], row[1], width, height, size)
    x2, y2 = to_source(row[2], row[3], width, height, size)
    return tuple(float(v) for v in np.clip([x1, y1, x2, y2], 0, 1))


def hand_box(wrist: np.ndarray, elbow: np.ndarray | None, width: int, height: int) -> Box:
    """A hand's box: just past the wrist, away from the elbow, sized by the forearm (both points normalized)."""
    pixels = np.array([width, height])
    wrist_px = wrist * pixels
    if elbow is None:
        forearm, length = np.array([0.0, 1.0]), 0.08 * max(width, height)
    else:
        forearm = wrist_px - elbow * pixels
        length = max(float(np.linalg.norm(forearm)), 1.0)
        forearm = forearm / length
    center = wrist_px + forearm * 0.35 * length
    half = 0.45 * length
    x1, y1 = (center - half) / pixels
    x2, y2 = (center + half) / pixels
    return tuple(float(v) for v in np.clip([x1, y1, x2, y2], 0, 1))


def candidates(det_rows: np.ndarray, pose_rows: np.ndarray, width: int, height: int, size: int) -> dict:
    """Boxes the detector found in one frame, by role: tools, hands and containers."""
    found = {"tool": [], "hand": [], "container": []}
    for row in det_rows:
        role = "tool" if int(row[5]) in TOOL_CLASSES else "container" if int(row[5]) in CONTAINER_CLASSES else None
        if role:
            found[role].append(box_to_source(row, width, height, size))
    for row in pose_rows:
        for wrist_id, elbow_id in ((LEFT_WRIST, LEFT_ELBOW), (RIGHT_WRIST, RIGHT_ELBOW)):
            wx, wy, wc = row[6 + 3 * wrist_id : 9 + 3 * wrist_id]
            if wc < MIN_KEYPOINT:
                continue
            ex, ey, ec = row[6 + 3 * elbow_id : 9 + 3 * elbow_id]
            wrist = np.array(to_source(wx, wy, width, height, size))
            elbow = np.array(to_source(ex, ey, width, height, size)) if ec >= MIN_KEYPOINT else None
            found["hand"].append(hand_box(wrist, elbow, width, height))
    return found


def detect_frames(sessions: dict, frames: list[np.ndarray]) -> list[dict]:
    """Run the stock detection and pose networks on every frame; return each frame's candidate boxes by role."""
    from kitchen.vision.bundle import letterbox
    from kitchen.vision.parity import decode

    found = []
    for rgb in frames:
        height, width, _ = rgb.shape
        rows = {}
        for name, session in sessions.items():
            size = session.get_inputs()[0].shape[2]
            tensor = letterbox(rgb[..., ::-1], size, size).transpose(2, 0, 1)[None].astype(np.float32) / 255
            rows[name] = decode(session.run(None, {session.get_inputs()[0].name: tensor})[0])
        found.append(candidates(rows["det"], rows["pose"], width, height, size))
    return found


# ---------------------------------------------------------------------------------------------------- tracking


def center(box: Box, aspect: float) -> np.ndarray:
    """A box's center, with x stretched by the frame's width/height so distances are in true proportions."""
    return np.array([(box[0] + box[2]) / 2 * aspect, (box[1] + box[3]) / 2])


def nearest(boxes: list[Box], point: np.ndarray, aspect: float, max_distance: float = np.inf) -> Box | None:
    """The box whose center is closest to a point, if it's within max_distance."""
    if not boxes:
        return None
    distances = [float(np.linalg.norm(center(box, aspect) - point)) for box in boxes]
    best = int(np.argmin(distances))
    return boxes[best] if distances[best] <= max_distance else None


def track(per_frame: list[list[Box]], start: int, start_box: Box, aspect: float, hold: int = 0) -> list[Box | None]:
    """Follow one box outward from frame `start`: each frame takes the nearest candidate within MAX_JUMP of the last
    position. A miss gives None, except that `hold` keeps the last box for that many frames (containers stay put)."""
    boxes: list[Box | None] = [None] * len(per_frame)
    boxes[start] = start_box
    for direction in (1, -1):
        last, missed = start_box, 0
        frame = start + direction
        while 0 <= frame < len(per_frame):
            found = nearest(per_frame[frame], center(last, aspect), aspect, MAX_JUMP)
            if found is not None:
                boxes[frame], last, missed = found, found, 0
            elif missed < hold:
                boxes[frame], missed = last, missed + 1
            frame += direction
    return boxes


def pick_pair(found: list[dict], label: Label, aspect: float) -> tuple[list, list] | None:
    """Choose and track A (the labeled tool or hand) and B (the container) through the clip, or None if either is
    nowhere to be found. Both are picked at the labeled moment, or the nearest frame that has candidates."""
    if label.container_box is not None:
        b_boxes = [label.container_box] * len(found)
    else:
        frames = sorted(range(len(found)), key=lambda f: abs(f - TOUCH_STEP))
        start = next((f for f in frames if found[f]["container"]), None)
        if start is None:
            return None
        actors = found[start][label.actor]
        if actors:
            # The container closest to any tool or hand: the one something is interacting with.
            b_start = min(
                found[start]["container"],
                key=lambda b: min(np.linalg.norm(center(b, aspect) - center(a, aspect)) for a in actors),
            )
        else:
            b_start = max(found[start]["container"], key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        b_boxes = track([f["container"] for f in found], start, b_start, aspect, CONTAINER_HOLD)

    frames = sorted(range(len(found)), key=lambda f: abs(f - TOUCH_STEP))
    start = next((f for f in frames if found[f][label.actor] and b_boxes[f] is not None), None)
    if start is None:
        return None
    a_start = nearest(found[start][label.actor], center(b_boxes[start], aspect), aspect)
    a_boxes = track([f[label.actor] for f in found], start, a_start, aspect)
    return a_boxes, b_boxes


# ---------------------------------------------------------------------------------------------------- clips


def event_clip(label: Label, frames: list, t_ms: list, size: tuple[int, int], a_boxes: list, b_boxes: list):
    """Build one event's interaction_clip record and uint8 [T, C, H, W] tensor with the shared code."""
    small = [letterbox_frame(frame, CLIP_SIZE) for frame in frames]
    steps, mask = [], None
    for f in range(1, len(frames)):
        mask = change_mask(small[f - 1], small[f], mask, **CHANGE_MASK_PARAMS)
        steps.append(ClipStep(t_ms[f], a_boxes[f], b_boxes[f], mask))
    labels = {
        "contact": contact_for(label),
        "transferred": label.moved,
        "action": action_for(label),
        "label_source": "human",
    }
    record, tensor = build_clip(
        "tool-real-a" if label.actor == "tool" else "hand-p1-right",
        "bin-real-b",
        steps,
        clip_id=f"real-{label.event}",
        source="recording",
        tensor_file=f"clips/{label.event}.npz",
        change_mask_params=CHANGE_MASK_PARAMS,
        labels=labels,
        source_size=size,
    )
    return validate(record, "interaction_clip"), tensor


def overlap_rule(tensor: np.ndarray) -> tuple[bool, bool]:
    """A plain baseline: contact if the boxes overlap on 2+ steps; moved if, after they first overlap, the change
    inside B but outside A averages above 0.1. Not the partner's zone rules, just a floor the LSTM should beat."""
    a, b, change = tensor[:, 0] > 0, tensor[:, 1] > 0, tensor[:, 2] / 255
    overlapping = [bool((a[k] & b[k]).any()) for k in range(len(tensor))]
    contact = sum(overlapping) >= 2
    if not contact:
        return False, False
    first = overlapping.index(True)
    region = b[first:] & ~a[first:]
    moved = bool(region.any()) and float(change[first:][region].mean()) > 0.1
    return contact, moved


# ---------------------------------------------------------------------------------------------------- scoring


def score_clips(weights: Path, tensors: list[np.ndarray], dt_ms: list[list[int]]) -> np.ndarray:
    """P(contact) and P(moved) for every clip, from a checkpoint made by kitchen/lstm/train.py."""
    import torch

    from kitchen.lstm import model as models

    checkpoint = torch.load(weights, map_location="cpu", weights_only=False)
    model = getattr(models, checkpoint["args"].get("model", "BoxPooledInteractionNet"))()
    model.load_state_dict(checkpoint["model"])
    model.eval()
    x = torch.from_numpy(np.stack(tensors)).float() / 255
    dt = torch.tensor(dt_ms, dtype=torch.float32)
    with torch.no_grad():
        return torch.sigmoid(model(x, dt)).numpy()


def rates(called: np.ndarray, truth: np.ndarray) -> dict:
    """Accuracy, recall (real cases caught) and false-alarm rate of yes/no calls."""
    return {
        "accuracy": float((called == truth).mean()),
        "recall": float(called[truth].mean()) if truth.any() else None,
        "false_alarm": float(called[~truth].mean()) if (~truth).any() else None,
    }


def summarize(rows: list[dict], thresholds: tuple[float, float]) -> dict:
    """Overall and per-kind results for the LSTM at the thresholds and at 0.5, and for the box-overlap rule."""
    scored = [row for row in rows if row["status"] == "scored"]
    summary = {
        "events": len(rows),
        "scored": len(scored),
        "thresholds": dict(zip(("contact", "moved"), thresholds, strict=True)),
    }
    if not scored:
        return summary
    kinds = np.array([row["kind"] for row in scored])
    for head, column in (("contact", 0), ("moved", 1)):
        truth = np.array([row[head] for row in scored])
        probability = np.array([row[f"p_{head}"] for row in scored])
        rule = np.array([row[f"rule_{head}"] for row in scored])
        summary[head] = {
            "lstm_at_thresholds": rates(probability >= thresholds[column], truth),
            "lstm_at_0.5": rates(probability >= 0.5, truth),
            "box_overlap_rule": rates(rule, truth),
            "lstm_accuracy_by_kind": {
                kind: round(float(((probability >= thresholds[column]) == truth)[kinds == kind].mean()), 3)
                for kind in sorted(set(kinds))
            },
        }
    return summary


# ---------------------------------------------------------------------------------------------------- previews


def draw_preview(path: Path, frames: list, tensor: np.ndarray | None, a_boxes: list, b_boxes: list, found: list):
    """Save the labeled moment large (with a 0-1 grid for typing a container_box), then every step small with its
    boxes, then the change masks: enough to see at a glance whether the right things were tracked."""
    import cv2

    def draw(image, box, color, thickness):
        if box is not None:
            h, w = image.shape[:2]
            cv2.rectangle(
                image, (int(box[0] * w), int(box[1] * h)), (int(box[2] * w), int(box[3] * h)), color, thickness
            )

    def bgr(rgb):
        return np.ascontiguousarray(rgb[..., ::-1])

    big = cv2.resize(bgr(frames[TOUCH_STEP]), (960, round(960 * frames[0].shape[0] / frames[0].shape[1])))
    for i in range(1, 10):
        x, y = int(big.shape[1] * i / 10), int(big.shape[0] * i / 10)
        cv2.line(big, (x, 0), (x, big.shape[0]), (200, 200, 200), 1)
        cv2.line(big, (0, y), (big.shape[1], y), (200, 200, 200), 1)
        cv2.putText(big, f"{i / 10:.1f}", (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        cv2.putText(big, f"{i / 10:.1f}", (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    for role, color in (("tool", (160, 160, 160)), ("hand", (160, 160, 160)), ("container", (230, 200, 120))):
        for box in found[TOUCH_STEP][role]:
            draw(big, box, color, 1)
    draw(big, b_boxes[TOUCH_STEP] if b_boxes else None, (255, 80, 0), 3)
    draw(big, a_boxes[TOUCH_STEP] if a_boxes else None, (0, 0, 255), 3)

    # Every step small, in two rows of eight, each followed by its change masks.
    cell = big.shape[1] // 8
    strip_h = round(cell * frames[0].shape[0] / frames[0].shape[1])
    steps = []
    for f in range(1, len(frames)):
        small = cv2.resize(bgr(frames[f]), (cell, strip_h))
        draw(small, b_boxes[f] if b_boxes else None, (255, 80, 0), 2)
        draw(small, a_boxes[f] if a_boxes else None, (0, 0, 255), 2)
        steps.append(small)
    rows = [big]
    for half in (slice(0, 8), slice(8, 16)):
        rows.append(np.concatenate(steps[half], axis=1))
        if tensor is not None:
            masks = [cv2.cvtColor(cv2.resize(tensor[k, 2], (cell, cell)), cv2.COLOR_GRAY2BGR) for k in range(STEPS)]
            rows.append(np.concatenate(masks[half], axis=1))
    width = max(row.shape[1] for row in rows)
    rows = [np.pad(row, ((0, 0), (0, width - row.shape[1]), (0, 0))) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.concatenate(rows, axis=0))


# ---------------------------------------------------------------------------------------------------- main


def process_event(label: Label, videos_dir: Path, sessions: dict, out: Path) -> tuple[dict, np.ndarray | None, list]:
    """Read, detect, track, build and preview one event; return its predictions row (unscored), tensor and dt_ms."""
    frames, t_ms, size = read_frames(videos_dir / label.video, label.t_s)
    found = detect_frames(sessions, frames)
    aspect = size[0] / size[1]
    pair = pick_pair(found, label, aspect)
    row = {
        "event": label.event,
        "kind": label.kind,
        "actor": label.actor,
        "touched": label.touched,
        "contact": contact_for(label),
        "moved": label.moved,
    }
    if pair is None:
        missing = "container" if label.container_box is None and not any(f["container"] for f in found) else label.actor
        draw_preview(out / "previews" / f"{label.event}.png", frames, None, [], [], found)
        return {**row, "status": f"skipped: detector found no {missing}"}, None, []

    a_boxes, b_boxes = pair
    record, tensor = event_clip(label, frames, t_ms, size, a_boxes, b_boxes)
    np.savez_compressed(out / record["tensor"]["file"], x=tensor, dt_ms=np.array(record["dt_ms"], dtype=np.int32))
    with open(out / "clips.jsonl", "a") as clips:
        clips.write(json.dumps(record) + "\n")
    draw_preview(out / "previews" / f"{label.event}.png", frames, tensor, a_boxes, b_boxes, found)
    rule_contact, rule_moved = overlap_rule(tensor)
    row.update(
        status="scored",
        a_steps=sum(box is not None for box in a_boxes[1:]),
        b_steps=sum(box is not None for box in b_boxes[1:]),
        rule_contact=rule_contact,
        rule_moved=rule_moved,
    )
    return row, tensor, record["dt_ms"]


def main() -> None:
    """Build clips for every labeled event, score them, and write predictions, a summary and previews."""
    parser = argparse.ArgumentParser(description="Score the interaction LSTM on labeled real footage.")
    parser.add_argument("--labels", type=Path, required=True, help="the filled-in labeling sheet")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, default=Path("data/models/stock-yolov8s"))
    parser.add_argument("--weights", type=Path, default=Path("data/models/lstm/boxpooled-v1/best.pt"))
    parser.add_argument("--thresholds", type=float, nargs=2, default=None, help="contact and moved thresholds")
    parser.add_argument("--no-score", action="store_true", help="only build clips and previews (no torch needed)")
    args = parser.parse_args()

    from kitchen.vision.parity import onnx_sessions

    if (args.out / "clips.jsonl").exists():
        raise SystemExit(f"{args.out} already has results; pick a new --out")
    (args.out / "clips").mkdir(parents=True, exist_ok=True)
    labels = read_labels(args.labels)
    sessions = onnx_sessions(args.bundle)

    rows, tensors, dts = [], [], []
    for label in labels:
        row, tensor, dt_ms = process_event(label, args.labels.parent, sessions, args.out)
        rows.append(row)
        if tensor is not None:
            tensors.append(tensor)
            dts.append(dt_ms)
        print(f"{label.event:>8}  {label.kind:<15} {row['status']}", flush=True)

    thresholds = tuple(args.thresholds) if args.thresholds else DEFAULT_THRESHOLDS
    if tensors and not args.no_score:
        probabilities = iter(score_clips(args.weights, tensors, dts))
        for row in rows:
            if row["status"] == "scored":
                row["p_contact"], row["p_moved"] = (round(float(p), 4) for p in next(probabilities))
                row["call_contact"] = row["p_contact"] >= thresholds[0]
                row["call_moved"] = row["p_moved"] >= thresholds[1]

    columns = list(dict.fromkeys(key for row in rows for key in row))
    with open(args.out / "predictions.csv", "w", newline="") as sheet:
        writer = csv.DictWriter(sheet, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    if args.no_score:
        print(f"built {len(tensors)} clips; previews in {args.out / 'previews'}")
        return

    summary = summarize(rows, thresholds)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
