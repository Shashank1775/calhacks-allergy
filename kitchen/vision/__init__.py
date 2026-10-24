"""Camera, detection and tracking on the Pi: video frames in, ObservationFrames out.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: camera or video frames, zones calibration, Clock
Emits: observation_frame (to the partner EntityStore and kitchen/interactions), zone, plate_binding, Vision HTTP API
Rules: 1 fail closed, never guess a binding; 5 strictly increasing t_ms; 8 new ID on re-acquire
Tasks: vision-01 .. vision-11

Not built yet (phase gate: PLANNING).
"""
