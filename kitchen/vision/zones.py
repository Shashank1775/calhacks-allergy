"""Zone calibration: file format, click-to-calibrate tool, point-in-polygon assignment, and writing zones to the database.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: a still image (calibration), zones.json
Emits: a zone (or null) for each detection; StateClient.set_zones -> zone table
Rules: 1 no zone guessed; deterministic tie-break for overlapping zones
Tasks: vision-03, vision-06 (set_zones at startup)

Not built yet (phase gate: PLANNING).
"""
