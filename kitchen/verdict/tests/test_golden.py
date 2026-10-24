"""Golden scenario runner: every file in contracts/scenarios/ runs through the verdict service and must match.

Owner: verdict (agents)
Step:  verdict
Reads: contracts/scenarios/*.json; kitchen.verdict.service; FakeStateClient and FakeProvenanceGraph (platform)
Emits: nothing
Rules: every safety rule a golden scenario tags (fail closed, known-clean source, time runs forward, late changes
       recheck everything); a new scenario file needs no code change here

VERDICT_GOLDEN_GRAPH=real selects the partner's graph (kitchen.graph.ProvenanceGraph) instead of
FakeProvenanceGraph, reading the same FakeStateClient the verdict service does.
"""

import os

import pytest

from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import (
    GOLDEN_SCENARIO_PATHS,
    FakeProvenanceGraph,
    FakeStateClient,
    load_scenario,
    scenario_timeline,
)
from kitchen.shared.models import EntityState, Zone
from kitchen.verdict.laya_check import FakeLayaCheck
from kitchen.verdict.service import VerdictService

GRAPH = os.environ.get("VERDICT_GOLDEN_GRAPH", "fake")
PASS_ZONE = Zone(zone_id="pass", kind="pass", polygon=[(0.8, 0.0), (1.0, 0.0), (1.0, 1.0), (0.8, 1.0)])


def make_graph(scenario, state):
    """The ProvenanceGraph to judge with: the platform fake by default, the partner's graph on `state` when asked for."""
    if GRAPH == "real":
        from kitchen.graph import ProvenanceGraph

        return ProvenanceGraph(state)
    return FakeProvenanceGraph(scenario)


def replay(path):
    """Play a scenario's timeline through FakeStateClient and the verdict service. Returns (state, graph, scenario)."""
    state = FakeStateClient()
    scenario = load_scenario(state, path)
    state.set_zones([PASS_ZONE])
    clock = FakeClock()
    graph = make_graph(scenario, state)
    VerdictService(state, graph, FakeLayaCheck(), clock).start()

    for item in scenario_timeline(scenario):
        clock.set(item.t_ms)
        if item.kind == "amendment":
            state.put_ticket(item.record)
        elif item.kind == "plate_at_pass":
            plate_id = item.record["plate_id"]
            state.move_entity(EntityState(plate_id, 1, item.t_ms, PASS_ZONE.zone_id, None, {}, False))
    return state, graph, scenario


def summary(verdict, graph, expected):
    """The parts of a verdict a golden scenario pins down, shaped like the expected entry so a mismatch diffs."""
    codes = [reason["code"] for reason in verdict["reasons"]]
    chain_ids = list(dict.fromkeys(step["event_id"] for step in verdict["chain"]))
    reached = {entry["allergen_unit_id"] for entry in graph.reach(verdict["plate_id"], verdict["decided_t_ms"])}
    return {
        "plate_id": verdict["plate_id"],
        "ticket_revision": verdict["ticket_revision"],
        "decision": verdict["decision"],
        # The scenario lists the allergens that must be found; a real graph may find more (unknown history carries
        # every allergen, and a wash leaves a trace), as the ProvenanceGraph contract allows.
        "allergens_reached": [unit for unit in sorted(expected["allergens_reached"]) if unit in reached],
        # The scenario lists the codes that must be there; decide() may add more (low_confidence alongside a flag).
        "reason_codes": [code for code in expected.get("reason_codes", []) if code in codes],
        "chain_event_ids": chain_ids,
    }


@pytest.mark.safety
@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_golden_scenarios(path):
    state, graph, scenario = replay(path)
    verdicts = state.list_verdicts()

    assert len(verdicts) == len(scenario["expected"]), [v["verdict_id"] for v in verdicts]
    for expected in scenario["expected"]:
        matching = [
            verdict
            for verdict in verdicts
            if verdict["plate_id"] == expected["plate_id"] and verdict["ticket_revision"] == expected["ticket_revision"]
        ]
        assert matching, f"no verdict for {expected['plate_id']} at revision {expected['ticket_revision']}"
        want = {
            "plate_id": expected["plate_id"],
            "ticket_revision": expected["ticket_revision"],
            "decision": expected["decision"],
            "allergens_reached": sorted(expected["allergens_reached"]),
            "reason_codes": expected.get("reason_codes", []),
            "chain_event_ids": expected.get("chain_event_ids", []),
        }
        assert summary(matching[-1], graph, expected) == want
