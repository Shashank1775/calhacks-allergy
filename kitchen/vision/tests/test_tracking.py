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

AFTER_SHIFT_START = 10_000
FRAME_MS = 33
LOST_FRAMES = 3  # the lost buffer, in test frames: a track missed for this many frames still matches the next one
SETTINGS = VisionSettings(track_lost_ms=(LOST_FRAMES + 1) * FRAME_MS, shift_start_ms=3000)

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

    owners, _, _ = tracks.update([KNIFE_BOX], [0.3], 0)
    assert owners == {}

    owners, _, _ = tracks.update([KNIFE_BOX], [0.9], FRAME_MS)
    assert owners == {0: 1}

    owners, _, _ = tracks.update([KNIFE_BOX], [0.3], 2 * FRAME_MS)
    assert owners == {0: 1}


def test_a_detection_below_the_match_iou_starts_a_new_track():
    counter = iter(range(1, 100))
    tracks = TrackSet(SETTINGS, lambda: next(counter))
    tracks.update([(0.3, 0.3, 0.4, 0.4)], [0.9], 0)

    # IoU 1/3 with the track's box is above track_match_iou, so it matches; IoU 0.18 is below, so a new track.
    owners, _, _ = tracks.update([(0.3, 0.35, 0.4, 0.45)], [0.9], FRAME_MS)
    assert owners == {0: 1}
    owners, _, _ = tracks.update([(0.3, 0.42, 0.4, 0.52)], [0.9], 2 * FRAME_MS)
    assert owners == {0: 2}


@pytest.mark.safety
def test_a_far_away_knife_does_not_take_a_lost_knifes_entity():
    tracker = Tracker(SETTINGS)
    first = tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)
    tracker.update([], [], AFTER_SHIFT_START + FRAME_MS, zone_of, 640, 640)
    # Still inside the lost buffer, but nowhere near the lost knife: a different knife, so a new unknown entity.
    again = tracker.update([knife(KNIFE_BOX)], [], AFTER_SHIFT_START + 2 * FRAME_MS, zone_of, 640, 640)

    assert first[0]["origin"] == "clean_stack"
    assert len(again) == 1
    assert again[0]["track_id"] != first[0]["track_id"]
    assert again[0]["entity_id"] == "tool-knife-2"
    assert again[0]["origin"] == "unknown"


def test_a_track_matched_within_the_lost_buffer_keeps_its_id():
    tracker = Tracker(SETTINGS)
    outputs = run(tracker, [([knife()], [])] + gone(LOST_FRAMES) + [([knife()], [])])

    assert outputs[0][0]["entity_id"] == outputs[-1][0]["entity_id"] == "tool-knife-1"
    assert outputs[0][0]["track_id"] == outputs[-1][0]["track_id"]


@pytest.mark.safety
def test_a_lost_track_emits_nothing():
    tracker = Tracker(SETTINGS)
    outputs = run(tracker, [([knife()], [])] + gone(LOST_FRAMES))

    assert len(outputs[0]) == 1
    assert all(items == [] for items in outputs[1:])


@pytest.mark.safety
def test_reacquired_after_the_lost_buffer_is_a_new_entity_with_unknown_origin():
    tracker = Tracker(SETTINGS)
    first = tracker.update([knife()], [], 0, zone_of, 640, 640)
    for frame in range(1, LOST_FRAMES + 2):
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
    outputs = run(tracker, [([bowl()], [])] + gone(LOST_FRAMES + 1))
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
    for frame in range(1, LOST_FRAMES + 2):
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


def test_the_hand_box_stays_square_on_a_wide_frame_and_is_shifted_inside_at_the_edge():
    box = hand_box((0.5, 0.5, 0.9), UNSURE, SETTINGS, 1280, 640)
    side_px = SETTINGS.hand_box_size * 1280

    assert (box[2] - box[0]) * 1280 == pytest.approx(side_px)
    assert (box[3] - box[1]) * 640 == pytest.approx(side_px)
    assert hand_box((0.0, 1.0, 0.9), UNSURE, SETTINGS, 640, 640) == pytest.approx((0.0, 0.92, 0.08, 1.0))


@pytest.mark.safety
def test_a_hand_at_the_frame_edge_keeps_its_full_box():
    # The elbow is inward, so the box centre lands past the edge; clipping it there would leave a zero-area box.
    assert hand_box((1.0, 0.5, 0.9), (0.8, 0.5, 0.9), SETTINGS, 640, 640) == pytest.approx((0.92, 0.46, 1.0, 0.54))
    assert hand_box((0.5, 1.0, 0.9), (0.5, 0.8, 0.9), SETTINGS, 1280, 720) == pytest.approx(
        (0.46, 1.0 - 0.08 * 1280 / 720, 0.54, 1.0)
    )


UNIT = st.floats(0.0, 1.0)
CONFIDENT = st.floats(SETTINGS.wrist_min_confidence, 1.0)


@pytest.mark.safety
@settings(max_examples=200, deadline=None)
@given(
    st.tuples(UNIT, UNIT, CONFIDENT),
    st.tuples(UNIT, UNIT, CONFIDENT),
    st.sampled_from([(640, 640), (1280, 720), (720, 1280), (640, 480)]),
)
def test_a_confident_hand_box_has_area_stays_in_frame_and_holds_the_wrist(wrist, elbow, size):
    width, height = size
    x1, y1, x2, y2 = hand_box(wrist, elbow, SETTINGS, width, height)

    assert x2 - x1 > 0
    assert y2 - y1 > 0
    assert 0.0 <= x1 and x2 <= 1.0
    assert 0.0 <= y1 and y2 <= 1.0
    tolerance = 1e-9
    assert x1 - tolerance <= wrist[0] <= x2 + tolerance
    assert y1 - tolerance <= wrist[1] <= y2 + tolerance


def test_the_hand_zone_comes_from_the_wrist_point():
    tracker = Tracker(SETTINGS)
    items = tracker.update([], [person(left_wrist=(0.9, 0.9, 0.9))], AFTER_SHIFT_START, zone_of, 640, 640)

    assert items[0]["zone"] == "clean-stack"
    # A hand never comes out of a clean stack, so reaching into one doesn't make it clean.
    assert items[0]["origin"] == "unknown"


@pytest.mark.safety
def test_a_reacquired_hand_first_seen_in_the_clean_stack_is_unknown():
    tracker = Tracker(SETTINGS)
    first = tracker.update([], [person()], 0, zone_of, 640, 640)
    for frame in range(1, LOST_FRAMES + 3):
        tracker.update([], [], frame * FRAME_MS, zone_of, 640, 640)
    again = tracker.update([], [person(left_wrist=(0.9, 0.9, 0.9))], AFTER_SHIFT_START, zone_of, 640, 640)

    assert first[0]["origin"] == "shift_start"
    assert [(item["entity_id"], item["zone"], item["origin"]) for item in again] == [
        ("hand-p2-left", "clean-stack", "unknown")
    ]


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
    book = IdentityBook(SETTINGS.shift_start_ms, SETTINGS.track_lost_ms)
    slot = Zone("slot-4", "bin_slot", [])
    seen = []
    for track_id, kind in enumerate(kinds, start=1):
        if kind == "bin":
            identity = book.object_identity(track_id, "bowl", AFTER_SHIFT_START, slot, maybe_switched=False)
        elif kind == "plate":
            identity = book.object_identity(track_id, "bowl", AFTER_SHIFT_START, None, maybe_switched=False)
        else:
            identity = book.object_identity(track_id, kind, AFTER_SHIFT_START, None, maybe_switched=False)
        # Every track ends straight away, so only the bin slot may hand its ID out again.
        book.end_track(track_id)
        if identity.kind != "bin":
            assert identity.entity_id not in seen
        seen.append(identity.entity_id)


def lose_all(tracker, start_ms):
    """Run enough empty frames from start_ms to end every live track."""
    for frame in range(LOST_FRAMES + 2):
        tracker.update([], [], start_ms + frame * FRAME_MS, zone_of, 640, 640)


@pytest.mark.safety
def test_an_object_lost_inside_the_clean_stack_comes_back_unknown():
    """Known-clean source: a knife that vanished inside the rack may be a dirty one put back and hidden by a hand, so
    the next knife that turns up in the rack has unknown history instead of starting clean."""
    tracker = Tracker(SETTINGS)
    tracker.update([knife(KNIFE_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)  # a dirty knife, out on the counter
    tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + FRAME_MS, zone_of, 640, 640)  # put in the rack
    lose_all(tracker, AFTER_SHIFT_START + 2 * FRAME_MS)  # a hand covers it until its track ends

    again = tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + 1000, zone_of, 640, 640)

    assert again[0]["zone"] == "clean-stack"
    assert again[0]["origin"] == "unknown"


@pytest.mark.safety
def test_each_loss_in_the_clean_stack_taints_one_newcomer_of_that_class():
    tracker = Tracker(SETTINGS)
    tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)
    lose_all(tracker, AFTER_SHIFT_START + FRAME_MS)

    first = tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + 1000, zone_of, 640, 640)
    lose_all(tracker, AFTER_SHIFT_START + 1000 + FRAME_MS)
    # Losing the newcomer in the rack too taints the next one again.
    second = tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + 2000, zone_of, 640, 640)
    spoon = tracker.update([Detection("spoon", CLEAN_STACK_BOX, 0.9)], [], AFTER_SHIFT_START + 2100, zone_of, 640, 640)

    assert first[0]["origin"] == "unknown"
    assert second[0]["origin"] == "unknown"
    assert spoon[0]["origin"] == "clean_stack"  # a spoon is not the knife that went missing


def test_an_object_lost_outside_the_clean_stack_does_not_taint_it():
    tracker = Tracker(SETTINGS)
    tracker.update([knife(KNIFE_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)
    lose_all(tracker, AFTER_SHIFT_START + FRAME_MS)

    newcomer = tracker.update([knife(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + 1000, zone_of, 640, 640)

    assert newcomer[0]["origin"] == "clean_stack"


@pytest.mark.safety
def test_a_plate_lost_in_the_clean_stack_taints_the_next_plate_there():
    tracker = Tracker(SETTINGS)
    tracker.update([bowl(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)
    lose_all(tracker, AFTER_SHIFT_START + FRAME_MS)

    again = tracker.update([bowl(CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + 1000, zone_of, 640, 640)

    assert again[0]["kind"] == "plate"
    assert again[0]["origin"] == "unknown"


SHIFTED_IN_STACK_BOX = (0.80, 0.80, 0.88, 0.88)  # still in the clean stack, but too far from CLEAN_STACK_BOX to match


def detection_of(class_name, box):
    return Detection(class_name, box, 0.9)


@pytest.mark.safety
@pytest.mark.parametrize("class_name", ["knife", "bowl"])
def test_an_object_carried_fast_into_the_clean_stack_is_unknown(class_name):
    """Known-clean source: a dirty knife (or plate) moved into the rack between two frames has no IoU with its old box,
    so it comes back as a new track there while its old track is lost. It must not start clean."""
    tracker = Tracker(SETTINGS)
    dirty = tracker.update([detection_of(class_name, KNIFE_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)
    moved = tracker.update(
        [detection_of(class_name, CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + FRAME_MS, zone_of, 640, 640
    )

    assert dirty[0]["origin"] == "unknown"
    assert moved[0]["track_id"] != dirty[0]["track_id"]
    assert moved[0]["zone"] == "clean-stack"
    assert moved[0]["origin"] == "unknown"


@pytest.mark.safety
@pytest.mark.parametrize("class_name", ["knife", "bowl"])
def test_an_object_hidden_in_the_clean_stack_and_uncovered_to_one_side_is_unknown(class_name):
    """A hand covers an object in the rack and lets go a little to one side: a new track, while the old one is still
    within its lost buffer. The newcomer may be that object (or a dirty one swapped in), so it can't start clean."""
    tracker = Tracker(SETTINGS)
    tracker.update([detection_of(class_name, CLEAN_STACK_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)
    tracker.update([], [], AFTER_SHIFT_START + FRAME_MS, zone_of, 640, 640)  # covered by a hand
    uncovered = tracker.update(
        [detection_of(class_name, SHIFTED_IN_STACK_BOX)], [], AFTER_SHIFT_START + 2 * FRAME_MS, zone_of, 640, 640
    )

    assert uncovered[0]["zone"] == "clean-stack"
    assert uncovered[0]["track_id"] == 2
    assert uncovered[0]["origin"] == "unknown"


def test_a_newcomer_to_the_clean_stack_with_no_lost_track_of_its_class_starts_clean():
    tracker = Tracker(SETTINGS)
    tracker.update([knife(KNIFE_BOX)], [], AFTER_SHIFT_START, zone_of, 640, 640)
    # The knife on the counter is still matched, so the spoon arriving in the rack can't be it.
    items = tracker.update(
        [knife(KNIFE_BOX), detection_of("spoon", CLEAN_STACK_BOX)], [], AFTER_SHIFT_START + FRAME_MS, zone_of, 640, 640
    )

    assert [(item["entity_id"], item["origin"]) for item in items] == [
        ("tool-knife-1", "unknown"),
        ("tool-spoon-1", "clean_stack"),
    ]


@pytest.mark.safety
def test_an_id_switch_before_shift_start_does_not_start_clean():
    """Before shift start a new track is normally shift_start, but one that starts while a knife's track is lost may
    be that knife under a new ID, carrying whatever it touched."""
    tracker = Tracker(SETTINGS)
    first = tracker.update([knife(KNIFE_BOX)], [], 0, zone_of, 640, 640)
    jumped = tracker.update([knife((0.1, 0.5, 0.2, 0.6))], [], FRAME_MS, zone_of, 640, 640)

    assert first[0]["origin"] == "shift_start"
    assert jumped[0]["entity_id"] == "tool-knife-2"
    assert jumped[0]["origin"] == "unknown"


@pytest.mark.safety
def test_a_person_switch_before_shift_start_gives_hands_unknown_history():
    tracker = Tracker(SETTINGS)
    first = tracker.update([], [person()], 0, zone_of, 640, 640)
    moved = Person((0.0, 0.0, 0.2, 0.2), 0.9, (0.1, 0.1, 0.9), UNSURE, UNSURE, UNSURE)  # no overlap with the old box
    again = tracker.update([], [moved], FRAME_MS, zone_of, 640, 640)

    assert first[0]["origin"] == "shift_start"
    assert [(item["entity_id"], item["origin"]) for item in again] == [("hand-p2-left", "unknown")]


@pytest.mark.safety
def test_a_hand_unseen_longer_than_the_lost_buffer_comes_back_as_a_new_unknown_hand():
    """The body stays tracked, but the wrist is out of sight long enough to touch anything: a new hand, not the clean
    one it was."""
    tracker = Tracker(SETTINGS)
    first = tracker.update([], [person()], 0, zone_of, 640, 640)
    for frame in range(1, LOST_FRAMES + 2):
        tracker.update([], [person(left_wrist=UNSURE)], frame * FRAME_MS, zone_of, 640, 640)
    again = tracker.update([], [person()], (LOST_FRAMES + 2) * FRAME_MS, zone_of, 640, 640)
    later = tracker.update([], [person()], (LOST_FRAMES + 3) * FRAME_MS, zone_of, 640, 640)

    assert [(item["entity_id"], item["origin"]) for item in first] == [("hand-p1-left", "shift_start")]
    assert [(item["entity_id"], item["person_id"], item["origin"]) for item in again] == [
        ("hand-p1-left-2", "p1", "unknown")
    ]
    assert later[0]["entity_id"] == "hand-p1-left-2"


def test_a_hand_unseen_within_the_lost_buffer_keeps_its_id():
    tracker = Tracker(SETTINGS)
    tracker.update([], [person()], 0, zone_of, 640, 640)
    for frame in range(1, LOST_FRAMES + 1):
        tracker.update([], [person(left_wrist=UNSURE)], frame * FRAME_MS, zone_of, 640, 640)
    again = tracker.update([], [person()], (LOST_FRAMES + 1) * FRAME_MS, zone_of, 640, 640)

    assert [(item["entity_id"], item["origin"]) for item in again] == [("hand-p1-left", "shift_start")]


@pytest.mark.safety
def test_a_camera_stall_longer_than_the_lost_buffer_ends_every_track():
    """The lost buffer is time, not frames: when frames stop for longer than it, the knife seen next is a new entity
    with unknown history, even though no frame in between missed it."""
    tracker = Tracker(SETTINGS)
    first = tracker.update([knife()], [], AFTER_SHIFT_START, zone_of, 640, 640)
    after_stall = tracker.update([knife()], [], AFTER_SHIFT_START + SETTINGS.track_lost_ms + 1, zone_of, 640, 640)

    assert first[0]["entity_id"] == "tool-knife-1"
    assert after_stall[0]["entity_id"] == "tool-knife-2"
    assert after_stall[0]["origin"] == "unknown"
