"""Factory fixtures that point the ProvenanceGraph contract suite at the real graph.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: kitchen.graph; FakeStateClient and load_scenario (kitchen.shared.fakes)
Emits: make_graph, make_unavailable_graph (the names kitchen/shared/contract_tests expects)
Rules: fail closed: the unavailable graph sits on a database that can't be reached
"""

import pytest

from kitchen.graph import ProvenanceGraph
from kitchen.shared.fakes import FakeStateClient, load_scenario


def graph_on(scenario, reachable: bool = True) -> ProvenanceGraph:
    """A ProvenanceGraph over a FakeStateClient holding the scenario's world."""
    state = FakeStateClient()
    load_scenario(state, scenario)
    state.set_unavailable(not reachable)
    return ProvenanceGraph(state)


@pytest.fixture
def make_graph():
    """The real graph over the given scenario."""
    return graph_on


@pytest.fixture
def make_unavailable_graph():
    """The real graph over the given scenario, with the database unreachable."""
    return lambda scenario: graph_on(scenario, reachable=False)
