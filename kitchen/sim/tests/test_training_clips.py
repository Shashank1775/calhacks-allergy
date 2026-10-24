"""Tests for simulated episodes and ConvLSTM training clips.

Owner: sim (agents)
Step:  simulator
Reads: kitchen.sim.episodes, kitchen.sim.training_clips, contracts/schemas
Emits: nothing
Rules: blind classifier (every clip is a valid interaction_clip); labels from the episode; deterministic per seed;
       no seed or layout family in two splits
"""

import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from kitchen.sim.episodes import KINDS, PORTION_RADIUS, RIM_WIDTH, STEPS, simulate_episode
from kitchen.sim.training_clips import SPLIT_SEED_OFFSET, TEST_FAMILIES, TRAIN_FAMILIES, make_clip, write_split

SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"


def clip_validator() -> Draft202012Validator:
    """A validator for interaction_clip that resolves references to the other contract schemas."""
    resources = [(p.name, Resource.from_contents(json.loads(p.read_text()))) for p in SCHEMAS.glob("*.schema.json")]
    schema = json.loads((SCHEMAS / "interaction_clip.schema.json").read_text())
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


def test_kind_shares_add_up_to_one():
    assert sum(kind.share for kind in KINDS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("kind", list(KINDS))
def test_every_kind_gives_a_labeled_clip_of_the_right_shape(kind):
    record, tensor, episode = make_clip(seed=7, family=0, tensor_file="x.npz", kind=kind)
    truth = KINDS[kind]
    assert tensor.shape == (STEPS, 3, 128, 128) and tensor.dtype == np.uint8
    assert record["labels"]["contact"] == truth.contact
    assert record["labels"]["transferred"] == truth.transferred
    assert record["labels"]["action"] == truth.action
    assert record["labels"]["transferred"] <= record["labels"]["contact"]  # material only moves on contact
    assert episode.kind == kind


@pytest.mark.safety
def test_every_clip_is_a_valid_interaction_clip():
    """Blind classifier: clips carry only boxes, the change mask and timing; the schema rejects anything else."""
    validator = clip_validator()
    for seed, kind in enumerate(KINDS):
        record, _, _ = make_clip(seed=seed, family=seed % 6, tensor_file="x.npz", kind=kind)
        validator.validate(record)


def test_same_seed_gives_the_same_clip():
    first_record, first_tensor, _ = make_clip(seed=42, family=3, tensor_file="x.npz")
    second_record, second_tensor, _ = make_clip(seed=42, family=3, tensor_file="x.npz")
    assert first_record == second_record
    assert np.array_equal(first_tensor, second_tensor)


def test_transfers_change_the_container_and_hover_does_not():
    """The model can only learn 'material moved' if the food really moves in transfer clips and stays put otherwise."""
    for kind, should_move in (("wipe", True), ("hover", False), ("pass_over", False), ("touch", False)):
        episode = simulate_episode(seed=3, family=0, kind=kind)
        b_food = [
            blob for blob in episode.food if np.linalg.norm(blob.position[0] - episode.containers[0].center[0]) < 0.2
        ]
        b_center = episode.containers[0].center
        # Relative to B, so a touch that nudges the whole container doesn't count as food moving.
        moved = max(
            np.linalg.norm((blob.position[-1] - b_center[-1]) - (blob.position[0] - b_center[0])) for blob in b_food
        )
        assert (moved > 1e-3) == should_move, kind


def inside_body(container, point, f):
    """True if a point is inside a container's body (inside the rim) at frame f."""
    offset = np.abs(point - container.center[f]) / (container.half_size - RIM_WIDTH)
    return (offset**2).sum() <= 1.0 if container.is_round else offset.max() <= 1.0


def food_moved_in_b(episode):
    """How far the food in B moved relative to B over the episode."""
    b = episode.containers[0].center
    in_b = [blob for blob in episode.food if np.linalg.norm(blob.position[0] - b[0]) < 0.2]
    return max(np.linalg.norm((blob.position[-1] - b[-1]) - (blob.position[0] - b[0])) for blob in in_b)


@pytest.mark.parametrize("seed", range(40))
def test_scoops_and_deposits_move_a_visible_portion_inside_b(seed):
    """v2: the moved portion is spoonful-sized, and it starts (scoop) or lands (deposit) inside B's body."""
    scoop = simulate_episode(seed=seed, family=0, kind="scoop")
    portion = next(blob for blob in scoop.food if blob.on_actor.any())
    assert portion.radius >= PORTION_RADIUS[0]
    assert inside_body(scoop.containers[0], portion.position[0], 0)
    assert np.linalg.norm(portion.position[-1] - portion.position[0]) > 2 * portion.radius  # it left its spot

    deposit = simulate_episode(seed=seed, family=0, kind="deposit")
    dropped = next(blob for blob in deposit.food if blob.on_actor.any())
    drop = int(np.argmin(dropped.on_actor))  # the first frame the portion is off A
    assert dropped.radius >= PORTION_RADIUS[0]
    assert inside_body(deposit.containers[0], dropped.position[drop], drop)


def test_the_v2_label_fixes_hold_over_many_seeds():
    """Wipes always smear food; some touches land inside B; a nudge never moves B before A arrives; hover is contact."""
    assert all(food_moved_in_b(simulate_episode(seed, 0, "wipe")) > 1e-3 for seed in range(100))
    touches = [simulate_episode(seed, 0, "touch") for seed in range(40)]
    tips_inside = [
        inside_body(t.containers[0], t.actor.tip[len(t.times_ms) // 2], len(t.times_ms) // 2) for t in touches
    ]
    assert 0 < sum(tips_inside) < len(touches)
    for touch in touches:
        contact_frame = int(np.searchsorted(touch.times_ms, touch.labels["contact_t_ms"]))
        assert np.allclose(touch.containers[0].center[contact_frame], touch.containers[0].center[0])
    assert (KINDS["hover"].contact, KINDS["hover"].transferred) == (True, False)


def test_splits_use_disjoint_seeds_and_test_uses_unseen_layouts(tmp_path):
    stats = {split: write_split(tmp_path, split, 2, seed_base=0, workers=1) for split in SPLIT_SEED_OFFSET}
    ranges = [range(s["seeds"][0], s["seeds"][1] + 1) for s in stats.values()]
    assert not set(ranges[0]) & set(ranges[1]) and not set(ranges[1]) & set(ranges[2])
    assert not set(ranges[0]) & set(ranges[2])
    assert not set(TRAIN_FAMILIES) & set(TEST_FAMILIES)
    lines = (tmp_path / "train" / "clips.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    saved = np.load(tmp_path / first["tensor"]["file"])
    assert saved["x"].shape == (STEPS, 3, 128, 128)
    assert list(saved["dt_ms"]) == first["dt_ms"]
