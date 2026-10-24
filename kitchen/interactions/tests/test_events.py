"""Events: segmentation, ordering and IDs; writing through StateClient without ever dropping one; the CLI.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.events; FakeStateClient, FakeClock; scenes
Emits: pytest tests
Rules: 5 t_start_ms <= t_end_ms, each pair and action in time order and never overlapping, whichever classifier
       answered; one touch is one edge, and news from a new touch crosses every touch going on its entities, once;
       frames move forward; 1 buffered and retried on StateUnavailable, never dropped; a transfer's first hit is
       written the frame it is seen; a wash is a self-loop, needs wash_floor, and one rinse is one wash
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
    still,
    unmatched,
)
from kitchen.interactions.zone_rules import ZoneRules
from kitchen.shared.fakes import FakeClock, FakeStateClient
from kitchen.shared.models import validate

SETTINGS = InteractionSettings()
ZONE_RULES = {"name": "zone_rules", "version": "test"}
HAND = "hand-p1-right"
CONTACT = [("transfer.contact", 0.8)]
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
def test_a_touch_is_one_edge_at_its_first_hit_and_a_surer_hit_adds_only_the_difference():
    """A contact at 0.6, then 0.9, 0.7 and 0.8: one edge at 0.6 the frame it starts, and one at 33 for the
    difference, (0.9 - 0.6) / (1 - 0.6) = 0.75, so the two add up to 0.9 under noisy-OR. Hits no surer than 0.9
    write nothing. A wash on another knife in the same frames keeps its lowest probability."""
    segmenter = Segmenter(SETTINGS)
    other_knife = {"a_id": "tool-knife-2", "b_id": "tool-knife-2", "b_kind": "tool"}
    written = []
    for t_ms, contact, wash in ((0, 0.6, 0.95), (33, 0.9, 0.99), (66, 0.7, 0.92), (99, 0.8, 0.97)):
        frame = [
            judged_at(t_ms, [("transfer.contact", contact)]),
            judged_at(t_ms, [("clean.wash", wash)], **other_knife),
        ]
        written += segmenter.add_frame(t_ms, frame)
    (wash,) = segmenter.close_all()

    assert [(e["t_start_ms"], e["t_end_ms"], e["probability"]) for e in written] == [
        (0, 0, 0.6),
        (33, 33, pytest.approx(0.75)),
    ]
    assert 1 - (1 - 0.6) * (1 - written[1]["probability"]) == pytest.approx(0.9)
    assert wash["probability"] == 0.92  # a wash lowers risk, so it keeps its lowest


def test_a_surer_hit_from_the_fallback_names_the_fallback():
    """A primary that fails on one frame doesn't start a new touch: zone rules' surer hit adds an edge for the
    difference, named for zone rules, and the primary's weaker hits after it write nothing."""
    lstm = {"name": "lstm", "version": "fake-1"}
    segmenter = Segmenter(SETTINGS)
    written = segmenter.add_frame(0, [judged_at(0, [("transfer.contact", 0.7)], lstm)])
    for t_ms, probability, classifier in ((33, 0.6, lstm), (66, 0.8, ZONE_RULES), (99, 0.6, lstm)):
        written += segmenter.add_frame(t_ms, [judged_at(t_ms, [("transfer.contact", probability)], classifier)])

    assert segmenter.close_all() == []
    assert [(e["t_start_ms"], e["classifier"]) for e in written] == [(0, lstm), (66, ZONE_RULES)]
    assert written[1]["probability"] == pytest.approx((0.8 - 0.7) / (1 - 0.7))


@pytest.mark.safety
def test_news_from_a_new_touch_crosses_each_touch_going_on_its_entities_once():
    """A spoon rests on a plate (a contact from 0, a scoop from 99); at 165 a hand starts touching the spoon. Each
    spoon-plate touch writes one more edge at its next hit, 198, after the hand's first hit, so the graph carries the
    hand's allergens on to the plate; after that they write nothing. The scoop starting on the same pair at 99 is no
    news to the contact."""
    segmenter = Segmenter(SETTINGS)
    spoon_plate = {"a_id": "tool-spoon-1", "b_id": "plate-t1", "b_kind": "plate"}
    hand_spoon = {"a_id": HAND, "b_id": "tool-spoon-1", "b_kind": "tool"}
    written: dict[int, list] = {}
    for t_ms in range(0, 400, 33):
        labels = CONTACT + ([("transfer.scoop", 0.9)] if t_ms >= 99 else [])
        frame = [judged_at(t_ms, labels, **spoon_plate)]
        if t_ms >= 165:
            frame.append(judged_at(t_ms, CONTACT, **hand_spoon))
        written[t_ms] = segmenter.add_frame(t_ms, frame)
    events = [event for t_ms in sorted(written) for event in written[t_ms]] + segmenter.close_all()

    assert [(e["b_id"], e["action"], e["t_start_ms"]) for e in events] == [
        ("plate-t1", "transfer.contact", 0),
        ("plate-t1", "transfer.scoop", 99),
        ("tool-spoon-1", "transfer.contact", 165),
        ("plate-t1", "transfer.contact", 198),
        ("plate-t1", "transfer.scoop", 198),
    ]
    assert all(e["t_start_ms"] == e["t_end_ms"] for e in events)
    assert [e["probability"] for e in written[198]] == [0.8, 0.9]  # each at its touch's strength


@pytest.mark.safety
def test_touches_starting_in_the_same_frame_carry_each_others_news_whatever_their_ids():
    """A hand lays a spoon on a plate, both contacts first seen in the same frame. Their edges share a start, so the
    graph orders them by event ID, and one may apply before the other's news. Each carries the other's news once at
    its next hit, so the plate gets the hand's allergens, and the hand the plate's, either way round."""
    segmenter = Segmenter(SETTINGS)
    hand_spoon = {"a_id": HAND, "b_id": "tool-spoon-1", "b_kind": "tool"}
    spoon_plate = {"a_id": "tool-spoon-1", "b_id": "plate-t1", "b_kind": "plate"}
    events = []
    for t_ms in range(0, 300, 33):
        events += segmenter.add_frame(
            t_ms, [judged_at(t_ms, CONTACT, **hand_spoon), judged_at(t_ms, CONTACT, **spoon_plate)]
        )

    assert [(e["a_id"], e["t_start_ms"]) for e in events] == [
        (HAND, 0),
        ("tool-spoon-1", 0),
        (HAND, 33),
        ("tool-spoon-1", 33),
    ]


def test_news_never_bounces_between_touches_forever():
    """A hand holds a spoon from 0; the spoon lands on a plate at 99 and stays; at 990 the hand also touches the bin,
    and everything stays touching for three seconds. Each piece of news crosses each touch going on once: the spoon
    landing crosses the hand's grip, and the bin crosses the grip, then the spoon on the plate. Nothing more."""
    segmenter = Segmenter(SETTINGS)
    hand_spoon = {"a_id": HAND, "b_id": "tool-spoon-1", "b_kind": "tool"}
    spoon_plate = {"a_id": "tool-spoon-1", "b_id": "plate-t1", "b_kind": "plate"}
    hand_bin = {"a_id": HAND, "b_id": "bin-1", "b_kind": "bin"}
    events = []
    for t_ms in range(0, 3000, 33):
        frame = [judged_at(t_ms, CONTACT, **hand_spoon)]
        if t_ms >= 99:
            frame.append(judged_at(t_ms, CONTACT, **spoon_plate))
        if t_ms >= 990:
            frame.append(judged_at(t_ms, CONTACT, **hand_bin))
        events += segmenter.add_frame(t_ms, frame)
    events += segmenter.close_all()

    assert [(e["a_id"], e["b_id"], e["t_start_ms"]) for e in events] == [
        (HAND, "tool-spoon-1", 0),
        ("tool-spoon-1", "plate-t1", 99),
        (HAND, "tool-spoon-1", 132),
        (HAND, "bin-1", 990),
        (HAND, "tool-spoon-1", 1023),
        ("tool-spoon-1", "plate-t1", 1056),
    ]


def test_news_crosses_a_ring_of_touching_things_once_each():
    """A spoon, a knife and a plate all touch each other for two seconds; at 495 a hand touches the knife. The hand's
    news goes round the ring once: every touch writes at most one more edge for it."""
    segmenter = Segmenter(SETTINGS)
    ring = [
        {"a_id": "tool-knife-1", "b_id": "tool-spoon-1", "b_kind": "tool"},
        {"a_id": "tool-spoon-1", "b_id": "plate-t1", "b_kind": "plate"},
        {"a_id": "tool-knife-1", "b_id": "plate-t1", "b_kind": "plate"},
    ]
    hand_knife = {"a_id": HAND, "b_id": "tool-knife-1", "b_kind": "tool"}
    events = []
    for t_ms in range(0, 2000, 33):
        frame = [judged_at(t_ms, CONTACT, **pair) for pair in ring]
        if t_ms >= 495:
            frame.append(judged_at(t_ms, CONTACT, **hand_knife))
        events += segmenter.add_frame(t_ms, frame)

    after_the_hand = [e for e in events if e["t_start_ms"] > 495]
    pairs_after = [(e["a_id"], e["b_id"]) for e in after_the_hand]
    assert len(pairs_after) == len(set(pairs_after)) == 3
    assert max(e["t_start_ms"] for e in after_the_hand) <= 495 + 3 * 33


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


def segment_frames(frames: list[tuple[int, set]]) -> tuple[list, list]:
    """Run (gap before the frame, hits) frames through a Segmenter. Returns every hit as (key, t_ms), and every event
    with the t_ms of the frame that wrote it, in the order written."""
    segmenter = Segmenter(SETTINGS)
    hits, written = [], []
    t_ms = 0
    for gap, frame_hits in frames:
        t_ms += gap
        judged = []
        for pair_index, action in sorted(frame_hits):
            a_id, b_id, b_kind = PAIRS[pair_index]
            judged.append(judged_at(t_ms, [(action, 0.8)], a_id=a_id, b_id=b_id, b_kind=b_kind))
            hits.append(((a_id, b_id, action), t_ms))
        written += [(event, t_ms) for event in segmenter.add_frame(t_ms, judged)]
    written += [(event, t_ms) for event in segmenter.close_all()]
    return hits, written


def touches_of(hits: list[tuple]) -> list[tuple[tuple, list[int]]]:
    """Each touch as (pair and action, its hit times): hits of one pair and action at most merge_gap_ms apart."""
    touches: list[tuple[tuple, list[int]]] = []
    latest: dict = {}
    for key, t_ms in hits:
        if key in latest and t_ms - touches[latest[key]][1][-1] <= SETTINGS.merge_gap_ms:
            touches[latest[key]][1].append(t_ms)
        else:
            latest[key] = len(touches)
            touches.append((key, [t_ms]))
    return touches


def key_of(event: dict) -> tuple:
    """An event's pair and action."""
    return (event["a_id"], event["b_id"], event["action"])


def neighbours(key: tuple, other: tuple) -> bool:
    """Whether two transfers are on different pairs that share an entity, so news on one concerns the other."""
    return key[:2] != other[:2] and bool(set(key[:2]) & set(other[:2]))


@pytest.mark.safety
@settings(max_examples=200, deadline=None)
@given(FRAMES)
def test_a_touch_is_one_edge_plus_one_per_news_and_news_crosses_every_touch_going_on(frames):
    hits, written = segment_frames(frames)
    events = [event for event, _ in written]

    # Every edge is one hit of its pair and action, written the frame of that hit.
    hit_set = set(hits)
    for event, t_ms in written:
        assert event["t_start_ms"] == event["t_end_ms"] == t_ms
        assert (key_of(event), t_ms) in hit_set

    # IDs are unique and replays repeat them; one pair and action's edges come out in time order.
    assert len({e["event_id"] for e in events}) == len(events)
    assert segment_frames(frames)[1] == written
    edge_times: dict = {}
    for event in events:
        edge_times.setdefault(key_of(event), []).append(event["t_start_ms"])
    assert all(times == sorted(set(times)) for times in edge_times.values())

    touches = touches_of(hits)
    for key, times in touches:
        mine = [t for t in edge_times.get(key, []) if times[0] <= t <= times[-1]]
        # A touch's first hit is written the frame it is seen...
        assert mine and mine[0] == times[0]
        # ...and it writes again only to carry news: an edge on a neighbouring touch since its own last one.
        for before, after in pairwise(mine):
            assert any(neighbours(key, key_of(e)) and before <= e["t_start_ms"] < after for e in events)

    # A new touch is news to every touch going on its entities, which carries it across at its next hit, after it.
    for new_key, new_times in touches:
        started = new_times[0]
        for key, times in touches:
            if neighbours(key, new_key) and times[0] <= started < times[-1]:
                next_hit = next(t for t in times if t > started)
                assert next_hit in edge_times[key], (new_key, started, key)


def test_a_long_touch_is_one_edge_written_the_frame_it_starts():
    # A hand resting on the plate for three seconds.
    hand = Track(HAND, "hand", ((0, (0.5, 0.55)), (3000, (0.5, 0.55))))
    frames = Scene([still("plate-t1", "plate", PLATE_AT, 3000), hand], 3000, []).frames()
    step = InteractionStep(ZONES, settings=SETTINGS)

    written = [(event, frame["t_ms"]) for frame in frames for event in step.process(frame)]

    assert step.finish() == []
    ((event, t_ms),) = written
    assert event["t_start_ms"] == event["t_end_ms"] == t_ms


def test_a_wash_is_never_cut():
    (wash,) = replay(SCENES["wash"].frames(), ZONES, SETTINGS)

    assert wash["t_end_ms"] - wash["t_start_ms"] > 1000  # the whole rinse, about 1.2 s


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


def test_pipeline_e2e_a_transfer_is_written_the_frame_of_its_hit_and_a_wash_soon_after_the_rinse():
    state = FakeStateClient()
    clock = FakeClock()
    written_at: dict = {}
    state.subscribe("interaction", lambda change: written_at.setdefault(change.row["event_id"], clock.now_ms()))

    run_live(back_to_back(sorted(SCENES)), state, clock)

    recorded = state.list_interactions()
    transfers = [event for event in recorded if event["action"] != "clean.wash"]
    assert transfers and len(transfers) < len(recorded)
    for event in recorded:
        if event["action"] == "clean.wash":
            # A wash is written wash_gap_ms after its last hit, since it bridges a loss.
            assert written_at[event["event_id"]] - event["t_end_ms"] <= SETTINGS.wash_gap_ms + 200, event["event_id"]
        else:
            assert written_at[event["event_id"]] == event["t_start_ms"] == event["t_end_ms"], event["event_id"]


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
