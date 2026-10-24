"""Runs the ProvenanceGraph contract suite against FakeProvenanceGraph, through the fixtures in conftest.py.

Owner: platform (agents)
Step:  shared
Reads: kitchen/shared/contract_tests/provenance_graph.py
Emits: nothing
Rules: the fake passes the same suite the partner's real graph must pass
"""

from kitchen.shared.contract_tests.provenance_graph import *  # noqa: F403
