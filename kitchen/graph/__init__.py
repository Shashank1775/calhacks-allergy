"""Provenance graph: entities as nodes, interactions as time-stamped edges; answers which allergens could reach a plate.

Owner: partner (human). Agents never edit this file.
Step:  provenance graph
Reads: entity, bin_profile, plate_binding, interaction_event (StateClient)
Emits: ProvenanceGraph (reach, subgraph, unknown_ancestors, version); entity_risk
Rules: 1 failures raise GraphUnavailable; 5 time-forward paths only; 8 unknown history is never clean
Tasks: partner

Not built yet (phase gate: PLANNING).
"""
