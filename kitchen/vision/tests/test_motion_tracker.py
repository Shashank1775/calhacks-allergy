"""Tests for the experimental motion tracker (VISION_TRACKER=motion) and the tracker comparison tool.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.tracking, kitchen.vision.compare_trackers
Emits: nothing
Rules: known-clean source: motion mode never hands one object another's identity when the pairing is ambiguous, a
       utensil relabelled by the detector or a pose wrist swap starts over with unknown history, and IDs are never
       reused; fail closed: a predicted box is used only to match, never emitted; the default tracker is unchanged
"""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kitchen.shared.models import Zone
from kitchen.vision.common import Detection, Person, SourceFrame, VisionSettings
from kitchen.vision.compare_trackers import compare, every_nth_ms, table
from kitchen.vision.tracking import Tracker

WIDTH, HEIGHT = 1920, 1080
FRAME_MS = 66  # 15 fps, the laptop demo's rate
AFTER_SHIFT_START = 10_000
MOTION = VisionSettings(tracker="motion", shift_start_ms=3000)
IOU = VisionSettings(shift_start_ms=3000)

RACK = Zone("rack", "clean_stack", [(0.75, 0.2), (1.0, 0.2), (1.0, 0.9), (0.75, 0.9)])
KNIFE_SIZE = (0.025, 0.12)  # about 48 x 130 px at 1080p
UNSURE = (0.0, 0.0, 0.0)


def zone_of(point):
    """A ZoneLookup with the rack on the right of the frame."""
    xs = [corner[0] for corner in RACK.polygon]
    ys = [corner[1] for corner in RACK.polygon]
    if min(xs) <= point[0] <= max(xs) and min(ys) <= point[1] <= max(ys):
        return RACK
    return None


def box_at(centre_x, centre_y, size=KNIFE_SIZE):
    width, height = size
    return (centre_x - width / 2, centre_y - height / 2, centre_x + width / 2, centre_y + height / 2)


def knife(centre_x, centre_y, class_name="knife", size=KNIFE_SIZE, score=0.9):
    return Detection(class_name, box_at(centre_x, centre_y, size), score)


def update(tracker, objects, t_ms, people=()):
    return tracker.update(list(objects), list(people), t_ms, zone_of, WIDTH, HEIGHT)


def entity_on(items, detection):
    """The item emitted for a detection (items keep the detection's own box)."""
    matches = [item for item in items if tuple(item["box"]) == tuple(float(v) for v in detection.box)]
    return matches[0] if matches else None


def test_the_default_tracker_is_iou_and_an_unknown_tracker_is_refused():
    assert VisionSettings().tracker == "iou"
    with pytest.raises(ValueError, match="VISION_TRACKER"):
        Tracker(VisionSettings(tracker="kalman"))


RAMP_S = 0.4  # a hand takes about this long to get a utensil from rest up to carrying speed


def carried_x(speed_fw_per_s, t_s):
    """Where an object picked up at x = 0.1 is after t_s: it speeds up evenly for RAMP_S, then keeps its speed."""
    if t_s < RAMP_S:
        return 0.1 + speed_fw_per_s * t_s * t_s / (2 * RAMP_S)
    return 0.1 + speed_fw_per_s * (t_s - RAMP_S / 2)


def carried_ids(settings, speed_fw_per_s, size=KNIFE_SIZE, class_name="knife", seconds=1.5):
    """The entity IDs one object gets while it is picked up and carried along x, at 15 fps."""
    tracker = Tracker(settings)
    ids = set()
    for frame in range(int(seconds * 1000 / FRAME_MS)):
        x = carried_x(speed_fw_per_s, frame * FRAME_MS / 1000)
        items = update(tracker, [knife(x, 0.5, class_name, size)], AFTER_SHIFT_START + frame * FRAME_MS)
        ids |= {item["entity_id"] for item in items}
    return ids


def test_a_carried_knife_keeps_one_identity_in_motion_mode():
    """Half a frame-width a second at 15 fps moves a knife more than its own width per frame: today's tracker loses it
    every frame, the motion tracker keeps it."""
    assert len(carried_ids(MOTION, 0.5)) == 1
    assert len(carried_ids(IOU, 0.5)) > 5


def test_a_carried_plate_keeps_one_identity_in_motion_mode():
    assert len(carried_ids(MOTION, 1.6, size=(0.15, 0.15), class_name="bowl")) == 1
    assert len(carried_ids(IOU, 1.6, size=(0.15, 0.15), class_name="bowl")) > 1


@pytest.mark.safety
def test_knives_side_by_side_in_the_rack_keep_their_identities():
    """Utensils lie close together in a rack. With jitter and missed frames they must keep their IDs (and their clean
    origin), not churn into unknown newcomers because a neighbour is within reach."""
    rng = np.random.default_rng(0)
    tracker = Tracker(MOTION)
    spots = [0.8, 0.83, 0.86]  # about 10 px between 48 px knives
    ids = {spot: set() for spot in spots}
    for frame in range(75):  # 5 s
        detections = []
        for spot in spots:
            if frame >= 5 and rng.random() < 0.1:
                continue  # a missed detection (each knife is seen at shift start first)
            jitter = rng.normal(0, 0.002, 2)
            detections.append((spot, knife(spot + jitter[0], 0.5 + jitter[1])))
        items = update(tracker, [detection for _, detection in detections], frame * FRAME_MS)
        for spot, detection in detections:
            ids[spot].add(entity_on(items, detection)["entity_id"])

    assert all(len(spot_ids) == 1 for spot_ids in ids.values())
    origins = {item["origin"] for item in items}
    assert origins == {"shift_start"}


@pytest.mark.safety
def test_a_still_knife_missed_for_a_frame_never_takes_a_new_neighbour():
    """A knife lies still in the rack. In the frame the detector misses it, another knife is put down beside it. The
    still knife hasn't gone anywhere, so the newcomer is a new entity, not the still knife (whose clean history it
    would otherwise take)."""
    tracker = Tracker(MOTION)
    for frame in range(5):
        update(tracker, [knife(0.80, 0.5)], frame * FRAME_MS)
    newcomer = knife(0.83, 0.5)
    items = update(tracker, [newcomer], 5 * FRAME_MS)
    back = update(tracker, [knife(0.80, 0.5), knife(0.83, 0.5)], 6 * FRAME_MS)

    assert entity_on(items, newcomer)["entity_id"] == "tool-knife-2"
    assert [item["entity_id"] for item in back] == ["tool-knife-1", "tool-knife-2"]


def carry_pair(tracker, frames, gap=0.04):
    """Two knives carried side by side to the right (picked up together), for `frames` frames from t = 0."""
    for frame in range(frames):
        x = carried_x(0.5, frame * FRAME_MS / 1000)
        update(tracker, [knife(x, 0.5), knife(x + gap, 0.5)], AFTER_SHIFT_START + frame * FRAME_MS)


@pytest.mark.safety
def test_a_detection_two_moving_tracks_could_take_is_taken_by_neither():
    """Two knives carried side by side are both missed for a frame; then one knife shows up between where both were
    heading. Either could be it, so neither track takes it: it starts as a new unknown entity."""
    tracker = Tracker(MOTION)
    carry_pair(tracker, 12)
    update(tracker, [], AFTER_SHIFT_START + 12 * FRAME_MS)
    between = knife(carried_x(0.5, 13 * FRAME_MS / 1000) + 0.02, 0.5)
    items = update(tracker, [between], AFTER_SHIFT_START + 13 * FRAME_MS)

    assert entity_on(items, between)["entity_id"] == "tool-knife-3"
    assert entity_on(items, between)["origin"] == "unknown"


@pytest.mark.safety
def test_a_moving_track_with_two_detections_in_reach_takes_neither():
    """A carried knife is missed for a frame; then two knives are in its reach. It could be either, so it takes
    neither."""
    tracker = Tracker(MOTION)
    for frame in range(12):
        update(tracker, [knife(carried_x(0.5, frame * FRAME_MS / 1000), 0.5)], AFTER_SHIFT_START + frame * FRAME_MS)
    update(tracker, [], AFTER_SHIFT_START + 12 * FRAME_MS)
    ahead = carried_x(0.5, 13 * FRAME_MS / 1000)
    left, right = knife(ahead - 0.02, 0.5), knife(ahead + 0.02, 0.5)
    items = update(tracker, [left, right], AFTER_SHIFT_START + 13 * FRAME_MS)

    assert {entity_on(items, left)["entity_id"], entity_on(items, right)["entity_id"]} == {
        "tool-knife-2",
        "tool-knife-3",
    }
    assert {item["origin"] for item in items} == {"unknown"}


def two_knives(clean_x, dirty_x, dirty_y=0.5, seconds=4.0):
    """A clean knife (seen from shift start) and a dirty one (first seen mid-shift), moving as clean_x(t_s) and
    dirty_x(t_s) along y = 0.5 and y = dirty_y. Returns every item emitted on the dirty knife's detection."""
    tracker = Tracker(MOTION)
    dirty_items = []
    start_ms = 4000  # the dirty knife turns up after shift start (3000), so it is unknown
    update(tracker, [knife(clean_x(0.0), 0.5)], 0)
    for frame in range(int(seconds * 1000 / FRAME_MS)):
        t_s = frame * FRAME_MS / 1000
        clean, dirty = knife(clean_x(t_s), 0.5), knife(dirty_x(t_s), dirty_y)
        items = update(tracker, [clean, dirty], start_ms + frame * FRAME_MS)
        dirty_items.append(entity_on(items, dirty))
    return [item for item in dirty_items if item is not None]


def still(x):
    return lambda t_s: x


def carried_from(x0, speed_fw_per_s):
    """Picked up at x0 and carried at speed (negative: to the left), speeding up over RAMP_S as a hand does."""
    return lambda t_s: x0 + carried_x(speed_fw_per_s, t_s) - 0.1


CROSSINGS = {
    "carried past a still knife": (still(0.5), carried_from(0.2, 0.25), 0.5),
    "carried past, a little lower": (still(0.5), carried_from(0.2, 0.25), 0.53),
    "head-on, both carried": (carried_from(0.7, -0.15), carried_from(0.3, 0.15), 0.5),
    "swap places": (carried_from(0.6, -0.1), carried_from(0.4, 0.1), 0.5),
}


@pytest.mark.safety
@pytest.mark.parametrize("name", list(CROSSINGS))
def test_a_dirty_knife_crossing_a_clean_one_never_takes_its_clean_identity(name):
    """Known-clean source: when a dirty knife passes over, crosses or swaps places with a clean one, the dirty knife may
    come out under a new unknown ID, but never under the clean knife's ID or any clean origin."""
    clean_x, dirty_x, dirty_y = CROSSINGS[name]
    dirty_items = two_knives(clean_x, dirty_x, dirty_y)

    assert dirty_items
    assert all(item["origin"] == "unknown" for item in dirty_items)
    assert all(item["entity_id"] != "tool-knife-1" for item in dirty_items)


def jerked(x0, speed_fw_per_s, start_s=1.0, travel=0.4):
    """Still at x0 until start_s, then moving at full speed straight away (a quick grab), for `travel`, then still."""

    def x(t_s):
        moved = min(max(t_s - start_s, 0.0) * abs(speed_fw_per_s), travel)
        return x0 + moved if speed_fw_per_s > 0 else x0 - moved

    return x


@pytest.mark.safety
def test_two_knives_swapping_places_never_take_each_others_identity():
    """Two clean knives are grabbed and each put down where the other lay, within a second. The spot each lands on
    still has the other knife's track (lost there, still alive), but the knife just carried in could be either, so it
    comes back unknown instead of taking the other knife's identity."""
    tracker = Tracker(MOTION)
    first = None
    last = {}
    for frame in range(45):
        t_s = frame * FRAME_MS / 1000
        a, b = knife(jerked(0.3, 0.5)(t_s), 0.5), knife(jerked(0.7, -0.5)(t_s), 0.5)
        items = update(tracker, [a, b], frame * FRAME_MS)
        last = {"a": entity_on(items, a), "b": entity_on(items, b)}
        first = first or {"a": last["a"]["entity_id"], "b": last["b"]["entity_id"]}

    assert first == {"a": "tool-knife-1", "b": "tool-knife-2"}
    assert last["a"]["entity_id"] != first["b"]
    assert last["b"]["entity_id"] != first["a"]


@pytest.mark.safety
def test_a_track_lost_while_moving_never_takes_whatever_is_where_it_was_lost():
    """A carried knife goes undetected for a few frames. Its object kept going, so a knife that turns up where it was
    last seen is another one: a new entity, not the carried knife's identity."""
    tracker = Tracker(MOTION)
    for frame in range(10):
        x = carried_x(0.5, frame * FRAME_MS / 1000)
        items = update(tracker, [knife(x, 0.5)], AFTER_SHIFT_START + frame * FRAME_MS)
    lost_at = x
    for frame in range(10, 14):
        update(tracker, [], AFTER_SHIFT_START + frame * FRAME_MS)
    other = knife(lost_at, 0.5)
    items = update(tracker, [other], AFTER_SHIFT_START + 14 * FRAME_MS)

    assert entity_on(items, other)["entity_id"] == "tool-knife-2"


def test_a_knife_grabbed_at_full_speed_settles_on_one_new_identity():
    """Grabbed and moved at full speed at once, a knife outruns its track on the first frame. The new track takes the
    lost track's motion (not its identity), so it follows the knife from then on instead of breaking every frame."""
    tracker = Tracker(MOTION)
    ids = []
    for frame in range(40):
        x = jerked(0.2, 0.5, start_s=0.5, travel=0.6)(frame * FRAME_MS / 1000)
        items = update(tracker, [knife(x, 0.5)], AFTER_SHIFT_START + frame * FRAME_MS)
        ids.append(items[0]["entity_id"])

    assert len(set(ids)) <= 2
    assert ids[-1] == ids[15]


def test_a_still_knife_lying_across_the_frame_keeps_one_identity_through_jitter_and_misses():
    """A horizontal knife's box is short: box jitter along its length must not count as moving (which would stop it
    being matched again where it lay after a missed frame or two)."""
    rng = np.random.default_rng(1)
    tracker = Tracker(MOTION)
    ids = set()
    for frame in range(150):
        if frame > 5 and rng.random() < 0.25:
            update(tracker, [], frame * FRAME_MS)
            continue
        jitter = rng.normal(0, [0.004, 0.0015])
        items = update(tracker, [knife(0.5 + jitter[0], 0.5 + jitter[1], size=(0.12, 0.025))], frame * FRAME_MS)
        ids |= {item["entity_id"] for item in items}

    assert ids == {"tool-knife-1"}


@pytest.mark.safety
def test_a_knife_relabelled_spoon_in_the_rack_is_unknown():
    """A dirty knife is carried into the rack; the next frame the detector calls it a spoon. Today the spoon is a new
    clean_stack entity. In motion mode a missing knife makes any new tool a possible switch: unknown."""
    tracker = Tracker(MOTION)
    for frame in range(10):
        x = 0.70 + frame * 0.01  # slid from the counter into the rack
        items = update(tracker, [knife(x, 0.5)], AFTER_SHIFT_START + frame * FRAME_MS)
    assert items[0]["entity_id"] == "tool-knife-1"
    assert items[0]["zone"] == "rack"
    assert items[0]["origin"] == "unknown"

    spoon = knife(0.79, 0.5, class_name="spoon")
    items = update(tracker, [spoon], AFTER_SHIFT_START + 10 * FRAME_MS)

    assert items[0]["entity_id"] == "tool-spoon-1"
    assert items[0]["zone"] == "rack"
    assert items[0]["origin"] == "unknown"


def test_the_default_tracker_still_lets_a_relabelled_spoon_start_clean():
    """The gap motion mode closes; the default keeps today's behavior until the switch is flipped."""
    tracker = Tracker(IOU)
    update(tracker, [knife(0.85, 0.5)], AFTER_SHIFT_START)
    items = update(tracker, [knife(0.85, 0.5, class_name="spoon")], AFTER_SHIFT_START + FRAME_MS)

    assert items[0]["origin"] == "clean_stack"


@pytest.mark.safety
def test_a_knife_lost_in_the_rack_taints_the_next_tool_of_any_class_there():
    """A knife vanishes in the rack (it may be hiding there). The next tool to appear in the rack is unknown even if
    the detector calls it a spoon."""
    tracker = Tracker(MOTION)
    update(tracker, [knife(0.85, 0.5)], AFTER_SHIFT_START)
    for frame in range(1, 20):  # longer than the lost buffer, so the knife's track ends in the rack
        update(tracker, [], AFTER_SHIFT_START + frame * FRAME_MS)
    items = update(tracker, [knife(0.85, 0.5, class_name="spoon")], AFTER_SHIFT_START + 20 * FRAME_MS)

    assert items[0]["zone"] == "rack"
    assert items[0]["origin"] == "unknown"


def person(left, right, box=(0.2, 0.2, 0.6, 0.9)):
    return Person(box, 0.9, (*left, 0.9), (*right, 0.9), UNSURE, UNSURE)


@pytest.mark.safety
def test_a_pose_wrist_swap_makes_both_hands_new_and_unknown():
    """Pose labels the left wrist as right and vice versa for a frame. Each hand would take the other's history, so
    both start over as new hands with unknown history."""
    tracker = Tracker(MOTION)
    first = update(tracker, [], 0, [person((0.30, 0.6), (0.50, 0.6))])
    swapped = update(tracker, [], FRAME_MS, [person((0.50, 0.6), (0.30, 0.6))])

    assert [(item["entity_id"], item["origin"]) for item in first] == [
        ("hand-p1-left", "shift_start"),
        ("hand-p1-right", "shift_start"),
    ]
    assert [(item["entity_id"], item["origin"]) for item in swapped] == [
        ("hand-p1-left-2", "unknown"),
        ("hand-p1-right-2", "unknown"),
    ]


def test_hands_moving_normally_keep_their_identities():
    tracker = Tracker(MOTION)
    ids = set()
    for frame in range(30):
        shift = frame * 0.004
        items = update(tracker, [], frame * FRAME_MS, [person((0.30 + shift, 0.6), (0.50 + shift, 0.6))])
        ids |= {item["entity_id"] for item in items}

    assert ids == {"hand-p1-left", "hand-p1-right"}


SPOTS = [(0.3, 0.3), (0.5, 0.3), (0.3, 0.6)]


@pytest.mark.safety
@settings(max_examples=100, deadline=None)
@given(
    st.lists(st.tuples(st.sets(st.integers(0, 2)), st.floats(-0.04, 0.04), st.integers(0, 6)), min_size=1, max_size=25)
)
def test_motion_mode_never_emits_a_predicted_box_and_never_reuses_an_id(steps):
    """Fail closed: every emitted box is one of this frame's detections; known-clean source: one entity ID belongs to
    one track for the whole shift."""
    tracker = Tracker(MOTION)
    track_of_entity = {}
    t_ms = 0
    drift = 0.0
    for visible, step, gap in steps:
        drift += step
        objects = [knife(SPOTS[spot][0] + drift, SPOTS[spot][1]) for spot in sorted(visible)]
        boxes = {tuple(float(v) for v in detection.box) for detection in objects}
        for item in update(tracker, objects, t_ms):
            assert tuple(item["box"]) in boxes
            assert track_of_entity.setdefault(item["entity_id"], item["track_id"]) == item["track_id"]
        t_ms += FRAME_MS
        for _ in range(gap):
            update(tracker, [], t_ms)
            t_ms += FRAME_MS


class CarriedKnifeDetector:
    """A fake detector: one knife picked up and carried right at half a frame-width a second, read off the frame's own
    time."""

    def detect(self, frame):
        return [knife(carried_x(0.5, frame.t_ms / 1000), 0.5)], []


def test_compare_trackers_runs_both_trackers_on_the_same_detections():
    frames = [SourceFrame(index, index * FRAME_MS, np.zeros((HEIGHT, WIDTH, 3), np.uint8)) for index in range(20)]
    tallies, frame_count, duration_ms = compare(frames, CarriedKnifeDetector(), zone_of, IOU)

    assert frame_count == 20
    assert duration_ms == 19 * FRAME_MS
    assert len(tallies["motion"]["knife"].entity_ids) == 1
    assert len(tallies["iou"]["knife"].entity_ids) > 1
    assert tallies["iou"]["knife"].frames_seen == 20
    report = table(tallies, frame_count, duration_ms)
    assert "| knife | iou |" in report
    assert "| knife | motion |" in report


def test_every_nth_ms_keeps_the_asked_frame_rate():
    frames = [SourceFrame(index, index * 33, np.zeros((2, 2, 3), np.uint8)) for index in range(30)]
    kept = list(every_nth_ms(frames, 10))

    # One frame per 100 ms of video time: the first frame at or after each 100 ms mark.
    assert [frame.t_ms for frame in kept] == [0, 132, 231, 330, 429, 528, 627, 726, 825, 924]
