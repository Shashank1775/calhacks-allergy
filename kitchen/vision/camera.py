"""Frame sources: video file, laptop webcam, JSONL replay, and later the Pi camera. Each frame is stamped with t_ms.

Owner: vision (agents)
Step:  vision
Reads: video files in data/, the webcam, frame JSONL, picamera2 (later), Clock
Emits: raw frames with t_ms for detect.py
Rules: time runs forward: strictly increasing t_ms, even across source restarts

Not built yet.
"""
