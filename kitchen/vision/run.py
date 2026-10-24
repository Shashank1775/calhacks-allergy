"""Main loop and CLI: source -> detect -> track -> zones -> ObservationFrame + change mask -> consumers.

Owner: vision (agents)
Step:  vision
Reads: camera.py, detect.py, tracking.py, zones.py, kitchen/shared/change_mask.py, Clock
Emits: observation_frame and its change mask to EntityStore.ingest (partner), kitchen/interactions and record.py
Rules: the frame loop never blocks on a consumer; time runs forward

Not built yet.
"""
