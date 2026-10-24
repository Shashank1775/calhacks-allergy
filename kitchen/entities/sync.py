"""Pushes entity snapshots to SpacetimeDB about 5 times a second without blocking the frame loop.

Owner: partner (human). Agents never edit this file.
Step:  Entity database
Reads: store snapshot, StateClient
Emits: StateClient.upsert_entities -> entity table
Rules: 1 failures surface as StateUnavailable or lag, never fake freshness; 8 never deletes
Tasks: partner

Not built yet.
"""
