"""Builds the Hailo deploy bundle: fixed-shape ONNX export, calibration frames, manifest.json, HEF fetch or compile step.

Owner: vision (agents)
Step:  Vision (detection + tracking)
Reads: chosen YOLOv8 det and pose weights; recorded kitchen footage for calibration frames
Emits: data/models/<name>/{model.onnx, calib/, manifest.json, model.hef once produced}; a data-manifest.json entry
Rules: none (no allergen logic); bundles stay out of git; manifest records hashes and tool versions
Tasks: vision-12 (bundle and parity script), vision-02 (which variants and input size)

Not built yet.
"""
