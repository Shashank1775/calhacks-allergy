"""The Pi's HTTP API (POST /plates/start, GET /stream.mjpg, GET /health) and FakeVisionAPI for outputs.

Owner: vision (agents)
Step:  vision
Reads: zones.py (binding), the latest frame, frame-loop health stats
Emits: the Vision HTTP API (contracts/apis.md)
Rules: fail closed: /health last_frame_t_ms stops advancing when frames stop; endpoints never block the frame loop

Not built yet.
"""
