"""Detection: YOLOv8 detection and pose (wrists become hands), run locally with ONNX Runtime, plus the pre- and
post-processing every backend shares (letterbox, decoding, NMS, keypoints, class map).

Owner: vision (agents)
Step:  vision
Reads: frames from camera.py; the model bundle in data/models/<name>/; VISION_DETECTOR=onnx|hailo (default onnx)
Emits: detections (knife, spoon, fork, bowl, wrist) with normalized boxes, confidence, and person_id and wrist for hands
Rules: fail closed: never invent or interpolate detections; backends only run the network, so local results predict
       Hailo results

Not built yet.
"""
