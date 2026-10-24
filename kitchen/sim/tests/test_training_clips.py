"""Tests for simulated episodes and ConvLSTM training clips.

Owner: sim (agents)
Step:  simulator
Reads: kitchen.sim.episodes, kitchen.sim.training_clips, contracts/schemas
Emits: nothing
Rules: blind classifier (every clip is a valid interaction_clip); labels from the episode; deterministic per seed;
       no seed in two splits; test uses layout families that train and val never see
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
    if truth.contact:  # contact kinds never get a lighting tag, so the tag is exactly the kind's
        assert record["labels"].get("hard_negative") == truth.hard_negative
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
        moved = food_moved_in_b(episode)  # relative to B, so a nudged container doesn't count
        assert (moved > 1e-3) == should_move, kind


def inside_body(container, point, f, margin=0.0):
    """True if a blob of radius `margin` centered at `point` is inside a container's body (inside the rim) at frame f."""
    body = container.half_size - RIM_WIDTH
    if container.is_round:
        room = body * max(1.0 - margin / body.min(), 1e-3)
        return (((point - container.center[f]) / room) ** 2).sum() <= 1.0 + 1e-6
    return (np.abs(point - container.center[f]) / np.maximum(body - margin, 1e-3)).max() <= 1.0 + 1e-6


def food_moved_in_b(episode):
    """How far the food in B moved relative to B over the episode."""
    b = episode.containers[0].center
    in_b = [blob for blob in episode.food if np.linalg.norm(blob.position[0] - b[0]) < 0.2 and not blob.on_actor.all()]
    return max(np.linalg.norm((blob.position[-1] - b[-1]) - (blob.position[0] - b[0])) for blob in in_b)


@pytest.mark.parametrize("seed", range(40))
def test_scoops_and_deposits_move_a_visible_portion_inside_b(seed):
    """v2: the moved portion is spoonful-sized, and it starts (scoop) or lands (deposit) inside B's body."""
    scoop = simulate_episode(seed=seed, family=0, kind="scoop")
    portion = next(blob for blob in scoop.food if blob.on_actor.any())
    assert portion.radius >= PORTION_RADIUS[0]
    assert inside_body(scoop.containers[0], portion.position[0], 0, portion.radius)  # the whole spoonful
    assert np.linalg.norm(portion.position[-1] - portion.position[0]) > 2 * portion.radius  # it left its spot

    deposit = simulate_episode(seed=seed, family=0, kind="deposit")
    dropped = next(blob for blob in deposit.food if blob.on_actor.any())
    drop = int(np.argmin(dropped.on_actor))  # the first frame the portion is off A
    assert dropped.radius >= PORTION_RADIUS[0]
    assert inside_body(deposit.containers[0], dropped.position[drop], drop, dropped.radius * dropped.stretch[drop])


def test_the_v2_label_fixes_hold_over_many_seeds():
    """Wipes always smear food; about half of touches land inside B; a nudge never moves B before A arrives."""
    assert all(food_moved_in_b(simulate_episode(seed, 0, "wipe")) > 1e-3 for seed in range(100))
    inside = 0
    for seed in range(200):
        touch = simulate_episode(seed, 0, "touch")
        b = touch.containers[0]
        f = int(np.searchsorted(touch.times_ms, touch.labels["contact_t_ms"]))
        inside += np.abs(touch.actor.tip[f] - b.center[f]).max() < 0.6 * b.half_size.max()
        assert np.allclose(b.center[f], b.center[0])  # the nudge comes after A arrives
    assert 0.35 < inside / 200 < 0.75
    assert (KINDS["hover"].contact, KINDS["hover"].transferred) == (True, False)
    assert KINDS["hover"].hard_negative == KINDS["touch"].hard_negative == "contact_no_motion"


def test_food_on_a_and_food_color_say_nothing_by_themselves():
    """Some non-transfer clips carry food on A the whole time; some deposits are the same color as B's food."""
    loaded = 0
    for seed in range(200):
        episode = simulate_episode(seed, 0, "pass_over")
        loaded += any(blob.on_actor.all() for blob in episode.food)
    assert 0.15 < loaded / 200 < 0.45

    same_color = 0
    for seed in range(100):
        deposit = simulate_episode(seed, 0, "deposit")
        dropped = next(blob for blob in deposit.food if blob.on_actor.any())
        others = [blob.color for blob in deposit.food if blob is not dropped and not blob.on_actor.any()]
        same_color += any(np.allclose(dropped.color, color) for color in others)
    assert 0.1 < same_color / 100 < 0.5


@pytest.mark.parametrize("kind", ["motion_nearby", "wipe"])
def test_pushed_food_stays_in_its_container(kind):
    """Food pushed by a stirring hand or a wipe never leaves B, drawn smear included, so it never ends up under A."""
    for seed in range(60):
        episode = simulate_episode(seed, 0, kind)
        b = episode.containers[0]
        body = (b.half_size - RIM_WIDTH).min()
        in_b = [blob for blob in episode.food if not blob.on_actor.any() and inside_body(b, blob.position[0], 0)]
        for blob in in_b:
            for f in range(len(episode.times_ms)):
                long_axis = blob.radius * blob.stretch[f]
                assert long_axis <= body + 1e-9  # a smear is never longer than the bin is wide
                assert inside_body(b, blob.position[f], f, long_axis)


def test_food_sizes_and_bystanders_say_nothing_by_themselves():
    """Ordinary food spans the portion sizes, and someone else stirring B shows up outside motion_nearby too."""
    from kitchen.sim.episodes import FOOD_RADIUS

    assert FOOD_RADIUS[1] >= PORTION_RADIUS[1]
    stirred = sum(len(simulate_episode(seed, 0, "touch").bystanders) > 0 for seed in range(300))
    assert 0.05 < stirred / 300 < 0.5  # a 10% stirrer, plus the 25% wandering hand


def test_no_contact_clips_keep_a_and_its_load_off_bs_food():
    """In motion_nearby (labeled contact=False), B's food never ends up under A, and a load on A never lies over B.

    Bounding boxes are a loose stand-in for the drawn shapes, so a few percent of near misses are allowed.
    """
    from kitchen.sim.episodes import actor_extent, container_extent

    def circle_hits_box(center, radius, box):
        return np.linalg.norm(center - np.clip(center, box[:2], box[2:])) < radius

    food_under_a = load_over_b = 0
    for seed in range(200):
        episode = simulate_episode(seed, 0, "motion_nearby")
        b, frames = episode.containers[0], range(len(episode.times_ms))
        b_food = [x for x in episode.food if not x.on_actor.any() and inside_body(b, x.position[0], 0)]
        load = [x for x in episode.food if x.on_actor.all()]
        food_under_a += any(
            circle_hits_box(x.position[f], x.radius * x.stretch[f], actor_extent(episode.actor, f))
            for x in b_food
            for f in frames
        )
        load_over_b += any(
            circle_hits_box(x.position[f], x.radius, container_extent(b, f)) for x in load for f in frames
        )
    assert food_under_a <= 8 and load_over_b <= 8  # v2.2 had about 100 and 27 per 200


def dwell_paths(kind, seeds=150):
    """(B, path during the dwell, arrival point) from plan_path on a fine time grid, for one kind."""
    from kitchen.sim.episodes import fill_with_food, make_container, plan_path

    grid = np.linspace(0, 1, 2001)
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        b = make_container(rng, np.array([0.5, 0.5]))
        path, _, (arrive, leave) = plan_path(rng, kind, b, fill_with_food(rng, b), grid, is_tool=True)
        inside = (grid > arrive) & (grid < leave)
        yield b, path[inside], path[np.searchsorted(grid, arrive)]


def test_still_dwells_share_one_wiggle_and_wipes_and_touches_stay_on_b():
    """Scoop jiggles no more than any other still dwell; a wipe's wide wiggle stays over B; rim touches reach the rim."""
    for kind in ("scoop", "deposit", "hover", "touch"):
        for _b, dwell, arrival in dwell_paths(kind):
            assert np.abs(dwell - arrival).max() <= 0.008 + 1e-9, kind
    for b, dwell, _ in dwell_paths("wipe"):
        assert all(inside_body(b, point, 0) for point in dwell)
    for b, _, arrival in dwell_paths("touch"):
        # Every touch ends at least half a rim inside B's outline, so the drawn tip visibly overlaps B.
        offset = np.abs(arrival - b.center[0]) / (b.half_size - RIM_WIDTH / 2)
        reach = np.sqrt((offset**2).sum()) if b.is_round else offset.max()
        assert reach <= 1.0 + 1e-3


def test_splits_use_disjoint_seeds_and_test_uses_unseen_layouts(tmp_path):
    stats = {split: write_split(tmp_path, split, 2, seed_base=0, workers=1) for split in SPLIT_SEED_OFFSET}
    ranges = [range(s["seeds"][0], s["seeds"][1] + 1) for s in stats.values()]
    assert not set(ranges[0]) & set(ranges[1]) and not set(ranges[1]) & set(ranges[2])
    assert not set(ranges[0]) & set(ranges[2])
    assert not set(TRAIN_FAMILIES) & set(TEST_FAMILIES)
    records = {
        split: [json.loads(line) for line in (tmp_path / split / "clips.jsonl").read_text().splitlines()]
        for split in SPLIT_SEED_OFFSET
    }
    seeds = {split: {r["generator"]["seed"] for r in rs} for split, rs in records.items()}
    assert not seeds["train"] & seeds["val"] and not seeds["train"] & seeds["test"] and not seeds["val"] & seeds["test"]
    assert all(int(r["generator"]["scenario_id"].split("-")[1]) in TEST_FAMILIES for r in records["test"])
    assert all(int(r["generator"]["scenario_id"].split("-")[1]) in TRAIN_FAMILIES for r in records["train"])
    lines = (tmp_path / "train" / "clips.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    saved = np.load(tmp_path / first["tensor"]["file"])
    assert saved["x"].shape == (STEPS, 3, 128, 128)
    assert list(saved["dt_ms"]) == first["dt_ms"]
