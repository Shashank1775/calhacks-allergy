"""Provenance graph: entities as nodes, interactions as time-stamped edges; answers which allergens could reach a plate.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: entity, bin_profile, interaction_event (StateClient)
Emits: ProvenanceGraph (reach, subgraph, unknown_ancestors, version)
Rules: fail closed: failures raise GraphUnavailable; time runs forward: time-forward paths only; known-clean source:
       unknown history is never clean

    from kitchen.graph import ProvenanceGraph
    graph = ProvenanceGraph(state)          # any StateClient
    graph.reach("plate-t1", as_of_t_ms)

Live per-entity risk for the overlays (risk.py) isn't built yet.
"""

from kitchen.graph.graph import ProvenanceGraph

__all__ = ["ProvenanceGraph"]
