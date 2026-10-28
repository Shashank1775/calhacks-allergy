"""Provenance graph: entities as nodes, interactions as time-stamped edges; answers which allergens could reach a plate.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: entity, bin_profile, interaction_event (StateClient)
Emits: ProvenanceGraph (reach, subgraph, unknown_ancestors, version); LiveRisk (entity_state rows for the overlays)
Rules: fail closed: failures raise GraphUnavailable; time runs forward: time-forward paths only; known-clean source:
       a tool, hand or plate of unknown origin starts clean, so only a path holds a plate

    from kitchen.graph import ProvenanceGraph
    graph = ProvenanceGraph(state)          # any StateClient
    graph.reach("plate-t1", as_of_t_ms)

    from kitchen.graph import LiveRisk
    risk = LiveRisk(state, clock)           # writes each tool, hand and plate's live allergens for the camera overlays
    risk.start()                            # in the background, whenever an edge lands
"""

from kitchen.graph.graph import ProvenanceGraph
from kitchen.graph.risk import LiveRisk

__all__ = ["LiveRisk", "ProvenanceGraph"]
