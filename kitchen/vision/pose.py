"""YOLOv8 pose wrists (keypoints 9 and 10) turned into hand detections with person_id and wrist.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: frames from camera.py
Emits: hand detections (kind=hand, class_name=wrist, person_id, wrist [x, y, conf])
Rules: 8 a person re-acquired gets new hand IDs (enforced in identity.py)
Tasks: vision-01

Not built yet.
"""
