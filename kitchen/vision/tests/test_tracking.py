"""Tests for tracking and identity: ByteTrack association, entity IDs, origins, hand boxes and the emitted items.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.tracking, kitchen.vision.identity, the observation_frame schema
Emits: nothing
Rules: known-clean source (new ID on re-acquire, origin, IDs never reused) and fail closed (a lost track emits
       nothing, no hand from an unsure wrist)
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kitchen.shared.models import Zone, validate
from kitchen.vision.common import Detection, Person, VisionSettings
from kitchen.vision.identity import IdentityBook, origin_for
from kitchen.vision.tracking import Tracker, TrackSet, greedy_match, hand_box, iou_matrix

SETTINGS = VisionSettings(track_lost_frames=3, shift_start_ms=3000)
AFTER_SHIFT_START = 10_000
FRAME_MS = 33

ZONES = [
    Zone("slot-4", "bin_slot", [(0.0, 0.0), (0.2, 0.0), (0.2, 0.2), (0.0, 0.2)]),
    Zone("clean-stack", "clean_stack", [(0.8, 0.8), (1.0, 0.8), (1.0, 1.0), (0.8, 1.0)]),
]

KNIFE_BOX = (0.4, 0.4, 0.5, 0.5)
SLOT_BOX = (0.05, 0.05, 0.15, 0.15)
CLEAN_STACK_BOX = (0.85, 0.85, 0.95, 0.95)
UNSURE = (0.0, 0.0, 0.0)


def zone_of(point):
    """A ZoneLookup over the rectangular test zones."""
    x, y = point
    for zone in ZONES:
        xs = [corner[0] for corner in zone.polygon]
        ys = [corner[1] for corner in zone.polygon]
        if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys):
            return zone
    return None


def knife(box=KNIFE_BOX, confidence=0.9):
    return Detection("knife", box, confidence)


def bowl(box=SLOT_BOX, confidence=0.9):
    return Detection("bowl", box, confidence)


def person(left_wrist=(0.5, 0.5, 0.9), right_wrist=UNSURE, left_elbow=UNSURE, right_elbow=UNSURE):
    return Person((0.3, 0.3, 0.7, 0.9), 0.9, left_wrist, right_wrist, left_elbow, right_elbow)


def run(tracker, frames, start_ms=0, width=640, height=640):
    """Feed (objects, people) frames to the tracker, one every FRAME_MS, and return each frame's items."""
    outputs = []
    for index, (objects, people) in enumerate(frames):
        outputs.append(tracker.update(objects, people, start_ms + index * FRAME_MS, zone_of, width, height))
    return outputs


def gone(frame_count):
    """Frames with nothing detected, long enough to end every track."""
    return [([], [])] * frame_count


def test_iou_matrix_and_greedy_match_pick_the_best_pairs():
    iou = iou_matrix([(0, 0, 1, 1), (0, 0, 0.5, 0.5)], [(0, 0, 0.5, 0.5), (0, 0, 1, 1), (0.9, 0.9, 1, 1)])

    assert iou[0, 1] == pytest.approx(1.0)
    assert iou[1, 0] == pytest.approx(1.0)
    assert iou[0, 0] == pytest.approx(0.25)
    assert greedy_match(iou, 0.3) == [(0, 1), (1, 0)]
    assert greedy_match(iou_matrix([], [(0, 0, 1, 1)]), 0.3) == []


def test_a_low_score_detection_keeps_a_track_but_never_starts_one():
    counter = iter(range(1, 100))
    tracks = TrackSet(SETTINGS, lambda: next(counter))

    owners, _ = tracks.update([KNIFE_BOX], [0.3])
    assert owners == {}

    owners, _ = tracks.update([KNIFE_BOX], [0.9])
    assert owners == {0: 1}

    owners, _ = tracks.update([KNIFE_BOX], [0.3])
    assert owners == {0: 1}


def test_a_track_matched_within_the_lost_buffer_keeps_its_id():
    tracker = Tracker(SETTINGS)
    outputs = run(tracker, [([knife()], [])] + gone(SETTINGS.track_lost_frames) + [([knife()], [])])

    assert outputs[0][0]["entity_id"] == outputs[-1][0]["entity_id"] == "tool-knife-1"
    assert outputs[0][0]["track_id"] == outputs[-1][0]["track_id"]


@pytest.mark.safety
def test_a_lost_track_emits_nothing():
    tracker = Tracker(SETTINGS)
    outputs = run(tracker, [([knife()], [])] + gone(SETTINGS.track_lost_frames))

    assert len(outputs[0]) == 1
    assert all(items == [] for items in outputs[1:])


@pytest.mark.safety
def test_reacquired_after_the_lost_buffer_is_a_new_entity_with_unknown_origin():
    tracker = Tracker(SETTINGS)
    first = tracker.update([knife()], [], 0, zone_of, 640, 640)
    for frame in range(1, SETTINGS.track_lost_frames + 2):
        tracker.update([], [], frame * FRAME_MS, zone_of, 640, 640)
    again = tracker.update([knife()], [], AFTER_SHIFT_START, zone_of, 640, 640)

    assert first[0]["entity_id"] == "tool-knife-1"
    assert first[0]["origin"] == "shift_start"
    assert again[0]["entity_id"] == "tool-knife-2"
    assert again[0]["track_id"] > first[0]["track_id"]
    assert again[0]["origin"] == "unknown"


@pytest.mark.safety
def test_origin_is_shift_start_up_to_shift_start():
    tracker = Tracker(SETTINGS)
    items = tracker.update([knife()], [], SETTINGS.shift_start_ms, zone_of, 640, 640)

    assert items[0]["origin"] == "shift_start"
    assert origin_for(SETTINGS.shift_start_ms, None, SETTINGS.shift_start_ms) == "shift_start"


@pytest.mark.safety
def test_origin_is_clean_stack_only_when_first_seen_in_the_clean_stack():
    tracker = Tracker(SETTINGS)
    items = tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)

    assert items[0]["zone"] == "clean-stack"
    assert items[0]["origin"] == "clean_stack"


@pytest.mark.safety
def test_origin_is_unknown_anywhere_else_after_shift_start_and_never_changes():
    tracker = Tracker(SETTINGS)
    first = tracker.update([knife((0.6, 0.6, 0.85, 0.85))], [], AFTER_SHIFT_START, zone_of, 640, 640)
    # Moving into the clean stack later doesn't clean an entity first seen elsewhere.
    later = tracker.update([knife((0.65, 0.65, 0.95, 0.95))], [], AFTER_SHIFT_START + FRAME_MS, zone_of, 640, 640)

    assert first[0]["origin"] == "unknown"
    assert later[0]["zone"] == "clean-stack"
    assert later[0]["entity_id"] == first[0]["entity_id"]
    assert later[0]["origin"] == "unknown"


def test_a_bowl_in_a_bin_slot_is_that_bin_and_any_other_bowl_is_a_plate():
    tracker = Tracker(SETTINGS)
    items = tracker.update([bowl(), bowl((0.5, 0.5, 0.7, 0.7))], [], 0, zone_of, 640, 640)

    assert [(item["entity_id"], item["kind"], item["zone"]) for item in items] == [
        ("bin-4", "bin", "slot-4"),
        ("plate-1", "plate", None),
    ]


def test_a_bowl_reacquired_in_its_slot_is_the_same_bin_with_its_first_origin():
    tracker = Tracker(SETTINGS)
    outputs = run(tracker, [([bowl()], [])] + gone(SETTINGS.track_lost_frames + 1))
    again = tracker.update([bowl()], [], AFTER_SHIFT_START, zone_of, 640, 640)

    assert outputs[0][0]["entity_id"] == again[0]["entity_id"] == "bin-4"
    assert again[0]["track_id"] != outputs[0][0]["track_id"]
    assert again[0]["origin"] == "shift_start"


@pytest.mark.safety
def test_a_second_bowl_in_a_held_slot_is_a_plate_not_a_second_bin():
    tracker = Tracker(SETTINGS)
    tracker.update([bowl()], [], 0, zone_of, 640, 640)
    items = tracker.update([bowl(), bowl((0.0, 0.1, 0.1, 0.2))], [], AFTER_SHIFT_START, zone_of, 640, 640)

    assert [(item["entity_id"], item["origin"]) for item in items] == [("bin-4", "shift_start"), ("plate-1", "unknown")]


def test_tools_count_per_class():
    tracker = Tracker(SETTINGS)
    objects = [knife(), Detection("spoon", (0.6, 0.6, 0.7, 0.7), 0.9), knife((0.1, 0.5, 0.2, 0.6))]
    items = tracker.update(objects, [], 0, zone_of, 640, 640)

    assert sorted(item["entity_id"] for item in items) == ["tool-knife-1", "tool-knife-2", "tool-spoon-1"]


def test_an_unknown_detector_class_raises():
    with pytest.raises(ValueError, match="cup"):
        Tracker(SETTINGS).update([Detection("cup", KNIFE_BOX, 0.9)], [], 0, zone_of, 640, 640)


@pytest.mark.safety
def test_a_hand_needs_a_confident_wrist():
    tracker = Tracker(SETTINGS)
    items = tracker.update([], [person(left_wrist=(0.5, 0.5, 0.9), right_wrist=(0.6, 0.5, 0.4))], 0, zone_of, 640, 640)

    assert [item["entity_id"] for item in items] == ["hand-p1-left"]
    assert items[0]["kind"] == "hand"
    assert items[0]["class_name"] == "wrist"
    assert items[0]["person_id"] == "p1"
    assert items[0]["wrist"] == [0.5, 0.5, 0.9]
    assert items[0]["confidence"] == 0.9


@pytest.mark.safety
def test_a_reacquired_person_gets_new_hand_ids():
    tracker = Tracker(SETTINGS)
    both = person(right_wrist=(0.6, 0.5, 0.9))
    first = tracker.update([], [both], 0, zone_of, 640, 640)
    for frame in range(1, SETTINGS.track_lost_frames + 2):
        tracker.update([], [], frame * FRAME_MS, zone_of, 640, 640)
    again = tracker.update([], [both], AFTER_SHIFT_START, zone_of, 640, 640)

    assert [item["entity_id"] for item in first] == ["hand-p1-left", "hand-p1-right"]
    assert [item["entity_id"] for item in again] == ["hand-p2-left", "hand-p2-right"]
    assert all(item["origin"] == "unknown" for item in again)


@pytest.mark.safety
def test_a_hand_first_seen_after_shift_start_is_unknown_even_if_its_person_was_seen_before():
    tracker = Tracker(SETTINGS)
    tracker.update([], [person(left_wrist=UNSURE)], 0, zone_of, 640, 640)
    items = tracker.update([], [person()], AFTER_SHIFT_START, zone_of, 640, 640)

    assert items[0]["entity_id"] == "hand-p1-left"
    assert items[0]["origin"] == "unknown"


def test_the_hand_box_sits_beyond_the_wrist_away_from_the_elbow():
    box = hand_box((0.5, 0.5, 0.9), (0.5, 0.3, 0.9), SETTINGS, 640, 640)

    assert box == pytest.approx((0.46, 0.5, 0.54, 0.58))


def test_the_hand_box_is_centred_on_the_wrist_when_the_elbow_is_unsure():
    box = hand_box((0.5, 0.5, 0.9), (0.5, 0.3, 0.2), SETTINGS, 640, 640)

    assert box == pytest.approx((0.46, 0.46, 0.54, 0.54))


def test_the_hand_box_stays_square_on_a_wide_frame_and_is_clipped():
    box = hand_box((0.5, 0.5, 0.9), UNSURE, SETTINGS, 1280, 640)
    side_px = SETTINGS.hand_box_size * 1280

    assert (box[2] - box[0]) * 1280 == pytest.approx(side_px)
    assert (box[3] - box[1]) * 640 == pytest.approx(side_px)
    assert hand_box((0.0, 1.0, 0.9), UNSURE, SETTINGS, 640, 640) == pytest.approx((0.0, 0.96, 0.04, 1.0))


def test_the_hand_zone_comes_from_the_wrist_point():
    tracker = Tracker(SETTINGS)
    items = tracker.update([], [person(left_wrist=(0.9, 0.9, 0.9))], AFTER_SHIFT_START, zone_of, 640, 640)

    assert items[0]["zone"] == "clean-stack"
    assert items[0]["origin"] == "clean_stack"


def test_every_item_validates_against_observation_frame():
    tracker = Tracker(SETTINGS)
    people = [person(right_wrist=(0.6, 0.5, 0.9), left_elbow=(0.5, 0.3, 0.9))]
    items = tracker.update([knife(), bowl(), bowl((0.5, 0.6, 0.6, 0.7), 0.6)], people, 0, zone_of, 1280, 720)

    assert len(items) == 5
    validate({"frame_id": 0, "t_ms": 0, "camera_id": "cam0", "detections": items}, "observation_frame")


def test_output_order_is_deterministic():
    frames = [([knife(), bowl()], [person()]), ([bowl(), knife()], [person()])]

    assert run(Tracker(SETTINGS), frames) == run(Tracker(SETTINGS), frames)
    assert [item["track_id"] for item in run(Tracker(SETTINGS), frames)[1]] == [1, 2, 3]


# Each step: which of three knives (at fixed, non-overlapping spots) are visible, plus a gap of 0-6 empty frames.
LIFETIMES = st.lists(st.tuples(st.sets(st.integers(0, 2)), st.integers(0, 6)), min_size=1, max_size=25)
SPOTS = [(0.3, 0.3, 0.4, 0.4), (0.5, 0.3, 0.6, 0.4), (0.3, 0.6, 0.4, 0.7)]


@pytest.mark.safety
@settings(max_examples=100, deadline=None)
@given(LIFETIMES)
def test_entity_ids_are_never_reused_over_random_track_lifetimes(steps):
    tracker = Tracker(SETTINGS)
    track_of_entity = {}
    t_ms = 0
    for visible, gap in steps:
        objects = [knife(SPOTS[spot]) for spot in sorted(visible)]
        people = [person(right_wrist=(0.6, 0.5, 0.9))] if 0 in visible else []
        for item in tracker.update(objects, people, t_ms, zone_of, 640, 640):
            # One entity ID belongs to exactly one track for the whole shift: a new track is always a new entity.
            assert track_of_entity.setdefault(item["entity_id"], item["track_id"]) == item["track_id"]
        t_ms += FRAME_MS
        for _ in range(gap):
            tracker.update([], [], t_ms, zone_of, 640, 640)
            t_ms += FRAME_MS


@pytest.mark.safety
@settings(max_examples=100, deadline=None)
@given(st.lists(st.sampled_from(["knife", "spoon", "fork", "plate", "bin"]), max_size=40))
def test_identity_book_never_hands_out_an_id_twice(kinds):
    book = IdentityBook(SETTINGS.shift_start_ms)
    slot = Zone("slot-4", "bin_slot", [])
    seen = []
    for track_id, kind in enumerate(kinds, start=1):
        if kind == "bin":
            identity = book.object_identity(track_id, "bowl", AFTER_SHIFT_START, slot)
        elif kind == "plate":
            identity = book.object_identity(track_id, "bowl", AFTER_SHIFT_START, None)
        else:
            identity = book.object_identity(track_id, kind, AFTER_SHIFT_START, None)
        # Every track ends straight away, so only the bin slot may hand its ID out again.
        book.end_track(track_id)
        if identity.kind != "bin":
            assert identity.entity_id not in seen
        seen.append(identity.entity_id)
