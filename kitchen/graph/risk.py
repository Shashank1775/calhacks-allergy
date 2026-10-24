"""Live per-entity allergen risk, written to entity_risk for the camera overlays.

Owner: partner (human). Agents never edit this file.
Step:  provenance graph
Reads: propagated probabilities
Emits: entity_risk via StateClient.put_entity_risk (single writer)
Rules: 8 unknown-history entities publish unknown: true until washed
Tasks: partner

Not built yet (phase gate: PLANNING).
"""
