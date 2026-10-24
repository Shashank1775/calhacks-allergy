"""Events: segmentation, ordering and IDs; writing through StateClient without ever dropping one; the CLI.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.events; FakeStateClient, FakeClock; scenes
Emits: pytest tests
Rules: 5 t_start_ms <= t_end_ms, each pair and action in time order, frames move forward; 1 buffered and retried on
       StateUnavailable, never dropped; a wash is a self-loop and needs wash_floor
"""

import json

import numpy as np
import pytest

from kitchen.interactions.events import (
    EventWriter,
    InteractionStep,
    Segmenter,
    direction_for,
    event_order,
    main,
    replay,
)
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import KNIFE, PLATE_AT, SCENES, ZONES, Scene, Track, still, unmatched
from kitchen.shared.fakes import FakeClock, FakeStateClient
from kitchen.shared.models import validate

SETTINGS = InteractionSettings()
ZONE_RULES = {"name": "zone_rules", "version": "test"}


def back_to_back(names: list[str]) -> list[dict]:
    """The named scenes one after another, re-timed 33 ms apart so time runs forward across them."""
    frames = [frame for name in names for frame in SCENES[name].frames()]
    return [{**frame, "frame_id": index, "t_ms": index * 33} for index, frame in enumerate(frames)]


def run_live(frames: list[dict], state: FakeStateClient, clock: FakeClock) -> EventWriter:
    """Run frames through the step and the writer the way the Pi does, with the clock at each frame's time."""
    step = InteractionStep(ZONES, settings=SETTINGS)
    writer = EventWriter(state, clock, SETTINGS.retry_ms)
    for frame in frames:
        clock.set(frame["t_ms"])
        writer.write(step.process(frame))
    writer.write(step.finish())
    return writer


def features_at(t_ms: int, a_id: str = KNIFE, b_id: str = "plate-t1", b_kind: str = "plate") -> dict:
    """The fields of a feature row the segmenter reads."""
    return {"t_ms": t_ms, "frame_id": t_ms // 33, "a_id": a_id, "b_id": b_id, "b_kind": b_kind}


@pytest.mark.safety
@pytest.mark.parametrize("name", sorted(SCENES))
def test_events_are_valid_time_ordered_and_never_overlap_on_one_pair_and_action(name):
    events = replay(SCENES[name].frames(), ZONES, SETTINGS)

    by_key: dict = {}
    for event in events:
        validate(event, "interaction_event")
        assert event["t_start_ms"] <= event["t_end_ms"]
        by_key.setdefault((event["a_id"], event["b_id"], event["action"]), []).append(event)
    for same in by_key.values():
        assert all(earlier["t_end_ms"] < later["t_start_ms"] for earlier, later in zip(same, same[1:], strict=False))
    assert events == sorted(events, key=event_order)


def test_replays_give_the_same_event_ids_and_write_no_duplicates():
    frames = SCENES["scoop_then_plate"].frames()
    state = FakeStateClient()

    first = run_live(frames, state, FakeClock())
    second = run_live(frames, state, FakeClock())

    recorded = state.list_interactions()
    assert len(first.pending) == len(second.pending) == 0
    assert recorded == replay(frames, ZONES, SETTINGS)
    assert len({event["event_id"] for event in recorded}) == len(recorded) == 3


@pytest.mark.safety
def test_a_wash_is_a_self_loop_and_nothing_else_is():
    segmenter = Segmenter(SETTINGS)

    # A wash label on a two-entity pair, a transfer on a self pair, and a wash below the floor make no edge.
    segmenter.add(features_at(0), [("clean.wash", 0.99)], ZONE_RULES)
    segmenter.add(features_at(0, b_id=KNIFE, b_kind="tool"), [("transfer.contact", 0.9)], ZONE_RULES)
    segmenter.add(features_at(0, b_id=KNIFE, b_kind="tool"), [("clean.wash", SETTINGS.wash_floor - 0.01)], ZONE_RULES)
    assert segmenter.close_all() == []

    segmenter.add(features_at(0, b_id=KNIFE, b_kind="tool"), [("clean.wash", 0.97)], ZONE_RULES)
    (wash,) = segmenter.close_all()
    assert (wash["a_id"], wash["b_id"], wash["action"]) == (KNIFE, KNIFE, "clean.wash")


def test_unimportant_and_weak_transfers_make_no_edge():
    segmenter = Segmenter(SETTINGS)

    segmenter.add(features_at(0), [("unimportant", 1.0), ("transfer.contact", SETTINGS.event_floor - 0.01)], ZONE_RULES)

    assert segmenter.close_all() == []


@pytest.mark.safety
def test_a_transfer_keeps_its_highest_probability_and_a_wash_its_lowest():
    segmenter = Segmenter(SETTINGS)
    for t_ms, contact, wash in ((0, 0.6, 0.95), (33, 0.9, 0.99), (66, 0.7, 0.92)):
        segmenter.add(features_at(t_ms), [("transfer.contact", contact)], ZONE_RULES)
        segmenter.add(features_at(t_ms, b_id=KNIFE, b_kind="tool"), [("clean.wash", wash)], ZONE_RULES)

    events = {event["action"]: event for event in segmenter.close_all()}

    assert events["transfer.contact"]["probability"] == 0.9
    assert events["clean.wash"]["probability"] == 0.92
    assert (events["transfer.contact"]["t_start_ms"], events["transfer.contact"]["t_end_ms"]) == (0, 66)


def test_a_long_transfer_is_cut_so_its_edge_is_not_held_back():
    # A hand resting on the plate for three seconds.
    hand = Track("hand-p1-right", "hand", ((0, (0.5, 0.55)), (3000, (0.5, 0.55))))
    frames = Scene([still("plate-t1", "plate", PLATE_AT, 3000), hand], 3000, []).frames()

    events = replay(frames, ZONES, SETTINGS)

    assert len(events) >= 3
    assert all(event["t_end_ms"] - event["t_start_ms"] <= SETTINGS.max_event_ms for event in events)


def test_a_wash_is_never_cut():
    (wash,) = replay(SCENES["wash"].frames(), ZONES, SETTINGS)

    assert wash["t_end_ms"] - wash["t_start_ms"] > SETTINGS.max_event_ms


def test_directions_follow_the_open_question_defaults():
    assert direction_for("transfer.scoop", "bin") == "b_to_a"
    assert direction_for("transfer.scoop", "plate") == "both"  # the camera can't tell which way food went
    assert direction_for("transfer.contact", "bin") == "both"
    assert direction_for("transfer.wipe", "plate") == "both"


@pytest.mark.safety
def test_frames_must_move_forward():
    step = InteractionStep(ZONES)
    frames = SCENES["hover"].frames()
    step.process(frames[1])

    with pytest.raises(ValueError, match="time runs forward"):
        step.process(frames[0])
    with pytest.raises(ValueError, match="time runs forward"):
        step.process(frames[1])


@pytest.mark.safety
def test_never_drops_an_event_while_the_database_is_down():
    frames = back_to_back(["scoop_then_plate", "wash"])
    state = FakeStateClient()
    clock = FakeClock()
    state.set_unavailable()

    writer = run_live(frames, state, clock)
    buffered = len(writer.pending)
    state.set_unavailable(False)
    clock.advance(SETTINGS.retry_ms)

    assert buffered == len(replay(frames, ZONES, SETTINGS)) > 0
    assert writer.flush() == 0
    assert state.list_interactions() == replay(frames, ZONES, SETTINGS)


@pytest.mark.safety
def test_intermittent_failures_lose_nothing_and_keep_the_order():
    state = FakeStateClient()
    clock = FakeClock()
    writer = EventWriter(state, clock, SETTINGS.retry_ms)
    events = replay(SCENES["scoop_then_plate"].frames(), ZONES, SETTINGS)

    state.fail_next(1)
    assert writer.write(events) == len(events)  # first write failed: everything waits
    assert writer.flush() == len(events)  # too soon to retry: the client isn't called again yet
    clock.advance(SETTINGS.retry_ms)
    assert writer.flush() == 0

    assert state.list_interactions() == events


def test_pipeline_e2e_edges_reach_the_database_within_500_ms_of_their_end():
    state = FakeStateClient()
    clock = FakeClock()
    written_at: dict = {}
    state.subscribe("interaction", lambda change: written_at.setdefault(change.row["event_id"], clock.now_ms()))

    run_live(back_to_back(sorted(SCENES)), state, clock)

    recorded = state.list_interactions()
    assert recorded
    for event in recorded:
        assert written_at[event["event_id"]] - event["t_end_ms"] <= 500, event["event_id"]


def write_recording(tmp_path, frames: list[dict], with_masks: bool) -> dict:
    """Write frames (and optionally masks and a manifest) the way vision records them, plus a zones file."""
    paths = {"frames": tmp_path / "frames.jsonl", "zones": tmp_path / "zones.json", "out": tmp_path / "events.jsonl"}
    paths["frames"].write_text("".join(json.dumps(frame) + "\n" for frame in frames))
    zones = [{"zone_id": zone.zone_id, "kind": zone.kind, "polygon": [list(p) for p in zone.polygon]} for zone in ZONES]
    paths["zones"].write_text(json.dumps({"zones": zones}))
    if with_masks:
        paths["change"] = tmp_path / "change.npz"
        masks = np.random.default_rng(5).integers(0, 256, (len(frames), 128, 128), dtype=np.uint8)
        np.savez(paths["change"], change=masks)
        manifest = {
            "width": 1280,
            "height": 720,
            "change_mask_params": {"gain": 4.0, "blur_sigma_px": 1.5, "decay": 0.0},
        }
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return paths


@pytest.mark.parametrize("with_masks", [False, True])
def test_cli_turns_frames_into_valid_events(tmp_path, with_masks):
    scene = SCENES["scoop_then_plate"]
    paths = write_recording(tmp_path, scene.frames(), with_masks)
    argv = ["--frames", str(paths["frames"]), "--zones", str(paths["zones"]), "--out", str(paths["out"])]
    if with_masks:
        argv += ["--change", str(paths["change"])]

    assert main(argv) == 0

    events = [json.loads(line) for line in paths["out"].read_text().splitlines()]
    for event in events:
        validate(event, "interaction_event")
    assert unmatched(events, scene.expected) == []


def test_cli_refuses_masks_that_do_not_match_the_frames(tmp_path):
    frames = SCENES["hover"].frames()
    paths = write_recording(tmp_path, frames, with_masks=True)
    np.savez(paths["change"], change=np.zeros((len(frames) - 1, 128, 128), dtype=np.uint8))

    with pytest.raises(ValueError, match="masks for"):
        main(
            [
                "--frames",
                str(paths["frames"]),
                "--zones",
                str(paths["zones"]),
                "--out",
                str(paths["out"]),
                "--change",
                str(paths["change"]),
            ]
        )
