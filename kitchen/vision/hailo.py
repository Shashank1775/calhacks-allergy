"""Hailo-8 backend for detect.py: runs the bundle's HEF with HailoRT on the Pi. The Hailo isn't connected yet.

Owner: vision (agents)
Step:  Vision (detection + tracking)
Reads: data/models/<name>/model.hef and manifest.json
Emits: raw network outputs for postprocess.py (same shape as the ONNX backend)
Rules: 1 a device error ends frames (health goes stale, gate faults), never fake detections; imported only when VISION_DETECTOR=hailo
Tasks: vision-11 (backend against a mock runtime locally; device verification by the owner is hardware)

Not built yet.
"""
