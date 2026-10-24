"""Tests for kitchen/shared/fakes/scenarios.py: every golden scenario loads, and its timeline runs in time order.

Owner: platform (agents)
Step:  shared
Reads: contracts/scenarios/*.json
Emits: nothing
Rules: known-clean source (shift-start entities clean, late unknown entities unknown); late changes recheck
       everything (amendments wait on the timeline)
"""

import pytest

from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS, FakeStateClient, load_scenario, read_scenario, scenario_timeline
from kitchen.shared.models import validate

SCENARIO_DIR = GOLDEN_SCENARIO_PATHS[0].parent


def test_the_golden_scenarios_are_found():
    assert len(GOLDEN_SCENARIO_PATHS) >= 6


@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_every_golden_scenario_loads_into_the_fake(path):
    state = FakeStateClient()
    scenario = load_scenario(state, path)

    for ticket in scenario["tickets"]:
        assert state.get_ticket(ticket["ticket_id"]) == ticket
    for binding in scenario.get("plate_bindings", []):
        assert state.get_plate_binding(binding["plate_id"]).ticket_id == binding["ticket_id"]
    assert [event["event_id"] for event in state.list_interactions()] == sorted(
        event["event_id"] for event in scenario["events"]
    )
    for entity in state.list_entities():
        validate(entity, "entity")


def test_load_scenario_accepts_a_dict_too():
    scenario = read_scenario(SCENARIO_DIR / "knife-carries-peanut-to-plate.json")
    state = FakeStateClient()

    assert load_scenario(state, scenario) == scenario
    assert state.get_ticket("t1")["forbidden"] == ["peanut"]


def test_bins_carry_their_scenario_profiles():
    state = FakeStateClient()
    load_scenario(state, SCENARIO_DIR / "knife-carries-peanut-to-plate.json")

    bin_1 = state.get_entity("bin-1")
    assert bin_1["label"] == "satay sauce"
    assert bin_1["allergen_profile"]["probabilities"] == {"peanut": 0.98}


@pytest.mark.safety
def test_entities_a_scenario_only_mentions_start_clean_at_shift_start():
    """Known-clean source: the knife was there at shift start, so it starts clean."""
    state = FakeStateClient()
    load_scenario(state, SCENARIO_DIR / "knife-carries-peanut-to-plate.json")

    knife = state.get_entity("tool-knife-1")
    assert (knife["origin"], knife["first_seen_t_ms"]) == ("shift_start", 0)
    assert knife["allergen_profile"] == {"probabilities": {}, "unknown": False, "source": "shift_start_clean"}


@pytest.mark.safety
def test_late_entities_keep_their_origin():
    """Known-clean source: a mid-shift spoon has unknown history; a plate from the clean stack starts clean."""
    spoon_state = FakeStateClient()
    load_scenario(spoon_state, SCENARIO_DIR / "unknown-tool-mid-shift.json")
    spoon = spoon_state.get_entity("tool-spoon-9")
    assert (spoon["origin"], spoon["first_seen_t_ms"]) == ("unknown", 1500)
    assert spoon["allergen_profile"]["unknown"] is True

    plate_state = FakeStateClient()
    load_scenario(plate_state, SCENARIO_DIR / "clean-plate-from-stack.json")
    plate = plate_state.get_entity("plate-t6")
    assert plate["origin"] == "clean_stack"
    assert plate["allergen_profile"]["unknown"] is False


@pytest.mark.safety
def test_amendments_are_not_loaded_up_front():
    """Late changes recheck everything: revision 1 must still be judgeable before the amendment lands."""
    state = FakeStateClient()
    load_scenario(state, SCENARIO_DIR / "late-amendment-adds-dairy.json")

    assert state.get_ticket("t5")["revision"] == 1


def test_the_timeline_runs_in_time_order():
    timeline = scenario_timeline(SCENARIO_DIR / "late-amendment-adds-dairy.json")

    assert [(item.t_ms, item.kind) for item in timeline] == [
        (1000, "event"),
        (2000, "event"),
        (3000, "plate_at_pass"),
        (4000, "amendment"),
    ]
    assert timeline[3].record["revision"] == 2


def test_at_equal_times_edges_and_amendments_come_before_the_plate():
    """Every judgment call leans toward HOLD: a plate is judged with everything that happened up to that moment."""
    scenario = read_scenario(SCENARIO_DIR / "late-amendment-adds-dairy.json")
    scenario["amendments"][0]["created_t_ms"] = 3000
    scenario["events"][1].update(t_start_ms=3000, t_end_ms=3000)

    assert [item.kind for item in scenario_timeline(scenario)] == ["event", "event", "amendment", "plate_at_pass"]


def test_playing_the_amendment_from_the_timeline_bumps_the_ticket():
    path = SCENARIO_DIR / "late-amendment-adds-dairy.json"
    state = FakeStateClient()
    load_scenario(state, path)
    for item in scenario_timeline(path):
        if item.kind == "amendment":
            state.put_ticket(item.record)

    assert state.get_ticket("t5")["forbidden"] == ["milk", "peanut"]
