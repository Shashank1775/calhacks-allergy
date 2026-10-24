"""Vision: camera frames in, tracked and identified bins, tools, hands and plates (ObservationFrames) out.

Owner: vision (agents)
Step:  vision
Reads: camera or video frames, the zones file, Clock
Emits: observation_frame and its change mask (to the partner's EntityStore and kitchen/interactions), zone, plate_binding,
       the Vision HTTP API
Rules: fail closed (never guess a plate binding); time runs forward (strictly increasing t_ms);
       known-clean source (a re-acquired track gets a new ID)

Built so far: the Hailo deploy path (bundle.py, hailo.py, parity.py). Not yet: camera.py, detect.py, tracking.py,
zones.py, record.py, server.py and run.py.
"""
