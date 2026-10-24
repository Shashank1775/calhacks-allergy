"""The graph's own math and edge cases, beyond the contract suite: noisy-OR, the tie rule, unknown history, failures.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: kitchen.graph; FakeStateClient and scenario_entity (kitchen.shared.fakes)
Emits: nothing
Rules: fail closed; time runs forward (washes first on a tie); known-clean source (unknown history until a wash)
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kitchen.graph import ProvenanceGraph
from kitchen.graph.graph import read_shift
from kitchen.graph.propagate import UNITS, propagate
from kitchen.shared.fakes import FakeStateClient
from kitchen.shared.fakes.scenarios import scenario_entity
from kitchen.shared.interfaces import GraphUnavailable
from kitchen.shared.models import validate

SATAY = {"probabilities": {"peanut": 0.98}, "unknown": False, "source": "manual"}
LATER = 10_000


def edge(event_id, t_ms, a_id, b_id, action="transfer.contact", probability=0.9, direction="both"):
    """An interaction edge lasting 400 ms."""
    return {
        "event_id": event_id,
        "t_start_ms": t_ms,
        "t_end_ms": t_ms + 400,
        "a_id": a_id,
        "b_id": b_id,
        "action": action,
        "probability": probability,
        "direction": direction,
        "classifier": {"name": "sim_ground_truth", "version": "0"},
    }


def scoop(event_id, t_ms):
    """The knife scoops satay out of bin-1."""
    return edge(event_id, t_ms, "tool-knife-1", "bin-1", "transfer.scoop", 0.95, "b_to_a")


def wash(event_id, t_ms, entity_id="tool-knife-1"):
    """A wash of one entity: a self-loop."""
    return edge(event_id, t_ms, entity_id, entity_id, "clean.wash", 0.98, "a_to_b")


def state_with(events, unknown=()):
    """A database with a satay bin, a knife and a plate from shift start, late entities of unknown history, and events."""
    state = FakeStateClient()
    for entity_id in ("bin-1", "tool-knife-1", "plate-t1"):
        state.register_entity(scenario_entity(entity_id, "shift_start", 0))
    for entity_id in unknown:
        state.register_entity(scenario_entity(entity_id, "unknown", 1500))
    state.put_bin_profile("bin-1", SATAY)
    for event in events:
        state.record_interaction(event)
    return state


def graph_with(events, unknown=()):
    """The graph over state_with(events, unknown)."""
    return ProvenanceGraph(state_with(events, unknown))


def reached(graph, unit="peanut"):
    """The plate's probability of carrying one allergen."""
    return {r["allergen_unit_id"]: r["probability"] for r in graph.reach("plate-t1", LATER)}.get(unit, 0.0)


def test_noisy_or_carries_the_allergen_along_the_chain():
    graph = graph_with([scoop("e1", 1000), edge("e2", 2000, "tool-knife-1", "plate-t1")])
    assert reached(graph) == pytest.approx(0.98 * 0.95 * 0.9)
    [peanut] = graph.reach("plate-t1", LATER)
    assert [step["event_id"] for step in peanut["chain"]] == ["e1", "e2"]


def test_two_contacts_combine_by_noisy_or():
    knife = 0.98 * 0.95
    graph = graph_with(
        [
            scoop("e1", 1000),
            edge("e2", 2000, "tool-knife-1", "plate-t1", probability=0.5),
            edge("e3", 3000, "tool-knife-1", "plate-t1", probability=0.5),
        ]
    )
    assert reached(graph) == pytest.approx(1 - (1 - 0.5 * knife) ** 2)


@pytest.mark.safety
def test_a_wash_tied_with_a_scoop_cannot_erase_it():
    """OQ-17: on equal times a wash goes before the transfer, whatever the event IDs say, so the knife keeps the peanut."""
    graph = graph_with([scoop("e1", 1000), wash("e2", 1000), edge("e3", 2000, "tool-knife-1", "plate-t1")])
    assert reached(graph) == pytest.approx(0.98 * 0.95 * 0.9)


@pytest.mark.safety
def test_an_edge_after_as_of_changes_nothing():
    graph = graph_with([scoop("e1", 1000), edge("e2", 2000, "tool-knife-1", "plate-t1")])
    assert graph.reach("plate-t1", 1999) == []
    assert graph.subgraph("plate-t1", 1999)["edges"] == []


@pytest.mark.safety
def test_unknown_history_spreads_through_other_entities_until_washed():
    """Known-clean source: a mid-shift spoon may carry anything, so the knife it touched may too."""
    events = [
        edge("e1", 2000, "tool-spoon-9", "tool-knife-1"),
        edge("e2", 3000, "tool-knife-1", "plate-t1"),
    ]
    graph = graph_with(events, unknown=["tool-spoon-9"])
    assert graph.unknown_ancestors("plate-t1", LATER) == ["tool-spoon-9"]
    reach = graph.reach("plate-t1", LATER)
    assert {r["allergen_unit_id"] for r in reach} == set(UNITS)
    assert all(r["probability"] == pytest.approx(0.81) and r["chain"] == [] for r in reach)

    washed = graph_with([events[0], wash("w1", 2500), events[1]], unknown=["tool-spoon-9"])
    assert washed.unknown_ancestors("plate-t1", LATER) == []
    assert reached(washed) == pytest.approx(0.9 * (1 - 0.98) * 0.9)


@pytest.mark.safety
def test_an_entity_the_database_lacks_counts_as_unknown_history():
    graph = graph_with([edge("e1", 2000, "tool-ghost-3", "plate-t1")])
    assert graph.unknown_ancestors("plate-t1", LATER) == ["tool-ghost-3"]
    subgraph = validate(graph.subgraph("plate-t1", LATER), "plate_subgraph")
    ghost = next(node for node in subgraph["nodes"] if node["entity_id"] == "tool-ghost-3")
    assert ghost["origin"] == "unknown" and ghost["start"]["unknown"] is True


@pytest.mark.safety
def test_a_plate_the_database_lacks_raises_graph_unavailable():
    graph = graph_with([])
    for query in (graph.reach, graph.unknown_ancestors, graph.subgraph):
        with pytest.raises(GraphUnavailable):
            query("plate-t99", LATER)


@pytest.mark.safety
def test_a_wash_between_two_entities_is_ignored():
    """A clean.wash must be a self-loop. One that isn't is malformed: it lowers nothing and stays out of the subgraph."""
    events = [scoop("e1", 1000), edge("e3", 3000, "tool-knife-1", "plate-t1")]
    bad_wash = edge("w2", 2000, "tool-knife-1", "plate-t1", "clean.wash", 0.98, "a_to_b")
    graph = graph_with([events[0], bad_wash, events[1]])
    assert reached(graph) == reached(graph_with(events))
    assert "w2" not in {edge["event_id"] for edge in graph.subgraph("plate-t1", LATER)["edges"]}


@pytest.mark.safety
def test_unknown_origin_is_never_clean_whatever_its_profile_says():
    """Known-clean source: origin decides. A database row calling a mid-shift spoon clean doesn't make it clean."""
    state = state_with([edge("e1", 2000, "tool-spoon-9", "plate-t1")])
    spoon = scenario_entity("tool-spoon-9", "unknown", 1500)
    spoon["allergen_profile"] = {"probabilities": {}, "unknown": False, "source": "shift_start_clean"}
    state.register_entity(spoon)
    graph = ProvenanceGraph(state)
    assert graph.unknown_ancestors("plate-t1", LATER) == ["tool-spoon-9"]
    assert reached(graph, "sesame") == pytest.approx(0.9)


ENTITIES = ["bin-1", "tool-knife-1", "tool-spoon-9", "plate-t1"]
random_edges = st.lists(
    st.tuples(
        st.sampled_from(ENTITIES),
        st.sampled_from(ENTITIES),
        st.sampled_from(["transfer.contact", "transfer.scoop", "transfer.wipe", "clean.wash"]),
        st.floats(0.0, 1.0),
        st.sampled_from(["a_to_b", "b_to_a", "both"]),
    ),
    max_size=12,
)


@pytest.mark.safety
@given(random_edges)
def test_transfers_never_lower_risk_and_washes_never_raise_it(drawn):
    """Noisy-OR is monotone: one more contact can never make anything look cleaner, and a wash can't add risk."""
    entities, _ = read_shift(state_with([], unknown=["tool-spoon-9"]), LATER)
    edges = []
    for number, (a_id, b_id, action, p, direction) in enumerate(drawn):
        if action == "clean.wash":
            b_id, direction = a_id, "a_to_b"
        edges.append(edge(f"e{number}", 1000 * (number + 2), a_id, b_id, action, p, direction))

    for position, current in enumerate(edges):
        before = propagate(entities, edges[:position]).probabilities
        after = propagate(entities, edges[: position + 1]).probabilities
        for entity_id in ENTITIES:
            for unit in UNITS:
                assert 0.0 <= after[entity_id][unit] <= 1.0
                if current["action"] == "clean.wash":
                    assert after[entity_id][unit] <= before[entity_id][unit] + 1e-12
                else:
                    assert after[entity_id][unit] >= before[entity_id][unit] - 1e-12
