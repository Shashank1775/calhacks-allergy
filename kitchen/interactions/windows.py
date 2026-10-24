"""Builds a PairWindow per candidate pair: vector features for zone rules, plus a clip (box masks + change mask) for the ConvLSTM.

Owner: interactions (agents)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: candidate pairs, observation_frame stream, change masks from vision, features.py
Emits: PairWindow (features + interaction_clip + tensor) for classifier.py; labeled real clips for the partner (with labels.py)
Rules: 6 allergen-blind features, object-blind clips; uses kitchen/shared/clips.py, never its own clip code
Tasks: interactions-12, interactions-13

Not built yet.
"""
