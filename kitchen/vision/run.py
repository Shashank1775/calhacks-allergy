"""Main loop and CLI: source -> detect + pose -> track -> identity + zones -> ObservationFrame + change mask -> consumers.

Owner: vision (agents)
Step:  Vision (detection + tracking)
Reads: camera.py, detect.py, pose.py, track.py, identity.py, zones.py, kitchen/shared/change_mask.py, Clock
Emits: observation_frame and its change mask to EntityStore.ingest (partner), kitchen/interactions and record.py; frame JSONL with --out
Rules: 1 the frame loop never blocks on a consumer; 5 strictly increasing t_ms
Tasks: vision-01, vision-06, vision-13

Not built yet.
"""
