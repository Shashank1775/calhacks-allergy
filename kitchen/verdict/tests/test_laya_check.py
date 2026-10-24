"""Tests for Laya's final plate check. They run without torch: the real adapter gets a scripted agent."""

import json
import math
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from kitchen.verdict.laya_check import (
    MAX_EDGES,
    UNITS,
    FakeLayaCheck,
    LayaCheckUnavailable,
    LocalLayaCheck,
    exact_reach,
    laya_hold_reasons,
    plate_state,
)
from kitchen.verdict.training.plate_data import ancestry, golden_rows, subgraph_from_scenario

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"


def scenario(name):
    return json.loads((CONTRACTS / "scenarios" / f"{name}.json").read_text())


def schema_errors(name, document):
    """Every way `document` breaks contracts/schemas/<name>.schema.json."""
    docs = {p.name: json.loads(p.read_text()) for p in (CONTRACTS / "schemas").glob("*.schema.json")}
    registry = Registry().with_resources((n, Resource.from_contents(d)) for n, d in docs.items())
    return [
        e.message for e in Draft202012Validator(docs[f"{name}.schema.json"], registry=registry).iter_errors(document)
    ]


class ScriptedAgent:
    """Stands in for the loaded plate model: answers every question with the given P, or misbehaves on cue."""

    def __init__(self, p=0.02, fail=False, broken=None):
        self.p, self.fail, self.broken = p, fail, broken

    def predict(self, state, questions):
        if self.fail:
            raise RuntimeError("model crashed")
        answer = self.broken if self.broken is not None else {"noul": self.p}
        return {"answers": {key: answer for key in questions}}


def local_check(agent):
    """A LocalLayaCheck wired to a scripted agent instead of a real checkpoint."""
    check = object.__new__(LocalLayaCheck)
    check._agent, check.version = agent, "test"
    return check


KNIFE = subgraph_from_scenario(scenario("knife-carries-peanut-to-plate"), "plate-t1", 3000)


@pytest.mark.safety
@pytest.mark.parametrize("row", golden_rows(), ids=lambda r: r["id"])
def test_the_fake_flags_exactly_the_expected_allergens_in_every_golden_scenario(row):
    reached = FakeLayaCheck().check(row["subgraph"], row["forbidden"])
    flagged = [r["allergen_unit_id"] for r in laya_hold_reasons(reached, row["forbidden"], row["pass_threshold"])]
    assert flagged == sorted(set(row["expected_reached"]) & set(row["forbidden"]))


def test_the_subgraph_matches_the_contract():
    assert schema_errors("plate_subgraph", KNIFE) == []


def test_the_subgraph_keeps_only_time_forward_paths():
    blocked = subgraph_from_scenario(scenario("time-order-blocks-path"), "plate-t3", 3000)
    assert [e["event_id"] for e in blocked["edges"]] == ["e1"]  # the scoop came after the knife touched the plate
    washed = subgraph_from_scenario(scenario("wash-resets-knife"), "plate-t2", 3000)
    assert [e["action"] for e in washed["edges"]] == ["transfer.scoop", "clean.wash", "transfer.contact"]


def test_the_state_reads_like_the_kitchen():
    state = plate_state(KNIFE)
    assert state["start"] == "bin-1 (satay sauce): peanuts 0.98 | tool-knife-1 (knife): clean | plate-t1 (plate): clean"
    assert state["events"] == (
        "1. 1.0s scoop bin-1 -> tool-knife-1 (p 0.95) | 2. 2.0s contact tool-knife-1 <-> plate-t1 (p 0.90)"
    )


def test_exact_reach_is_noisy_or_with_full_transfer():
    assert exact_reach(KNIFE)["peanut"] == pytest.approx(0.98 * 0.95 * 0.90)
    assert exact_reach(KNIFE)["milk"] == 0.0


def test_ancestry_ignores_edges_after_the_plate_was_touched():
    edges = [
        {
            "event_id": "e1",
            "t_start_ms": 1,
            "t_end_ms": 2,
            "a_id": "tool-a",
            "b_id": "plate-t1",
            "action": "transfer.contact",
            "probability": 0.9,
            "direction": "both",
        },
        {
            "event_id": "e2",
            "t_start_ms": 5,
            "t_end_ms": 6,
            "a_id": "tool-a",
            "b_id": "bin-1",
            "action": "transfer.scoop",
            "probability": 0.9,
            "direction": "b_to_a",
        },
    ]
    assert [e["event_id"] for e in ancestry(edges, "plate-t1")] == ["e1"]


@pytest.mark.safety
@given(
    reached=st.fixed_dictionaries({u: st.floats(0.0, 1.0) for u in UNITS}),
    forbidden=st.lists(st.sampled_from(UNITS), unique=True),
    pass_threshold=st.sampled_from([0.80, 0.90, 0.95, 0.99]),
)
def test_laya_only_ever_adds_hold_reasons_for_forbidden_allergens(reached, forbidden, pass_threshold):
    reasons = laya_hold_reasons(reached, forbidden, pass_threshold)
    assert all(r["code"] == "laya_flagged" and r["allergen_unit_id"] in forbidden for r in reasons)
    flagged = {r["allergen_unit_id"] for r in reasons}
    assert flagged == {u for u in forbidden if reached[u] >= 1.0 - pass_threshold}


def test_the_local_check_asks_only_about_forbidden_allergens():
    assert local_check(ScriptedAgent(p=0.83)).check(KNIFE, ["peanut", "soy"]) == {"peanut": 0.83, "soy": 0.83}
    assert local_check(ScriptedAgent()).check(KNIFE, []) == {}


@pytest.mark.safety
@pytest.mark.parametrize(
    "agent",
    [
        ScriptedAgent(fail=True),
        ScriptedAgent(broken={"noul": math.nan}),
        ScriptedAgent(broken={"noul": 1.4}),
        ScriptedAgent(broken={}),
    ],
    ids=["crash", "nan", "out-of-range", "missing"],
)
def test_a_model_failure_holds(agent):
    with pytest.raises(LayaCheckUnavailable):
        local_check(agent).check(KNIFE, ["peanut"])


@pytest.mark.safety
def test_a_history_too_long_to_read_holds():
    long = dict(KNIFE, edges=[dict(KNIFE["edges"][0], event_id=f"e{i}") for i in range(MAX_EDGES + 1)])
    with pytest.raises(LayaCheckUnavailable):
        FakeLayaCheck().check(long, ["peanut"])
    with pytest.raises(LayaCheckUnavailable):
        local_check(ScriptedAgent()).check(long, ["peanut"])


def test_a_missing_checkpoint_raises():
    with pytest.raises(LayaCheckUnavailable):
        LocalLayaCheck("/no/such/checkpoint")
