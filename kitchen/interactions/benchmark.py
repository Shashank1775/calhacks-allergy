"""Frozen held-out benchmark that compares any InteractionClassifier against zone rules with a cost-weighted score.

Owner: interactions (agents)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: held-out manifest, labeled sessions, InteractionClassifier implementations
Emits: report.json + report.md (per-action precision/recall, cost-weighted score)
Rules: 7 decides LSTM adoption; missed transfers and false washes weigh most
Tasks: interactions-11

Not built yet.
"""
