"""Detector interface; picks a backend with VISION_DETECTOR=onnx|hailo (default onnx, local).

Owner: vision (agents)
Step:  Vision (detection + tracking)
Reads: frames from camera.py; the deploy bundle in data/models/<name>/ (model.onnx now, model.hef later)
Emits: detections (knife, spoon, fork, bowl, person keypoints) with normalized boxes and confidence
Rules: 1 never invent or interpolate detections; backends only run the network, all pre/post-processing is postprocess.py
Tasks: vision-01 (onnx backend), vision-11 (hailo backend)

Not built yet.
"""
