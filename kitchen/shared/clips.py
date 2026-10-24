"""The one clip builder: a pair's boxes and change masks over time to an interaction_clip and its [T, C, H, W] tensor.

Owner: platform (agents)
Step:  shared
Reads: camera frames (letterbox_frame), ClipStep sequences (t_ms, box_a, box_b, change_mask, other_boxes)
Emits: letterboxed frames for change_mask; interaction_clip metadata + tensor
Rules: blind classifier: no object pixels, classes, kinds or allergen data; same builder live, in sim and in partner training;
       frames and boxes go through the same letterbox, so boxes line up with the change mask

Live order of operations: letterbox each camera frame to the clip size with letterbox_frame, run change_mask on
consecutive letterboxed frames, then build_clip with source_size set to the camera's (width, height) so the
boxes, normalized to the camera frame, get the same letterbox.
"""

from dataclasses import dataclass, field

import numpy as np

BUILDER_VERSION = "1.1.0"
LETTERBOX_PAD = 114  # the gray YOLO letterboxes with; any constant works, since padding never changes between frames
DEFAULT_CHANNELS = ("box_a", "box_b", "change")

# A normalized [x1, y1, x2, y2] box.
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class ClipStep:
    """One time step of a candidate pair: when it was, where both boxes were, the other boxes, and the change mask."""

    t_ms: int
    box_a: Box | None  # None when the detector missed A on this step
    box_b: Box | None
    change_mask: np.ndarray  # float [H, W] in [0, 1], already at clip size
    other_boxes: list[Box] = field(default_factory=list)


def letterbox_layout(width: int, height: int, size: int) -> tuple[int, int, int, int]:
    """Where a width x height frame lands inside a size x size square: (content width, content height, left, top)."""
    scale = size / max(width, height)
    content_width, content_height = max(1, round(width * scale)), max(1, round(height * scale))
    return content_width, content_height, (size - content_width) // 2, (size - content_height) // 2


def letterbox_frame(rgb: np.ndarray, size: int) -> np.ndarray:
    """Shrink a uint8 RGB frame into a size x size square, averaging each output pixel's area, and pad the short side.

    Area averaging (not picking pixels) keeps camera noise low, which matters because change_mask's gain and blur are
    tuned for frames at clip size.
    """
    height, width, _ = rgb.shape
    if max(height, width) < size:
        raise ValueError(f"frame {width}x{height} is smaller than the {size}px clip")

    content_width, content_height, left, top = letterbox_layout(width, height, size)
    rows = np.floor(np.arange(content_height) * height / content_height).astype(int)
    cols = np.floor(np.arange(content_width) * width / content_width).astype(int)
    summed = np.add.reduceat(np.add.reduceat(rgb.astype(np.float64), rows, axis=0), cols, axis=1)
    counts = np.diff(np.append(rows, height))[:, None] * np.diff(np.append(cols, width))[None, :]

    square = np.full((size, size, 3), LETTERBOX_PAD, dtype=np.float64)
    square[top : top + content_height, left : left + content_width] = summed / counts[..., None]
    return np.round(square).astype(np.uint8)


def letterbox_box(box: Box, width: int, height: int, size: int) -> Box:
    """Move a box normalized to a width x height frame onto that frame's letterbox, normalized to the size x size clip."""
    content_width, content_height, left, top = letterbox_layout(width, height, size)
    x1, y1, x2, y2 = box
    return (
        (left + x1 * content_width) / size,
        (top + y1 * content_height) / size,
        (left + x2 * content_width) / size,
        (top + y2 * content_height) / size,
    )


def box_mask(box: Box | None, height: int, width: int) -> np.ndarray:
    """Return a uint8 [H, W] mask that is 1 inside a normalized box and 0 elsewhere; all zeros for a missed box."""
    mask = np.zeros((height, width), dtype=np.uint8)
    if box is None:
        return mask

    x1, y1, x2, y2 = np.clip(box, 0.0, 1.0)
    left, right = int(np.floor(x1 * width)), int(np.ceil(x2 * width))
    top, bottom = int(np.floor(y1 * height)), int(np.ceil(y2 * height))
    mask[top:bottom, left:right] = 1
    return mask


def letterbox_step(step: ClipStep, width: int, height: int, size: int) -> ClipStep:
    """The same step with every box moved onto the letterboxed clip frame."""

    def move(box: Box | None) -> Box | None:
        return None if box is None else letterbox_box(box, width, height, size)

    return ClipStep(
        step.t_ms, move(step.box_a), move(step.box_b), step.change_mask, [move(b) for b in step.other_boxes]
    )


def step_planes(step: ClipStep, channels: tuple[str, ...], height: int, width: int) -> np.ndarray:
    """Draw one step as a uint8 [C, H, W] stack, 0 to 255, in the requested channel order."""
    planes = []
    for name in channels:
        if name == "box_a":
            planes.append(box_mask(step.box_a, height, width) * 255)
        elif name == "box_b":
            planes.append(box_mask(step.box_b, height, width) * 255)
        elif name == "change":
            planes.append(np.round(np.clip(step.change_mask, 0.0, 1.0) * 255))
        elif name == "others":
            union = np.zeros((height, width), dtype=np.uint8)
            for box in step.other_boxes:
                union |= box_mask(box, height, width)
            planes.append(union * 255)
        else:
            raise ValueError(f"unknown channel {name!r}")
    return np.stack(planes).astype(np.uint8)


def build_clip(
    a_id: str,
    b_id: str,
    steps: list[ClipStep],
    *,
    clip_id: str,
    source: str,
    tensor_file: str,
    change_mask_params: dict,
    H: int = 128,  # noqa: N803 (named as in the clip contract)
    W: int = 128,  # noqa: N803
    view: str = "full",
    channels: tuple[str, ...] = DEFAULT_CHANNELS,
    generator: dict | None = None,
    labels: dict | None = None,
    source_size: tuple[int, int] | None = None,
) -> tuple[dict, np.ndarray]:
    """Stack a pair's steps into an interaction_clip record and its uint8 [T, C, H, W] tensor.

    Only box masks, the change mask and timing go in, never object pixels, classes or kinds (safety rule: blind
    classifier), so the model learns interaction rather than appearance.

    source_size is the (width, height) of the camera frame the boxes are normalized to. Give it whenever that frame
    isn't the clip's square (live video): the boxes then get the same letterbox as the frames, so they line up with the
    change mask. Leave it None when boxes already match the clip frame (sim renders square frames).
    """
    if view != "full":
        raise ValueError("only the full-frame view is built")
    if len(steps) < 2:
        raise ValueError("a clip needs at least two steps")
    if source_size is not None:
        if H != W:
            raise ValueError("letterboxed clips are square")
        steps = [letterbox_step(step, *source_size, H) for step in steps]

    t_ms = [step.t_ms for step in steps]
    if any(later <= earlier for earlier, later in zip(t_ms, t_ms[1:], strict=False)):
        raise ValueError("step times must strictly increase")
    for step in steps:
        if step.change_mask.shape != (H, W):
            raise ValueError(f"change mask is {step.change_mask.shape}, expected {(H, W)}")
        if not np.issubdtype(step.change_mask.dtype, np.floating):
            # A stored uint8 mask (change.npz) would be clipped to 0 or 1 here; it must be divided by 255 first.
            raise ValueError(
                f"change masks are floats in [0, 1], got {step.change_mask.dtype}; divide stored masks by 255"
            )

    tensor = np.stack([step_planes(step, channels, H, W) for step in steps])
    dt_ms = [0] + [later - earlier for earlier, later in zip(t_ms, t_ms[1:], strict=False)]

    record = {
        "clip_id": clip_id,
        "source": source,
        "builder_version": BUILDER_VERSION,
        "a_id": a_id,
        "b_id": b_id,
        "t_ms": t_ms,
        "dt_ms": dt_ms,
        "shape": {"T": len(steps), "C": len(channels), "H": H, "W": W},
        "channels": list(channels),
        "view": view,
        "change_mask": dict(change_mask_params),
        "tensor": {"file": tensor_file, "key": "x", "dtype": "uint8"},
    }
    if generator is not None:
        record["generator"] = generator
    if labels is not None:
        record["labels"] = labels
    return record, tensor
