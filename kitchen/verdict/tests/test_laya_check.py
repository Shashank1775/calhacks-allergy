"""Tests for Laya's final plate check. They run without torch: the real adapter gets a scripted agent."""

import hashlib
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
    SHORTER_WINDOWS,
    UNITS,
    FakeLayaCheck,
    LayaCheckUnavailable,
    LocalLayaCheck,
    carry,
    exact_reach,
    laya_hold_reasons,
    on_path,
    plate_state,
    reached_question,
    readable_history,
)
from kitchen.verdict.training.plate_data import golden_rows, subgraph_from_scenario

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"

# sha256 of the questions exactly as the plate model (plate-v2) was fine-tuned on them. If this test fails, the wording
# changed: retrain the plate model, then update this hash. test_the_state_reads_like_the_kitchen pins the input.
TRAINED_QUESTIONS_SHA256 = "b64d73daf123c6ea596b08244ddb9b6c752ab1baf8c14c3aa2eab3500d464cb2"


def scenario(name):
    return json.loads((CONTRACTS / "scenarios" / f"{name}.json").read_text())


def schema_errors(name, document):
    """Every way `document` breaks contracts/schemas/<name>.schema.json."""
    docs = {p.name: json.loads(p.read_text()) for p in (CONTRACTS / "schemas").glob("*.schema.json")}
    registry = Registry().with_resources((n, Resource.from_contents(d)) for n, d in docs.items())
    validator = Draft202012Validator(docs[f"{name}.schema.json"], registry=registry)
    return [e.message for e in validator.iter_errors(document)]


def edge(event_id, t, a, b, action="transfer.contact", direction="both", p=0.9):
    """A minimal interaction event."""
    return {
        "event_id": event_id,
        "t_start_ms": t,
        "t_end_ms": t + 100,
        "a_id": a,
        "b_id": b,
        "action": action,
        "probability": p,
        "direction": direction,
        "classifier": {"name": "manual", "version": "test"},
    }


class ScriptedAgent:
    """Stands in for the loaded plate model: answers every question with the given P, or misbehaves on cue. The
    input is cut while it has more than `fits` events (the default: any length fits)."""

    def __init__(self, p=0.02, fail=False, broken=None, usage=None, fits=None):
        self.p, self.fail, self.broken, self.fits = p, fail, broken, fits
        self.usage = usage if usage is not None else dict(FULL_INPUT)
        self.states = []

    def predict(self, state, questions):
        self.states.append(state)
        if self.fail:
            raise RuntimeError("model crashed")
        answer = self.broken if self.broken is not None else {"noul": self.p}
        usage = self.usage
        if self.fits is not None and len(state["events"].split(" | ")) > self.fits:
            usage = dict(FULL_INPUT, truncated=True, state_tokens_dropped=40)
        return {"answers": {key: answer for key in questions}, "usage": usage}


# laya's usage block when the whole input fit
FULL_INPUT = {
    "input_tokens": 480,
    "state_tokens": 420,
    "state_tokens_dropped": 0,
    "truncated": False,
    "truncated_questions": [],
}


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


def test_question_wording_matches_training():
    wording = json.dumps([reached_question(u) for u in UNITS], sort_keys=True)
    assert hashlib.sha256(wording.encode()).hexdigest() == TRAINED_QUESTIONS_SHA256


def test_the_subgraph_matches_the_contract():
    assert schema_errors("plate_subgraph", KNIFE) == []


def test_the_state_reads_like_the_kitchen():
    state = plate_state(KNIFE)
    assert state["start"] == "tool-knife-1 (knife): clean | bin-1 (satay sauce): peanuts 0.98 | plate-t1 (plate): clean"
    assert state["events"] == "1. scoop tool-knife-1 <- bin-1 (p 0.95) | 2. contact tool-knife-1 <-> plate-t1 (p 0.90)"


def test_only_time_forward_paths_are_read():
    blocked = subgraph_from_scenario(scenario("time-order-blocks-path"), "plate-t3", 3000)
    assert [e["event_id"] for e in blocked["edges"]] == ["e1"]  # the scoop came after the knife touched the plate
    washed = subgraph_from_scenario(scenario("wash-resets-knife"), "plate-t2", 3000)
    assert [e["action"] for e in washed["edges"]] == ["transfer.scoop", "clean.wash", "transfer.contact"]


def test_extra_off_path_edges_are_dropped_before_the_model_reads_them():
    late_scoop = edge("e9", 2500, "tool-knife-1", "bin-1", action="transfer.scoop", direction="b_to_a")
    with_extra = dict(KNIFE, edges=KNIFE["edges"] + [late_scoop])
    assert plate_state(with_extra) == plate_state(KNIFE)
    assert on_path(with_extra) == KNIFE["edges"]


def test_exact_reach_is_noisy_or_with_full_transfer():
    assert exact_reach(KNIFE)["peanut"] == pytest.approx(0.98 * 0.95 * 0.90)
    assert exact_reach(KNIFE)["milk"] == 0.0


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
    assert flagged == {u for u in forbidden if 1.0 - reached[u] <= pass_threshold}


@pytest.mark.safety
@pytest.mark.parametrize(("p", "pass_threshold"), [(0.01, 0.99), (0.05, 0.95), (0.10, 0.90), (0.20, 0.80)])
def test_the_flag_boundary_holds(p, pass_threshold):
    assert laya_hold_reasons({"peanut": p}, ["peanut"], pass_threshold) != []


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
        ScriptedAgent(broken={"noul": None}),
        ScriptedAgent(broken={}),
    ],
    ids=["crash", "nan", "out-of-range", "none", "missing"],
)
def test_a_model_failure_holds(agent):
    with pytest.raises(LayaCheckUnavailable):
        local_check(agent).check(KNIFE, ["peanut"])


@pytest.mark.safety
def test_a_missing_forbidden_list_holds():
    with pytest.raises(LayaCheckUnavailable):
        local_check(ScriptedAgent()).check(KNIFE, None)


@pytest.mark.safety
@pytest.mark.parametrize(
    "usage",
    [
        dict(FULL_INPUT, truncated=True),
        dict(FULL_INPUT, state_tokens_dropped=12),
        dict(FULL_INPUT, truncated_questions=["reached_peanut"]),
        {"input_tokens": 480, "output_tokens": 0},
        {},
    ],
    ids=["truncated", "tokens-dropped", "question-cut", "no-truncation-keys", "empty"],
)
def test_an_input_the_model_had_to_cut_holds(usage):
    with pytest.raises(LayaCheckUnavailable):
        local_check(ScriptedAgent(usage=usage)).check(KNIFE, ["peanut"])


# ---------- long histories ----------


def kitchen_node(entity_id, probabilities=None, unknown=False):
    """A subgraph node: a bin with `probabilities`, or a clean (or unknown-history) tool, hand or plate."""
    kind = entity_id.split("-")[0]
    if unknown:
        start = {"probabilities": {}, "unknown": True, "source": "unknown_history"}
    elif probabilities is not None:
        start = {"probabilities": probabilities, "unknown": False, "source": "manual"}
    else:
        start = {"probabilities": {}, "unknown": False, "source": "shift_start_clean"}
    origin = "unknown" if unknown else "shift_start"
    return {"entity_id": entity_id, "kind": kind, "label": kind, "origin": origin, "start": start}


SATAY = kitchen_node("bin-1", {"peanut": 0.98, "soy": 0.9})
CLEAN = [kitchen_node(e) for e in ("tool-knife-1", "hand-p1-left", "hand-p1-right", "plate-t1")]


def full_reach(subgraph):
    """Noisy-OR over every on-path edge, however many: what shortening must never go below."""
    nodes = {n["entity_id"]: n for n in subgraph["nodes"]}
    return carry(on_path(subgraph), nodes)[subgraph["plate_id"]]


@pytest.mark.safety
def test_a_long_history_drops_touches_that_move_nothing_first():
    clean_touches = [edge(f"e{i}", 100 * i, "hand-p1-left", "plate-t1") for i in range(1, MAX_EDGES + 1)]
    story = [
        edge("s1", 5000, "tool-knife-1", "bin-1", action="transfer.scoop", direction="b_to_a"),
        edge("s2", 6000, "tool-knife-1", "plate-t1"),
    ]
    long = {"plate_id": "plate-t1", "as_of_t_ms": 9000, "nodes": [SATAY] + CLEAN, "edges": clean_touches + story}
    assert len(on_path(long)) == MAX_EDGES + 2
    assert readable_history(long)[0] == story  # the clean hand moved nothing; the starts are read as they are
    assert exact_reach(long) == full_reach(long)


SCOOP = edge("e0", 0, "tool-knife-1", "bin-1", action="transfer.scoop", direction="b_to_a")
TOUCHES = [edge(f"e{i}", 1000 * i, "tool-knife-1", "plate-t1", p=0.5) for i in range(1, MAX_EDGES + 2)]
LONG_KNIFE = {"plate_id": "plate-t1", "as_of_t_ms": 99000, "nodes": [SATAY] + CLEAN, "edges": [SCOOP] + TOUCHES}


@pytest.mark.safety
def test_a_history_still_too_long_folds_its_oldest_events_into_rounded_up_starts():
    edges, nodes = readable_history(LONG_KNIFE)
    assert edges == TOUCHES[-MAX_EDGES:]
    # After the scoop and the first touch: knife peanut 0.98 * 0.9 = 0.882, plate 0.441, shown rounded up.
    assert nodes["tool-knife-1"]["start"]["probabilities"] == {"peanut": 0.89, "soy": 0.81}
    assert nodes["plate-t1"]["start"]["probabilities"] == {"peanut": 0.45, "soy": 0.41}
    assert (
        plate_state(LONG_KNIFE)["start"]
        == "tool-knife-1 (tool): peanuts 0.89, soy 0.81 | plate-t1 (plate): peanuts 0.45, soy 0.41"
    )
    assert all(exact_reach(LONG_KNIFE)[u] >= full_reach(LONG_KNIFE)[u] for u in UNITS)


ENTITIES = ["bin-1", "bin-2", "tool-knife-1", "tool-spoon-2", "hand-p1-left", "hand-p1-right", "plate-t1"]
HUNDREDTHS = st.integers(1, 100).map(lambda k: k / 100)


@st.composite
def kitchens(draw):
    """A random plate subgraph: two bins, tools and hands (some of unknown history), and up to 40 time-ordered edges
    of every action and direction, ties included. Half the edges repeat the pair before them, as a touch seen across
    several windows and the news it carries do in a real kitchen."""
    nodes = []
    for entity_id in ENTITIES:
        if entity_id.startswith("bin"):
            profile = draw(st.dictionaries(st.sampled_from(UNITS), HUNDREDTHS, max_size=3))
            nodes.append(kitchen_node(entity_id, profile))
        else:
            nodes.append(kitchen_node(entity_id, unknown=entity_id != "plate-t1" and draw(st.booleans())))
    edges, t = [], 0
    for i in range(draw(st.integers(0, 40))):
        t += draw(st.sampled_from([0, 100, 1000]))
        if edges and draw(st.booleans()):
            a, b = edges[-1]["a_id"], edges[-1]["b_id"]
        else:
            a, b = draw(st.sampled_from(ENTITIES)), draw(st.sampled_from(ENTITIES))
        if a == b:
            edges.append(edge(f"e{i}", t, a, a, action="clean.wash", direction="a_to_b", p=draw(HUNDREDTHS)))
            continue
        action = draw(st.sampled_from(["transfer.contact", "transfer.scoop", "transfer.wipe"]))
        direction = draw(st.sampled_from(["a_to_b", "b_to_a", "both"]))
        edges.append(edge(f"e{i}", t, a, b, action=action, direction=direction, p=draw(HUNDREDTHS)))
    return {"plate_id": "plate-t1", "as_of_t_ms": t + 1000, "nodes": nodes, "edges": edges}


@pytest.mark.safety
@given(kitchen=kitchens(), limit=st.sampled_from((MAX_EDGES, *SHORTER_WINDOWS, 1)))
def test_shortening_never_lowers_any_allergens_reach(kitchen, limit):
    """Stricter only: the history the model reads fits, and carries at least as much of every allergen onto the plate
    as the whole history does, by the same noisy-OR, at the usual length and at every shorter window the check uses."""
    edges, nodes = readable_history(kitchen, limit)
    assert len(edges) <= limit
    full, read = full_reach(kitchen), carry(edges, nodes)[kitchen["plate_id"]]
    assert all(read[u] >= full[u] for u in UNITS), {u: (read[u], full[u]) for u in UNITS if read[u] < full[u]}


@pytest.mark.safety
def test_an_input_too_long_for_the_model_is_folded_further_and_asked_again():
    agent = ScriptedAgent(p=0.97, fits=SHORTER_WINDOWS[-1])
    assert local_check(agent).check(LONG_KNIFE, ["peanut"]) == {"peanut": 0.97}
    assert [len(state["events"].split(" | ")) for state in agent.states] == [MAX_EDGES, *SHORTER_WINDOWS]
    with pytest.raises(LayaCheckUnavailable):  # still too long at the shortest window: hold
        local_check(ScriptedAgent(fits=1)).check(LONG_KNIFE, ["peanut"])


@pytest.mark.safety
def test_an_edge_without_a_node_holds():
    stray = edge("e9", 2500, "tool-spoon-3", "plate-t1")  # on the path, but nobody said what the spoon carried
    broken = dict(KNIFE, edges=KNIFE["edges"] + [stray])
    with pytest.raises(LayaCheckUnavailable):
        FakeLayaCheck().check(broken, ["peanut"])
    with pytest.raises(LayaCheckUnavailable):
        local_check(ScriptedAgent()).check(broken, ["peanut"])


def test_a_missing_checkpoint_raises():
    with pytest.raises(LayaCheckUnavailable):
        LocalLayaCheck("/no/such/checkpoint")
