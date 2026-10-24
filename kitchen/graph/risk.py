"""Live per-entity allergen risk, written as entity_state rows for the camera overlays.

Owner: partner (human). Agents never edit this file.
Step:  provenance graph
Reads: propagated probabilities
Emits: entity_state rows (allergens, unknown, caused_by) via StateClient.move_entity
Rules: 8 unknown-history entities publish unknown: true until washed
Tasks: partner

Not built yet.
"""
