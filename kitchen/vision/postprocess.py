"""Pre- and post-processing shared by every detector backend: letterbox, output decoding, NMS, keypoints, class map.

Owner: vision (agents)
Step:  Vision (detection + tracking)
Reads: raw network outputs from detect.py's backend (ONNX Runtime now, HailoRT later); manifest.json
Emits: detections in normalized frame coordinates
Rules: 1 never invent detections; identical code for every backend, so local results predict Hailo results
Tasks: vision-01, vision-12

Not built yet.
"""
