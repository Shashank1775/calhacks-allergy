"""Recording and replay: ObservationFrames to frames.jsonl and change masks to change.npz, replayed byte for byte.

Owner: vision (agents)
Step:  vision
Reads: the observation_frame stream and change masks
Emits: frames.jsonl + change.npz + manifest.json under data/; a replay source; the fixture pack
Rules: time runs forward: replay keeps the recorded t_ms

Not built yet.
"""
