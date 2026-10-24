"""Zone rules on the hand-written scenes, at 15, 30 and 60 FPS, plus the wash and version rules.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.zone_rules, kitchen.interactions.events.replay, scenes
Emits: pytest tests
Rules: 1 a wash needs the wash zone, a long dwell and a self pair; a brief pass is no wash; a real touch counts at any
       frame rate; 7 zone rules need nothing but features, so they always answer
"""

import pytest

from kitchen.interactions.events import replay
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import (
    BIN_AT,
    KNIFE,
    PLATE_AT,
    SCENES,
    ZONES,
    Track,
    render,
    still,
    unmatched,
)
from kitchen.interactions.zone_rules import RULES_VERSION, ZoneRules
from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.interfaces import PairWindow

SETTINGS = InteractionSettings()


def window_of(rows: list[dict]) -> PairWindow:
    """A features-only window, as zone rules see it."""
    return PairWindow(a_id=rows[-1]["a_id"], b_id=rows[-1]["b_id"], features=rows, clip=None, tensor=None)


def held_rows(base: dict, duration_ms: int, step_ms: int = 33) -> list[dict]:
    """The same feature row about every step_ms, the last exactly duration_ms after the first, dwell advancing too."""
    offsets = [*range(0, duration_ms, step_ms), duration_ms]
    return [{**base, "t_ms": base["t_ms"] + t, "dwell_ms": base["dwell_ms"] + t} for t in offsets]


@pytest.mark.parametrize("fps", [15, 30, 60])
@pytest.mark.parametrize("name", sorted(SCENES))
def test_rules_fixtures_give_the_expected_events(name, fps):
    scene = SCENES[name]

    events = replay(scene.frames(fps), ZONES, SETTINGS)

    assert unmatched(events, scene.expected) == []


# Out of every zone, and too far from the plate and the bin to be a candidate with either.
AWAY = (0.65, 0.2)


def tap(target: tuple[float, float], start_ms: float, length_ms: int, scene_ms: int = 2000) -> Track:
    """The knife jumps onto `target` at start_ms, stays length_ms and jumps back: a touch with no approach, so the
    pair is first seen already overlapping, the hardest case for a low frame rate."""
    end_ms = start_ms + length_ms
    keys = ((0, AWAY), (start_ms - 0.001, AWAY), (start_ms, target), (end_ms, target), (end_ms + 0.001, AWAY))
    return Track(KNIFE, "tool", (*keys, (scene_ms, AWAY)))


def tap_phases(fps: int) -> list[float]:
    """Ten start times spread over one frame interval, off the sample times, so every way a tap can fall between
    frames is tried."""
    return [500 + phase * (1000 / fps) / 10 + 0.5 for phase in range(10)]


@pytest.mark.safety
@pytest.mark.parametrize("fps", [10, 15, 30, 60])
def test_a_tap_just_over_contact_min_ms_is_a_contact_at_any_frame_rate(fps):
    """A 220 ms knife tap on a plate is a contact (contact_min_ms is 200) whatever the frame rate and however the tap
    falls between frames: the overlap is counted from one frame before it was first seen, when it may have begun."""
    for start_ms in tap_phases(fps):
        frames = render([still("plate-t1", "plate", PLATE_AT, 2000), tap(PLATE_AT, start_ms, 220)], 2000, fps)

        touches = [event for event in replay(frames, ZONES, SETTINGS) if event["action"] == "transfer.contact"]

        assert len(touches) == 1, (fps, start_ms)


@pytest.mark.safety
@pytest.mark.parametrize("fps", [10, 15, 30, 60])
def test_a_dip_just_over_scoop_min_ms_is_a_scoop_at_any_frame_rate(fps):
    """A 340 ms dip into the bin is a scoop (scoop_min_ms is 300) at 10, 15, 30 and 60 FPS, at every phase."""
    for start_ms in tap_phases(fps):
        frames = render([still("bin-1", "bin", BIN_AT, 2000), tap(BIN_AT, start_ms, 340)], 2000, fps)

        scoops = [event for event in replay(frames, ZONES, SETTINGS) if event["action"] == "transfer.scoop"]

        assert len(scoops) == 1, (fps, start_ms)


def test_rules_fixtures_scoop_comes_before_the_plate_contact():
    events = replay(SCENES["scoop_then_plate"].frames(), ZONES, SETTINGS)

    scoop = next(event for event in events if event["action"] == "transfer.scoop")
    plate = next(event for event in events if event["b_id"] == "plate-t1")
    assert scoop["t_end_ms"] < plate["t_start_ms"]
    assert (scoop["a_id"], scoop["b_id"], scoop["direction"]) == (KNIFE, "bin-1", "b_to_a")


@pytest.mark.safety
def test_rules_fixtures_near_miss_and_brief_wash_make_no_event_at_all():
    for name in ("pass_over", "hover", "brief_wash"):
        assert replay(SCENES[name].frames(), ZONES, SETTINGS) == [], name


@pytest.mark.safety
def test_a_wash_needs_the_full_dwell_in_the_wash_zone():
    rules = ZoneRules(SETTINGS)
    washing = {
        **contract_fixture("interaction_features", "knife-in-bin"),
        "b_id": KNIFE,
        "a_id": KNIFE,
        "b_kind": "tool",
        "zone_kind": "wash",
        "dwell_ms": 0,
    }

    short = held_rows(washing, SETTINGS.wash_min_dwell_ms - 100)
    long = held_rows(washing, SETTINGS.wash_min_dwell_ms)

    assert rules.classify(window_of(short)) == [("unimportant", 1.0)]
    assert rules.classify(window_of(long)) == [("clean.wash", SETTINGS.wash_probability)]
    assert SETTINGS.wash_probability >= SETTINGS.wash_floor


@pytest.mark.safety
def test_only_a_self_pair_can_be_a_wash():
    rules = ZoneRules(SETTINGS)
    in_sink_together = {**contract_fixture("interaction_features", "knife-in-bin"), "zone_kind": "wash", "dwell_ms": 0}
    plate_alone = {**in_sink_together, "a_id": "plate-t1", "b_id": "plate-t1", "a_kind": "plate", "b_kind": "plate"}

    pair_labels = rules.classify(window_of(held_rows(in_sink_together, 2 * SETTINGS.wash_min_dwell_ms)))
    plate_labels = rules.classify(window_of(held_rows(plate_alone, 2 * SETTINGS.wash_min_dwell_ms)))

    assert all(action != "clean.wash" for action, _ in pair_labels)
    assert plate_labels == [("unimportant", 1.0)]  # plates aren't washed mid-shift


def test_a_touch_on_both_sides_of_a_loss_counts_only_the_time_it_was_seen():
    """A hand on a bin is seen for 66 ms, lost for 500 ms (longer than pair_gap_ms, within track_gap_ms), then seen
    again. Its window bridges the loss, and zone rules count both parts plus one frame interval, never the loss: 198
    ms seen is no contact, 231 ms is."""
    hand_in_bin = {
        **contract_fixture("interaction_features", "knife-in-bin"),
        "a_id": "hand-p1-right",
        "a_kind": "hand",
    }
    times = [0, 33, 66, 566, 599, 632, 665]

    def labels_at(seen_times: list[int]) -> list:
        return ZoneRules(SETTINGS).classify(window_of([{**hand_in_bin, "t_ms": t_ms} for t_ms in seen_times]))

    assert labels_at(times) == [("unimportant", 1.0)]
    assert labels_at([*times, 698]) == [("transfer.contact", SETTINGS.contact_probability)]


def test_a_hand_in_a_bin_is_a_contact_not_a_scoop():
    hand_in_bin = {
        **contract_fixture("interaction_features", "knife-in-bin"),
        "a_id": "hand-p1-right",
        "a_kind": "hand",
    }

    labels = ZoneRules(SETTINGS).classify(window_of(held_rows(hand_in_bin, 500)))

    assert labels == [("transfer.contact", SETTINGS.contact_probability)]


def test_a_knife_held_in_its_bin_is_a_scoop_and_a_contact():
    knife_in_bin = contract_fixture("interaction_features", "knife-in-bin")

    labels = ZoneRules(SETTINGS).classify(window_of(held_rows(knife_in_bin, 500)))

    assert labels == [
        ("transfer.scoop", SETTINGS.scoop_probability),
        ("transfer.contact", SETTINGS.contact_probability),
    ]


def test_version_encodes_every_constant():
    default = ZoneRules(InteractionSettings()).version

    assert default.startswith(f"{RULES_VERSION}-")
    assert ZoneRules(InteractionSettings()).version == default
    assert ZoneRules(InteractionSettings(contact_min_ms=250)).version != default
    assert ZoneRules(InteractionSettings(wash_min_dwell_ms=4000)).version != default
