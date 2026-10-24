"""Zone rules on the hand-written scenes, at 15, 30 and 60 FPS, plus the wash and version rules.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.zone_rules, kitchen.interactions.events.replay, scenes
Emits: pytest tests
Rules: 1 a wash needs the wash zone, a long dwell and a self pair; a brief pass is no wash; 7 zone rules need nothing
       but features, so they always answer
"""

import pytest

from kitchen.interactions.events import replay
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import KNIFE, SCENES, ZONES, unmatched
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
