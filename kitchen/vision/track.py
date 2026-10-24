"""ByteTrack behind a Tracker interface: per-frame detections in, track IDs out.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: detections from detect.py and pose.py
Emits: tracked detections with track_id
Rules: 8 a track lost beyond the buffer comes back as a new track
Tasks: vision-01

Not built yet.
"""
