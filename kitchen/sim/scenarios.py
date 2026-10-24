"""Scenario writer: world runs become scenario files and replay JSONL (CLI: generate, export).

Owner: sim (agents)
Step:  simulator
Reads: world runs (world.py)
Emits: scenario files, interaction_event JSONL, entity JSONL
Rules: expected answers come from world truth only; known-clean source: track loss becomes a new late entity with origin unknown

Not built yet.
"""
