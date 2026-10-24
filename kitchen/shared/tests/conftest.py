"""Factory fixtures that point the contract suites in kitchen/shared/contract_tests at the platform's fakes.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.fakes
Emits: make_state_client, make_unavailable_state_client, make_entity_store, make_graph, make_unavailable_graph,
       make_classifier
Rules: the partner's folders provide the same fixture names, returning their real implementations, to run the same
       suites (kitchen/shared/README.md, Contract tests)
"""

import pytest

from kitchen.shared.fakes import FakeClassifier, FakeClock, FakeEntityStore, FakeProvenanceGraph, FakeStateClient


@pytest.fixture
def make_state_client():
    """A new, empty FakeStateClient per call."""
    return FakeStateClient


@pytest.fixture
def make_unavailable_state_client():
    """A FakeStateClient whose database is unreachable."""

    def make():
        client = FakeStateClient()
        client.set_unavailable(True)
        return client

    return make


@pytest.fixture
def make_entity_store():
    """A new, empty FakeEntityStore on a FakeClock per call."""
    return lambda: FakeEntityStore(FakeClock())


@pytest.fixture
def make_graph():
    """A FakeProvenanceGraph answering from the given scenario's ground truth."""
    return FakeProvenanceGraph


@pytest.fixture
def make_unavailable_graph():
    """A FakeProvenanceGraph over the given scenario that fails every query."""

    def make(scenario):
        graph = FakeProvenanceGraph(scenario)
        graph.set_unavailable(True)
        return graph

    return make


@pytest.fixture
def make_classifier():
    """A FakeClassifier with no script: every pair is unimportant."""
    return FakeClassifier
