"""Tests for the shared change-mask function and clip builder.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.change_mask, kitchen.shared.clips, contracts/schemas
Emits: nothing
Rules: blind classifier: a built clip is a valid interaction_clip, so it can't carry allergen or object fields
"""

import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from kitchen.shared.change_mask import change_mask
from kitchen.shared.clips import (
    LETTERBOX_PAD,
    ClipStep,
    box_mask,
    build_clip,
    letterbox_box,
    letterbox_frame,
    letterbox_layout,
)

SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"
PARAMS = {"gain": 4.0, "blur_sigma_px": 1.5, "decay": 0.0}


def clip_validator() -> Draft202012Validator:
    """A validator for interaction_clip that resolves references to the other contract schemas."""
    resources = [(p.name, Resource.from_contents(json.loads(p.read_text()))) for p in SCHEMAS.glob("*.schema.json")]
    schema = json.loads((SCHEMAS / "interaction_clip.schema.json").read_text())
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


def square_frame(left: int) -> np.ndarray:
    """A 32x32 gray frame with a white 6x6 square whose left edge is at `left`."""
    frame = np.full((32, 32, 3), 100, dtype=np.uint8)
    frame[10:16, left : left + 6] = 255
    return frame


def test_identical_frames_give_an_empty_mask():
    frame = square_frame(5)
    assert change_mask(frame, frame, None).max() == 0


def test_mask_lights_up_where_something_moved_and_nowhere_else():
    mask = change_mask(square_frame(5), square_frame(15), None)
    assert mask.shape == (32, 32)
    assert 0 <= mask.min() and mask.max() <= 1
    assert mask[12, 7] > 0.5 and mask[12, 18] > 0.5
    assert mask[28, 28] == 0


def test_decay_leaves_a_fading_trail():
    previous = np.ones((32, 32), dtype=np.float32)
    frame = square_frame(5)
    assert change_mask(frame, frame, previous, decay=0.5).min() == pytest.approx(0.5)


def test_box_mask_fills_the_box_and_a_missed_box_is_empty():
    mask = box_mask((0.25, 0.5, 0.5, 0.75), 8, 8)
    assert mask.sum() == 4 and mask[4, 2] == 1 and mask[0, 0] == 0
    assert box_mask(None, 8, 8).sum() == 0


def make_steps(count: int = 3) -> list[ClipStep]:
    """A few steps with A moving toward B."""
    return [
        ClipStep(100 * i, (0.1 * i, 0.1, 0.1 * i + 0.2, 0.3), (0.5, 0.5, 0.8, 0.8), np.zeros((16, 16)))
        for i in range(count)
    ]


def test_build_clip_stacks_steps_into_the_contract_shape():
    record, tensor = build_clip(
        "tool-sim-a", "bin-sim-b", make_steps(), clip_id="c1", source="sim", tensor_file="c1.npz",
        change_mask_params=PARAMS, H=16, W=16,
    )  # fmt: skip
    assert tensor.shape == (3, 3, 16, 16) and tensor.dtype == np.uint8
    assert record["dt_ms"] == [0, 100, 100]
    assert record["shape"] == {"T": 3, "C": 3, "H": 16, "W": 16}
    assert tensor[0, 1, 10, 10] == 255 and tensor[0, 1, 0, 0] == 0


@pytest.mark.safety
def test_built_clip_is_a_valid_interaction_clip():
    """Blind classifier: the record validates, and the schema rejects any allergen or object field."""
    labels = {"contact": True, "transferred": False, "action": "transfer.contact", "label_source": "sim_truth"}
    record, _ = build_clip(
        "tool-sim-a", "bin-sim-b", make_steps(), clip_id="c1", source="sim", tensor_file="c1.npz",
        change_mask_params=PARAMS, H=16, W=16, generator={"version": "1", "seed": 1}, labels=labels,
    )  # fmt: skip
    validator = clip_validator()
    validator.validate(record)
    assert not validator.is_valid({**record, "object_class": "spoon"})


def test_build_clip_rejects_times_that_do_not_move_forward():
    steps = make_steps()
    steps[2] = ClipStep(100, steps[2].box_a, steps[2].box_b, steps[2].change_mask)
    with pytest.raises(ValueError):
        build_clip("tool-sim-a", "bin-sim-b", steps, clip_id="c", source="sim", tensor_file="c.npz",
                   change_mask_params=PARAMS, H=16, W=16)  # fmt: skip


# Exact values: each of these fails if the formula it pins changes.


def test_gain_scales_the_largest_channel_change_exactly():
    """Max over R, G, B of |change| / 255, times gain, clipped at 1 (no blur)."""
    before = np.zeros((4, 4, 3), dtype=np.uint8)
    after = before.copy()
    after[1, 1] = (0, 51, 10)  # only green moves by much: 51 / 255 = 0.2
    mask = change_mask(before, after, None, gain=4.0, blur_sigma_px=0.0, decay=0.0)
    assert mask[1, 1] == pytest.approx(0.8)
    assert mask.sum() == pytest.approx(0.8)
    clipped = change_mask(before, after, None, gain=10.0, blur_sigma_px=0.0, decay=0.0)
    assert clipped[1, 1] == 1.0


def test_blur_spreads_a_change_but_keeps_its_total():
    """A Gaussian blur moves brightness to the neighbors without adding or losing any (away from the edges)."""
    before = np.zeros((21, 21, 3), dtype=np.uint8)
    after = before.copy()
    after[10, 10] = 51
    mask = change_mask(before, after, None, gain=1.0, blur_sigma_px=1.5, decay=0.0)
    assert mask.sum() == pytest.approx(0.2, rel=1e-5)
    assert mask[10, 10] == pytest.approx(0.2 * (1 / (2 * np.pi * 1.5**2)), rel=0.02)
    assert mask[10, 11] < mask[10, 10] and mask[10, 11] == pytest.approx(mask[11, 10])


def test_decay_keeps_the_brighter_of_new_change_and_faded_trail():
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    previous = np.full((4, 4), 0.6, dtype=np.float32)
    mask = change_mask(frame, frame, previous, gain=4.0, blur_sigma_px=0.0, decay=0.5)
    assert np.allclose(mask, 0.3)


def test_box_mask_rounds_outward_to_whole_pixels():
    """Edges that fall inside a pixel take the whole pixel: 3.1 -> 3 on the left, 5.8 -> 6 on the right."""
    mask = box_mask((0.31, 0.31, 0.58, 0.58), 10, 10)
    assert mask[3:6, 3:6].all() and mask.sum() == 9


def test_change_channel_is_scaled_to_0_255_and_rounded():
    steps = make_steps(2)
    half = np.full((16, 16), 0.5, dtype=np.float32)
    steps = [ClipStep(s.t_ms, s.box_a, s.box_b, half) for s in steps]
    _, tensor = build_clip("tool-sim-a", "bin-sim-b", steps, clip_id="c", source="sim", tensor_file="c.npz",
                           change_mask_params=PARAMS, H=16, W=16)  # fmt: skip
    assert (tensor[:, 2] == 128).all()


def test_others_channel_is_the_union_of_other_boxes():
    step = ClipStep(0, None, None, np.zeros((8, 8)), [(0.0, 0.0, 0.25, 0.25), (0.5, 0.5, 0.75, 0.75)])
    later = ClipStep(1, None, None, np.zeros((8, 8)))
    _, tensor = build_clip("tool-sim-a", "bin-sim-b", [step, later], clip_id="c", source="sim", tensor_file="c.npz",
                           change_mask_params=PARAMS, H=8, W=8, channels=("box_a", "box_b", "change", "others"))  # fmt: skip
    assert tensor[0, 3].sum() == 8 * 255 and tensor[1, 3].sum() == 0


def test_uint8_masks_are_rejected():
    """A stored mask must be divided by 255 first, or the builder would clip it to 0 or 255."""
    steps = [ClipStep(s.t_ms, s.box_a, s.box_b, np.full((16, 16), 40, dtype=np.uint8)) for s in make_steps(2)]
    with pytest.raises(ValueError, match="divide"):
        build_clip("tool-sim-a", "bin-sim-b", steps, clip_id="c", source="sim", tensor_file="c.npz",
                   change_mask_params=PARAMS, H=16, W=16)  # fmt: skip


# Letterboxing: frames and boxes from a wide camera frame land on the same clip pixels.


def test_letterbox_frame_shrinks_by_area_and_pads_the_short_side():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:, :640] = 200  # left half bright
    square = letterbox_frame(frame, 128)
    assert square.shape == (128, 128, 3)
    assert (square[:28] == LETTERBOX_PAD).all() and (square[100:] == LETTERBOX_PAD).all()  # 72 content rows, centered
    assert (square[28:100, :64] == 200).all() and (square[28:100, 64:] == 0).all()


def test_wide_frame_box_and_change_line_up_after_letterboxing():
    """A bright square moves inside a 1280x720 frame; its box must cover the same clip pixels as its change."""
    before = np.zeros((720, 1280, 3), dtype=np.uint8)
    after = before.copy()
    after[400:500, 600:700] = 255  # something appears here
    box = (600 / 1280, 400 / 720, 700 / 1280, 500 / 720)  # its box, normalized to the camera frame

    mask = change_mask(letterbox_frame(before, 128), letterbox_frame(after, 128), None, blur_sigma_px=0.0)
    steps = [ClipStep(0, box, None, np.zeros((128, 128), np.float32)), ClipStep(60, box, None, mask)]
    _, tensor = build_clip("tool-sim-a", "bin-sim-b", steps, clip_id="c", source="live", tensor_file="c.npz",
                           change_mask_params=PARAMS, source_size=(1280, 720))  # fmt: skip

    box_pixels = tensor[1, 0] > 0
    change_pixels = tensor[1, 2] > 0
    assert change_pixels.any()
    assert (change_pixels <= box_pixels).all()  # every changed pixel is inside the letterboxed box


def test_without_source_size_a_wide_frame_box_misses_its_change():
    """The bug this guards against: drawing a 16:9 box straight onto the square puts it on the wrong rows."""
    box = (600 / 1280, 400 / 720, 700 / 1280, 500 / 720)
    moved = letterbox_box(box, 1280, 720, 128)
    assert moved[1] > box[1] - 0.2 and moved[1] != pytest.approx(box[1])
    assert letterbox_layout(1280, 720, 128) == (128, 72, 0, 28)


def test_tall_frame_boxes_get_left_and_right_padding():
    """A portrait frame pads the sides instead, so the box shifts right by the left padding."""
    assert letterbox_layout(720, 1280, 128) == (72, 128, 28, 0)
    assert letterbox_box((0.0, 0.0, 1.0, 1.0), 720, 1280, 128) == pytest.approx((28 / 128, 0.0, 100 / 128, 1.0))
    assert letterbox_box((0.5, 0.25, 0.75, 0.5), 720, 1280, 128) == pytest.approx((64 / 128, 0.25, 82 / 128, 0.5))
