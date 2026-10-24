"""In-memory entity store: builds and updates Entity rows from every ObservationFrame.

Owner: partner (human). Agents never edit this file.
Step:  Entity database
Reads: observation_frame
Emits: Entity via get() and snapshot()
Rules: 8 never delete, never change first_seen_t_ms or origin; 5 t_ms only forward
Tasks: partner

Not built yet (phase gate: PLANNING).
"""
