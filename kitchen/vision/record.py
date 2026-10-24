"""Records ObservationFrames to JSONL plus change masks to change.npz, with a session manifest; replays both byte for byte.

Owner: vision (agents)
Step:  Vision (detection + tracking)
Reads: observation_frame stream and change masks
Emits: frames.jsonl + change.npz + manifest.json under data/; a replay source; the fixture pack
Rules: 5 replay keeps the recorded t_ms
Tasks: vision-05, vision-09, vision-13

Not built yet.
"""
