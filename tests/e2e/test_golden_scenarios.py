"""Every golden scenario through the whole system: database, verdict service, Laya's plate check, gate and voice.

Owner: platform (agents)
Step:  end-to-end harness
Reads: contracts/scenarios/*.json; the `kitchen` fixture (tests/e2e/conftest.py)
Emits: nothing
Rules: every safety rule a golden scenario tags; a failure is a bug against the first team whose output diverges
       from `expected`, never a reason to edit the scenario
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS
from kitchen.shared.models import validate

HARNESS = Path(__file__).resolve().parent / "conftest.py"


def verdict_for(verdicts, expected):
    """The latest verdict for an expected (plate, revision) entry, or None."""
    matching = [
        verdict
        for verdict in verdicts
        if verdict["plate_id"] == expected["plate_id"] and verdict["ticket_revision"] == expected["ticket_revision"]
    ]
    return matching[-1] if matching else None


def ticket_at(scenario, expected):
    """The ticket revision an expected entry judges its plate against."""
    ticket_id = next(p["ticket_id"] for p in scenario["plates_at_pass"] if p["plate_id"] == expected["plate_id"])
    revisions = scenario["tickets"] + scenario.get("amendments", [])
    return next(t for t in revisions if t["ticket_id"] == ticket_id and t["revision"] == expected["ticket_revision"])


def flagged_by(verdict, code):
    """The allergens a verdict's reasons of one code name, sorted."""
    return sorted(reason["allergen_unit_id"] for reason in verdict["reasons"] if reason["code"] == code)


def observed(kitchen, verdict, expected):
    """What the system recorded for one expected entry, shaped like wanted() so a mismatch prints a readable diff.

    Everything here comes from what the system wrote (the verdict row, the gate's log), never from asking a fake again.
    """
    codes = [reason["code"] for reason in verdict["reasons"]]
    return {
        "plate_id": verdict["plate_id"],
        "ticket_revision": verdict["ticket_revision"],
        "decision": verdict["decision"],
        "forbidden_reached": sorted(item["allergen_unit_id"] for item in verdict["flagged"]),
        "graph_flags": flagged_by(verdict, "forbidden_allergen"),
        "laya_flags": flagged_by(verdict, "laya_flagged"),
        "laya": verdict["versions"].get("laya"),
        # The scenario names the codes that must be there; the policy may add more (low_confidence beside a flag).
        "reason_codes": [code for code in expected.get("reason_codes", []) if code in codes],
        "chain_event_ids": list(dict.fromkeys(step["event_id"] for step in verdict["chain"])),
        "gate": kitchen.gate_at(verdict["decided_t_ms"]),
    }


def wanted(scenario, expected):
    """An expected entry in observed()'s shape.

    The forbidden allergens that reached the plate are flagged by the graph and by Laya alike (a perfect FakeLayaCheck
    agrees with the ground truth), Laya answered every verdict, and the gate shows the expected decision.
    """
    forbidden = set(ticket_at(scenario, expected)["forbidden"])
    reached = sorted(set(expected["allergens_reached"]) & forbidden)
    return {
        "plate_id": expected["plate_id"],
        "ticket_revision": expected["ticket_revision"],
        "decision": expected["decision"],
        "forbidden_reached": reached,
        "graph_flags": reached,
        "laya_flags": reached,
        "laya": "fake",
        "reason_codes": expected.get("reason_codes", []),
        "chain_event_ids": expected.get("chain_event_ids", []),
        "gate": expected["decision"],
    }


@pytest.mark.safety
@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_golden_scenario_end_to_end(kitchen, path):
    shift = kitchen(path)
    shift.play_scenario()
    verdicts = shift.state.list_verdicts()

    assert len(verdicts) == len(shift.scenario["expected"]), [v["verdict_id"] for v in verdicts]
    for expected in shift.scenario["expected"]:
        verdict = verdict_for(verdicts, expected)
        assert verdict is not None, f"no verdict for {expected['plate_id']} at revision {expected['ticket_revision']}"
        validate(verdict, "verdict")
        assert observed(shift, verdict, expected) == wanted(shift.scenario, expected)


@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_every_hold_is_spoken_once_and_passes_are_quiet(kitchen, path):
    shift = kitchen(path)
    shift.play_scenario()

    holds = [verdict for verdict in shift.state.list_verdicts() if verdict["decision"] == "hold"]
    assert len(shift.speaker.lines) == len(holds)
    assert all(line.startswith("Hold ") for line in shift.speaker.lines)


# Plays every golden scenario in a fresh interpreter and prints what the system wrote, as JSON.
DUMP = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("e2e_harness", sys.argv[1])
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS, FakeProvenanceGraph, FakeStateClient, load_scenario
out = []
for path in GOLDEN_SCENARIO_PATHS:
    state = FakeStateClient()
    scenario = load_scenario(state, path)
    shift = harness.Kitchen(state=state, graph=FakeProvenanceGraph(scenario), scenario=scenario)
    shift.play_scenario()
    out.append([path.stem, state.list_verdicts(), shift.gate.calls, shift.speaker.lines])
print(json.dumps(out))
"""


def dump_with_hash_seed(seed: str) -> str:
    """Run DUMP in a separate Python with PYTHONHASHSEED=seed and return its output."""
    env = dict(os.environ, PYTHONHASHSEED=seed)
    run = subprocess.run([sys.executable, "-c", DUMP, str(HARNESS)], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_output_is_identical_across_processes_and_hash_seeds():
    """Deterministic under FakeClock: separate interpreters with different hash seeds write byte-identical verdicts,
    gate states and spoken lines, so nothing depends on set or dict ordering that changes between runs."""
    first, second = dump_with_hash_seed("0"), dump_with_hash_seed("1")

    assert first.strip()
    assert first == second
