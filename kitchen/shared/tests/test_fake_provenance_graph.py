"""Tests for FakeProvenanceGraph beyond the shared contract suite: fail-closed answers and time cut-offs.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.fakes.provenance_graph, contracts/scenarios/*.json
Emits: nothing
Rules: fail closed (an unknown plate raises, ground truth reports probability 1); time runs forward (nothing reaches
       a plate before its chain has happened)
"""

import json

import pytest

from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS, FakeProvenanceGraph
from kitchen.shared.interfaces import GraphUnavailable

SCENARIO_DIR = GOLDEN_SCENARIO_PATHS[0].parent
KNIFE = SCENARIO_DIR / "knife-carries-peanut-to-plate.json"


def test_reach_gives_the_chain_from_the_bin_to_the_plate():
    (peanut,) = FakeProvenanceGraph(KNIFE).reach("plate-t1", 3000)

    assert peanut["allergen_unit_id"] == "peanut"
    assert peanut["probability"] == 1.0
    assert [(step["from_id"], step["to_id"], step["event_id"]) for step in peanut["chain"]] == [
        ("bin-1", "tool-knife-1", "e1"),
        ("tool-knife-1", "plate-t1", "e2"),
    ]


@pytest.mark.safety
def test_a_plate_the_scenario_doesnt_cover_raises_rather_than_looking_clean():
    graph = FakeProvenanceGraph(KNIFE)
    with pytest.raises(GraphUnavailable):
        graph.reach("plate-t99", 3000)
    with pytest.raises(GraphUnavailable):
        graph.unknown_ancestors("plate-t99", 3000)


@pytest.mark.safety
def test_nothing_reaches_the_plate_before_its_chain_has_happened():
    """Time runs forward: as of 1500 the knife has scooped but not yet touched the plate."""
    graph = FakeProvenanceGraph(KNIFE)

    assert graph.reach("plate-t1", 1500) == []
    assert [reach["allergen_unit_id"] for reach in graph.reach("plate-t1", 2000)] == ["peanut"]


def test_unknown_ancestors_appear_once_the_entity_is_seen():
    """A bin nobody declared, first seen mid-shift, is the one unknown ancestor left (a tool of unknown origin starts
    clean). The fake names it once a scenario expecting an unknown_history hold has seen it."""
    scenario = json.loads((SCENARIO_DIR / "unknown-tool-mid-shift.json").read_text())
    scenario["late_entities"] = [{"entity_id": "bin-9", "first_seen_t_ms": 1500, "origin": "unknown"}]
    scenario["events"][0]["a_id"] = "bin-9"
    scenario["expected"][0].update(decision="hold", allergens_reached=["sesame"], reason_codes=["unknown_history"])
    graph = FakeProvenanceGraph(scenario)

    assert graph.unknown_ancestors("plate-t4", 1000) == []
    assert graph.unknown_ancestors("plate-t4", 3000) == ["bin-9"]


@pytest.mark.safety
def test_set_unavailable_lasts_until_turned_off():
    graph = FakeProvenanceGraph(KNIFE)
    graph.set_unavailable(True)
    with pytest.raises(GraphUnavailable):
        graph.reach("plate-t1", 3000)

    graph.set_unavailable(False)
    assert len(graph.reach("plate-t1", 3000)) == 1


def test_version_names_the_fake_and_its_scenario():
    assert FakeProvenanceGraph(KNIFE).version() == "fake-ground-truth:knife-carries-peanut-to-plate"
