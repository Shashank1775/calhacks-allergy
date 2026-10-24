"""Hailo-8, ready for when it's connected: builds the deploy bundle, runs the HEF with HailoRT, and checks ONNX-vs-HEF parity.

Owner: vision (agents)
Step:  vision
Reads: the chosen YOLOv8 weights; recorded kitchen footage for calibration frames; data/models/<name>/model.hef
Emits: data/models/<name>/{model.onnx, calib/, manifest.json, model.hef}; raw network outputs for detect.py;
       a parity report
Rules: fail closed: a device error stops frames (health goes stale, the gate faults), never fake detections;
       imported only when VISION_DETECTOR=hailo; bundles stay out of git

Not built yet.
"""
