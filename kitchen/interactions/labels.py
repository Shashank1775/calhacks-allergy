"""Labeled clip dataset: session layout, validator, frozen held-out manifest, and the keypress labeling tool.

Owner: interactions (agents)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: vision recordings (frames.jsonl + manifest), candidate segments
Emits: labels.jsonl per session; held-out manifest with content hash
Rules: 7 split by session, never by clip, so the benchmark cannot leak
Tasks: interactions-09, interactions-10

Not built yet (phase gate: PLANNING).
"""
