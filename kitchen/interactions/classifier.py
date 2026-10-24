"""Classifier wrapper: gives each PairWindow to the adopted ConvLSTM when healthy, else to zone rules.

Owner: partner (human). Agents never edit this file.
Step:  Interactions (candidate pairs, features, zone rules)
Reads: PairWindow; InteractionClassifier implementations: zone_rules.py, partner kitchen/lstm (after adoption)
Emits: list of (action, probability) plus the name and version of the classifier that produced it
Rules: 7 zone rules are always the fallback (load failure, error, timeout); ConvLSTM only after the benchmark ADR
Tasks: interactions-06

Not built yet.
"""
