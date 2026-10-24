"""Trains the LSTM on labeled feature windows, with a session-level split.

Owner: partner (human). Agents never edit this file.
Step:  LSTM classifier
Reads: feature windows JSONL (interactions-12), labels (interactions-09), optional sim windows
Emits: weights in data/ listed in data-manifest.json (never in git)
Rules: 6 allergen-blind; 7 never trains on the frozen held-out set
Tasks: partner

Not built yet.
"""
