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
from kitchen.shared.clips import ClipStep, box_mask, build_clip

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
