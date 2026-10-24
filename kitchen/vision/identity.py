"""Maps tracks to stable entity IDs (bin-<slot>, tool-<class>-<n>, hand-p<k>-left|right, plate-<n>) and sets origin.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: tracked detections, zones from zones.py, shift start
Emits: entity_id, origin and zone on every observation_frame detection
Rules: 8 re-acquired track = new ID, origin unknown; clean_stack only when leaving that zone; IDs never reused
Tasks: vision-04 (vision-01 ships a provisional version)

Not built yet.
"""
