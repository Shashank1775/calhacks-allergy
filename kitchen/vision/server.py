"""The Pi's HTTP API (POST /v1/plates/start, GET /stream.mjpg, GET /v1/health) and the scripted FakeVisionAPI.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: plates.py, the latest frame, frame-loop health stats
Emits: Vision HTTP API (contracts/apis.md#vision-http-api-on-the-pi)
Rules: 1 health last_frame_t_ms stops when frames stop; endpoints never block the frame loop
Tasks: vision-07, vision-08, vision-10

Not built yet (phase gate: PLANNING).
"""
