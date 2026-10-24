"""Main loop and CLI: source -> detect + pose -> track -> identity + zones -> ObservationFrame -> consumers.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: camera.py, detect.py, pose.py, track.py, identity.py, zones.py, Clock
Emits: observation_frame to EntityStore.ingest (partner), kitchen/interactions and record.py; frame JSONL with --out
Rules: 1 the frame loop never blocks on a consumer; 5 strictly increasing t_ms
Tasks: vision-01 (replay CLI), vision-06 (wiring to the entity store)

Not built yet.
"""
