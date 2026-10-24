"""Who touched what: tracked entity pairs in, classified, time-stamped interaction edges out.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: observation_frame and change masks (vision), zones, InteractionClassifier (zone rules; partner LSTM once adopted)
Emits: interaction_event (StateClient.record_interaction and JSONL), PairWindows of interaction_features + clips
Rules: 6 allergen-blind features, object-blind clips; 7 rules stay the fallback; 1 never drop events; 5 events
       time-ordered

Built: candidates.py, features.py, windows.py, zone_rules.py, classifier.py, events.py (segmenter, writer, the
InteractionStep pipeline and the CLI), settings.py. Not built yet: labels.py, benchmark.py.
"""
