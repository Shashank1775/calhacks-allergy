"""Segments per-frame labels into InteractionEvents and streams them to the database; also the JSONL CLI.

Owner: interactions (agents)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: observation_frame stream, candidates.py, features.py, classifier.py, StateClient, Clock
Emits: interaction_event via StateClient.record_interactions (interaction table) and events.jsonl
Rules: 5 t_start <= t_end, ordered per pair; 1 buffer and retry, never drop; wash is a self-loop
Tasks: interactions-01 (segmenter + CLI), interactions-05 (event pack), interactions-07 (streaming)

Not built yet.
"""
