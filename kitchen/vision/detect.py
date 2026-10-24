"""Detector interface with a CPU backend (ultralytics YOLOv8) and a Hailo-8 backend, picked by VISION_DETECTOR.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: frames from camera.py
Emits: detections (knife, spoon, fork, bowl) with normalized boxes and confidence
Rules: 1 never invent or interpolate detections
Tasks: vision-01 (CPU backend), vision-02 (Hailo spike), vision-11 (Hailo backend)

Not built yet (phase gate: PLANNING).
"""
