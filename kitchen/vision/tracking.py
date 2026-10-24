"""Tracking and identity: ByteTrack track IDs become stable entity IDs (bin-<slot>, tool-<class>-<n>, hand-p<k>-left|right,
plate-<n>), each with an origin.

Owner: vision (agents)
Step:  vision
Reads: detections from detect.py, zones from zones.py, shift start
Emits: entity_id, origin and zone on every observation_frame detection
Rules: known-clean source: a track lost and re-acquired gets a new ID with origin unknown; clean_stack only when first
       seen leaving that zone; IDs are never reused

Not built yet.
"""
