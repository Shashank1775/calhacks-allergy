"""Events: segmentation, ordering and IDs; writing through StateClient without ever dropping one; the CLI.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.events; FakeStateClient, FakeClock; scenes
Emits: pytest tests
Rules: 5 t_start_ms <= t_end_ms, each pair and action in time order and never overlapping, whichever classifier
       answered; a new touch cuts the transfers open on its entities; frames move forward; 1 buffered and retried on
       StateUnavailable, never dropped; a transfer's first hit is written the frame it is seen; a wash is a self-loop,
       needs wash_floor, and one rinse is one wash
"""

import json
from itertools import pairwise

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kitchen.interactions.classifier import FallbackClassifier
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
from kitchen.interactions.tests.scenes import (
    KNIFE,
    PLATE_AT,
    SCENES,
    WASH_AT,
    ZONES,
    Scene,
    Track,
    render,
    spans,
    still,
    unmatched,
)
from kitchen.interactions.zone_rules import ZoneRules
from kitchen.shared.fakes import FakeClock, FakeStateClient
from kitchen.shared.models import validate

SETTINGS = InteractionSettings()
ZONE_RULES = {"name": "zone_rules", "version": "test"}
MASK = np.zeros((128, 128), dtype=np.float32)


class FlakyPrimary:
    """A stand-in for an adopted ConvLSTM that answers contact on every window but fails on every fifth frame, so
    zone rules answer those frames in the middle of its touches."""

    name = "lstm"
    version = "flaky-1"

    def classify(self, window):
        if window.features[-1]["frame_id"] % 5 == 0:
            raise RuntimeError("one bad frame")
        return [("transfer.contact", 0.9)]


CLASSIFIERS = {
    "zone rules": lambda: None,
    "a primary that fails now and then": lambda: FallbackClassifier(ZoneRules(SETTINGS), FlakyPrimary()),
}


def emitted(frames: list[dict], classifier: FallbackClassifier | None = None) -> list[dict]:
    """Every event the step writes, in the order it writes them: each frame's, then the stream end's."""
    step = InteractionStep(ZONES, settings=SETTINGS, classifier=classifier, with_clips=classifier is not None)
    events = []
    for frame in frames:
        events += step.process(frame, MASK)
    return events + step.finish()


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


def judged_at(t_ms: int, labels: list, classifier: dict = ZONE_RULES, **pair) -> tuple:
    """One judged window for Segmenter.add_frame: the pair's feature row at t_ms, its labels and who gave them."""
    return (features_at(t_ms, **pair), labels, classifier)


@pytest.mark.safety
@pytest.mark.parametrize("classifier", sorted(CLASSIFIERS))
@pytest.mark.parametrize("name", sorted(SCENES))
def test_events_are_valid_time_ordered_and_never_overlap_on_one_pair_and_action(name, classifier):
    events = emitted(SCENES[name].frames(), CLASSIFIERS[classifier]())

    by_key: dict = {}
    for event in events:
        validate(event, "interaction_event")
        assert event["t_start_ms"] <= event["t_end_ms"]
        by_key.setdefault((event["a_id"], event["b_id"], event["action"]), []).append(event)
    # In the order written, whichever classifier answered each frame.
    for same in by_key.values():
        assert all(earlier["t_end_ms"] < later["t_start_ms"] for earlier, later in pairwise(same))
    assert len({event["event_id"] for event in events}) == len(events)
    replayed = replay(SCENES[name].frames(), ZONES, SETTINGS)
    assert replayed == sorted(replayed, key=event_order)


def test_replays_give_the_same_event_ids_and_write_no_duplicates():
    frames = SCENES["scoop_then_plate"].frames()
    state = FakeStateClient()

    first = run_live(frames, state, FakeClock())
    second = run_live(frames, state, FakeClock())

    recorded = state.list_interactions()
    assert len(first.pending) == len(second.pending) == 0
    assert recorded == replay(frames, ZONES, SETTINGS)
    assert len({event["event_id"] for event in recorded}) == len(recorded)
    assert unmatched(recorded, SCENES["scoop_then_plate"].expected) == []


@pytest.mark.safety
def test_a_wash_is_a_self_loop_and_nothing_else_is():
    segmenter = Segmenter(SETTINGS)

    # A wash label on a two-entity pair, a transfer on a self pair, and a wash below the floor make no edge.
    nothing = [
        judged_at(0, [("clean.wash", 0.99)]),
        judged_at(0, [("transfer.contact", 0.9)], b_id=KNIFE, b_kind="tool"),
        judged_at(0, [("clean.wash", SETTINGS.wash_floor - 0.01)], b_id=KNIFE, b_kind="tool"),
    ]
    assert segmenter.add_frame(0, nothing) == []
    assert segmenter.close_all() == []

    segmenter.add_frame(33, [judged_at(33, [("clean.wash", 0.97)], b_id=KNIFE, b_kind="tool")])
    (wash,) = segmenter.close_all()
    assert (wash["a_id"], wash["b_id"], wash["action"]) == (KNIFE, KNIFE, "clean.wash")


def test_unimportant_and_weak_transfers_make_no_edge():
    segmenter = Segmenter(SETTINGS)

    weak = [("unimportant", 1.0), ("transfer.contact", SETTINGS.event_floor - 0.01)]
    assert segmenter.add_frame(0, [judged_at(0, weak)]) == []
    assert segmenter.close_all() == []


@pytest.mark.safety
def test_a_transfers_first_hit_is_written_at_once_and_the_rest_keeps_its_highest_probability():
    segmenter = Segmenter(SETTINGS)
    other_knife = {"a_id": "tool-knife-2", "b_id": "tool-knife-2", "b_kind": "tool"}

    first = segmenter.add_frame(
        0, [judged_at(0, [("transfer.contact", 0.6)]), judged_at(0, [("clean.wash", 0.95)], **other_knife)]
    )
    for t_ms, contact, wash in ((33, 0.9, 0.99), (66, 0.7, 0.92), (99, 0.8, 0.97)):
        frame = [
            judged_at(t_ms, [("transfer.contact", contact)]),
            judged_at(t_ms, [("clean.wash", wash)], **other_knife),
        ]
        assert segmenter.add_frame(t_ms, frame) == []
    rest = {event["action"]: event for event in segmenter.close_all()}

    # The touch reaches the database the frame it starts; the rest of it is one event from the next hit.
    assert [(event["t_start_ms"], event["t_end_ms"], event["probability"]) for event in first] == [(0, 0, 0.6)]
    contact = rest["transfer.contact"]
    assert (contact["t_start_ms"], contact["t_end_ms"], contact["probability"]) == (33, 99, 0.9)
    assert rest["clean.wash"]["probability"] == 0.92  # a wash lowers risk, so it keeps its lowest


def test_one_event_per_pair_and_action_names_the_classifier_of_its_strongest_hit():
    """A primary that fails on one frame doesn't split a touch: zone rules' hit joins the same event, and the event
    names whichever classifier gave its highest probability."""
    lstm = {"name": "lstm", "version": "fake-1"}
    segmenter = Segmenter(SETTINGS)
    segmenter.add_frame(0, [judged_at(0, [("transfer.contact", 0.7)], lstm)])
    for t_ms, probability, classifier in ((33, 0.6, lstm), (66, 0.8, ZONE_RULES), (99, 0.6, lstm)):
        segmenter.add_frame(t_ms, [judged_at(t_ms, [("transfer.contact", probability)], classifier)])

    (rest,) = segmenter.close_all()

    assert (rest["t_start_ms"], rest["t_end_ms"], rest["probability"]) == (33, 99, 0.8)
    assert rest["classifier"] == ZONE_RULES


@pytest.mark.safety
def test_a_new_touch_cuts_the_transfers_open_on_its_entities_so_they_carry_what_it_brought():
    """A spoon rests on a plate (an open contact); at 165 a hand starts touching the spoon. The spoon-plate contact is
    cut there, and its rest starts after the hand's first hit and is written at once, so the graph carries the
    hand's allergens on to the plate. A scoop starting on the same pair at 99 carries nothing new and cuts nothing."""
    segmenter = Segmenter(SETTINGS)
    spoon_plate = {"a_id": "tool-spoon-1", "b_id": "plate-t1", "b_kind": "plate"}
    hand_spoon = {"a_id": "hand-p1-right", "b_id": "tool-spoon-1", "b_kind": "tool"}
    written: dict[int, list] = {}
    for t_ms in range(0, 400, 33):
        labels = [("transfer.contact", 0.8)] + ([("transfer.scoop", 0.9)] if t_ms >= 99 else [])
        frame = [judged_at(t_ms, labels, **spoon_plate)]
        if t_ms >= 165:
            frame.append(judged_at(t_ms, [("transfer.contact", 0.8)], **hand_spoon))
        written[t_ms] = segmenter.add_frame(t_ms, frame)
    events = [event for t_ms in sorted(written) for event in written[t_ms]] + segmenter.close_all()

    contacts = [e for e in events if (e["b_id"], e["action"]) == ("plate-t1", "transfer.contact")]
    assert [(e["t_start_ms"], e["t_end_ms"]) for e in contacts] == [(0, 0), (33, 165), (198, 198), (231, 396)]
    # At 165: the hand's first hit, and both spoon-plate transfers cut there (the scoop's first hit was at 99).
    assert [(e["b_id"], e["action"], e["t_start_ms"], e["t_end_ms"]) for e in written[165]] == [
        ("plate-t1", "transfer.contact", 33, 165),
        ("plate-t1", "transfer.scoop", 132, 165),
        ("tool-spoon-1", "transfer.contact", 165, 165),
    ]
    assert ("plate-t1", 198, 198) in [(e["b_id"], e["t_start_ms"], e["t_end_ms"]) for e in written[198]]


PAIRS = [
    ("hand-p1-right", "bin-1", "bin"),
    ("hand-p1-right", "plate-t1", "plate"),
    ("hand-p1-right", "tool-spoon-1", "tool"),
    ("tool-spoon-1", "plate-t1", "plate"),
    ("tool-spoon-1", "bin-1", "bin"),
]
ACTIONS = ["transfer.contact", "transfer.scoop"]
HITS = st.sets(st.tuples(st.sampled_from(range(len(PAIRS))), st.sampled_from(ACTIONS)))
FRAMES = st.lists(st.tuples(st.integers(1, 400), HITS), max_size=60)


def segment_frames(frames: list[tuple[int, set]]) -> tuple[list, list, dict]:
    """Run (gap before the frame, hits) frames through a Segmenter. Returns every hit as (key, t_ms), every event in
    the order written, and each new touch's t_ms -> the events written that frame."""
    segmenter = Segmenter(SETTINGS)
    hits, events, written_with_touch = [], [], {}
    last_hit: dict = {}
    t_ms = 0
    for gap, frame_hits in frames:
        t_ms += gap
        judged = []
        for pair_index, action in sorted(frame_hits):
            a_id, b_id, b_kind = PAIRS[pair_index]
            judged.append(judged_at(t_ms, [(action, 0.8)], a_id=a_id, b_id=b_id, b_kind=b_kind))
            key = (a_id, b_id, action)
            hits.append((key, t_ms))
            if key not in last_hit or t_ms - last_hit[key] > SETTINGS.merge_gap_ms:
                written_with_touch[(key, t_ms)] = None
            last_hit[key] = t_ms
        frame_events = segmenter.add_frame(t_ms, judged)
        for touch in written_with_touch:
            if touch[1] == t_ms:
                written_with_touch[touch] = frame_events
        events += frame_events
    return hits, events + segmenter.close_all(), written_with_touch


def key_of(event: dict) -> tuple:
    """An event's pair and action."""
    return (event["a_id"], event["b_id"], event["action"])


@pytest.mark.safety
@settings(max_examples=150, deadline=None)
@given(FRAMES)
def test_segmenting_never_drops_overlaps_or_reorders_a_hit_and_new_touches_are_written_at_once(frames):
    hits, events, new_touches = segment_frames(frames)

    # Every hit is in exactly one event of its pair and action, and every event starts and ends on real hits.
    for key, t_ms in hits:
        assert sum(key_of(e) == key and e["t_start_ms"] <= t_ms <= e["t_end_ms"] for e in events) == 1
    hit_set = set(hits)
    assert all((key_of(e), e["t_start_ms"]) in hit_set and (key_of(e), e["t_end_ms"]) in hit_set for e in events)

    # One pair and action's events come out in time order and never overlap; IDs are unique and replays repeat them.
    for key in {key_of(e) for e in events}:
        same = [e for e in events if key_of(e) == key]
        assert all(earlier["t_end_ms"] < later["t_start_ms"] for earlier, later in pairwise(same))
    assert len({e["event_id"] for e in events}) == len(events)
    assert segment_frames(frames)[1] == events

    for (key, t_ms), written in new_touches.items():
        # A new touch's first hit reaches the database the frame it is seen...
        assert any(key_of(e) == key and (e["t_start_ms"], e["t_end_ms"]) == (t_ms, t_ms) for e in written)
        # ...and no transfer on another pair sharing an entity spans it, so the graph applies it in order.
        for e in events:
            if (e["a_id"], e["b_id"]) != key[:2] and {e["a_id"], e["b_id"]} & set(key[:2]):
                assert not e["t_start_ms"] < t_ms < e["t_end_ms"]


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


@pytest.mark.safety
def test_a_rinse_split_by_a_loss_longer_than_the_bridge_is_still_one_wash():
    """The knife is unseen in the sink for 1.5 s, longer than wash_gap_ms, so its dwell starts over and a second wash
    label comes. Nothing touched the knife between the two, so the second can't make it any cleaner: it is dropped."""
    knife = Track(
        KNIFE,
        "tool",
        ((0, (0.5, 0.8)), (300, WASH_AT), (9000, WASH_AT), (9300, (0.5, 0.8)), (9600, (0.5, 0.8))),
        unseen=((4000, 5500),),
    )

    events = replay(render([knife], 9600), ZONES, SETTINGS)

    assert [event["action"] for event in events] == ["clean.wash"]


def test_a_second_wash_counts_once_a_transfer_has_touched_the_entity_again():
    """Washed, laid on a plate (a contact), then rinsed again: the second wash cleans what the plate gave it, so it
    is kept, and it starts after the contact."""
    knife = Track(
        KNIFE,
        "tool",
        (
            (0, (0.5, 0.8)),
            (300, WASH_AT),
            (3600, WASH_AT),
            (3900, PLATE_AT),
            (4400, PLATE_AT),
            (4700, WASH_AT),
            (8500, WASH_AT),
            (8800, (0.5, 0.8)),
            (9000, (0.5, 0.8)),
        ),
    )

    events = replay(render([still("plate-t1", "plate", PLATE_AT, 9000), knife], 9000), ZONES, SETTINGS)

    washes = [event for event in events if event["action"] == "clean.wash"]
    contacts = [event for event in events if event["action"] == "transfer.contact"]
    assert len(washes) == 2 and contacts
    assert washes[1]["t_start_ms"] > max(contact["t_end_ms"] for contact in contacts)


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


def test_pipeline_e2e_a_touch_is_written_the_frame_it_starts_and_every_edge_soon_after_it_ends():
    state = FakeStateClient()
    clock = FakeClock()
    written_at: dict = {}
    state.subscribe("interaction", lambda change: written_at.setdefault(change.row["event_id"], clock.now_ms()))

    run_live(back_to_back(sorted(SCENES)), state, clock)

    recorded = state.list_interactions()
    transfers = [event for event in recorded if event["action"] != "clean.wash"]
    assert transfers and len(transfers) < len(recorded)
    for event in recorded:
        # A transfer is written merge_gap_ms after its last hit, a wash wash_gap_ms after (it bridges a loss).
        allowed_ms = (SETTINGS.wash_gap_ms if event["action"] == "clean.wash" else SETTINGS.merge_gap_ms) + 200
        assert written_at[event["event_id"]] - event["t_end_ms"] <= allowed_ms, event["event_id"]
    for touch in spans(transfers):
        first = next(event for event in transfers if event["event_id"] == touch["event_id"])
        assert written_at[first["event_id"]] == first["t_start_ms"], first["event_id"]


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
