"""Who touched what: tracked entity pairs in, classified, time-stamped interaction edges out.

Owner: partner (human). Agents never edit this file.
Step:  Interactions (candidate pairs, features, zone rules)
Reads: observation_frame, zone kinds, InteractionClassifier (zone rules; partner LSTM once adopted)
Emits: interaction_event (StateClient.record_interaction and JSONL), interaction_features windows, benchmark reports
Rules: 6 allergen-blind features; 7 rules stay the fallback; 1 never drop events; 5 events time-ordered
Tasks: interactions-01 .. interactions-12

Not built yet.
"""
