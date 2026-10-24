"""InteractionFeatures per candidate pair per frame, plus per-pair window export for LSTM training.

Owner: interactions (agents)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: candidate pairs, observation_frame, zone kinds
Emits: interaction_features rows; windows JSONL (one pair per window) for the partner's LSTM and sim
Rules: 6 no allergen fields, zone_kind never zone ID; time-based units stable across FPS
Tasks: interactions-03, interactions-12 (window export)

Not built yet (phase gate: PLANNING).
"""
