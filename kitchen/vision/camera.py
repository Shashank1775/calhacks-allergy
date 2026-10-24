"""Frame sources (Pi camera, video file, JSONL replay), each frame stamped with t_ms from the Clock.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: picamera2, video files in data/, frame JSONL, Clock
Emits: raw frames with t_ms for detect.py and pose.py
Rules: 5 strictly increasing t_ms, even across source restarts
Tasks: vision-01 (video file), vision-09 (JSONL replay), picamera2 in M2

Not built yet (phase gate: PLANNING).
"""
