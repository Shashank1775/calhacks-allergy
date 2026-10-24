"""Classifier wrapper: uses the adopted LSTM when healthy, falls back to zone rules on load failure, error or timeout.

Owner: interactions (agents)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: InteractionClassifier implementations: zone_rules.py, partner kitchen/lstm (after adoption)
Emits: list of (action, probability) plus the name and version of the classifier that produced it
Rules: 7 zone rules are always the fallback; LSTM only after the benchmark ADR
Tasks: interactions-06

Not built yet (phase gate: PLANNING).
"""
