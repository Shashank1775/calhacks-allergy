"""Observation model and scenario writer: world runs to scenario files and replay JSONL, plus the generate/export/metrics/load CLI.

Owner: sim (agents)
Step:  simulation
Reads: world runs (kitchen.py); verdict JSONL (metrics); StateClient (load replay)
Emits: scenario, interaction_event JSONL, entity JSONL, metrics and load reports
Rules: expected answers come from world truth only; 8 track loss becomes a new late entity with origin unknown
Tasks: sim-01, sim-03, sim-05, sim-07, sim-10, sim-11

Not built yet.
"""
