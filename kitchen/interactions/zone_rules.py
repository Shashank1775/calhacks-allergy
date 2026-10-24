"""Hand-written zone-rule classifier: the always-available InteractionClassifier named zone_rules.

Owner: partner (human). Agents never edit this file.
Step:  Interactions (candidate pairs, features, zone rules)
Reads: windows of interaction_features
Emits: list of (action, probability); constants in one config object, encoded in version
Rules: 7 always available fallback; 1 wash needs wash zone, dwell, high probability
Tasks: interactions-01, interactions-08 (tuning)

Not built yet.
"""
