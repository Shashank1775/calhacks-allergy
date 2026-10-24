"""The one clip builder: a pair's boxes and change masks over time to an interaction_clip and its [T, C, H, W] tensor.

Owner: platform (agents)
Step:  shared
Reads: ClipStep sequences (t_ms, box_a, box_b, other boxes, change mask)
Emits: interaction_clip metadata + tensor
Rules: blind classifier: no object pixels, classes, kinds or allergen data; same builder live, in sim and in partner training
"""

from dataclasses import dataclass, field

import numpy as np

BUILDER_VERSION = "1.0.0"
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
) -> tuple[dict, np.ndarray]:
    """Stack a pair's steps into an interaction_clip record and its uint8 [T, C, H, W] tensor.

    Only box masks, the change mask and timing go in, never object pixels, classes or kinds (safety rule: blind
    classifier), so the model learns interaction rather than appearance.
    """
    if view != "full":
        raise ValueError("only the full-frame view is built")
    if len(steps) < 2:
        raise ValueError("a clip needs at least two steps")

    t_ms = [step.t_ms for step in steps]
    if any(later <= earlier for earlier, later in zip(t_ms, t_ms[1:], strict=False)):
        raise ValueError("step times must strictly increase")
    for step in steps:
        if step.change_mask.shape != (H, W):
            raise ValueError(f"change mask is {step.change_mask.shape}, expected {(H, W)}")

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
