"""Records ObservationFrames to JSONL with a session manifest, and replays them byte for byte.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: observation_frame stream
Emits: frames.jsonl + manifest.json under data/; a replay source; the fixture pack
Rules: 5 replay keeps the recorded t_ms
Tasks: vision-05, vision-09

Not built yet (phase gate: PLANNING).
"""
