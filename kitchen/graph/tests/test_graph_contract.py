"""Runs the ProvenanceGraph contract suite against the real graph, through the fixtures in conftest.py.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: kitchen/shared/contract_tests/provenance_graph.py
Emits: nothing
Rules: the real graph passes the same suite as FakeProvenanceGraph, on every golden scenario
"""

from kitchen.shared.contract_tests.provenance_graph import *  # noqa: F403
