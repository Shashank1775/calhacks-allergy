"""Pass gate and voice: make each verdict physical (servo arm, red/green light, buzzer) and spoken ("Hold seven").

Owner: outputs (agents)
Step:  gate and screens
Reads: verdicts (StateClient.subscribe), ticket revisions, vision GET /health
Emits: GateDriver.show, Speaker.say
Rules: fail closed (closed by default, fault on a stale or broken feed); plain-code verdict (never recompute a decision);
       late changes recheck everything (an old revision's PASS holds)

Built so far: service.py (the gate state machine and GateService, with a --fake scenario replay), driver.py
(FakeGate) and voice.py (spoken lines, FakeSpeaker). Not yet: the GPIO driver (hardware), the Cartesia Sonic
speaker, and dropping a stale spoken line.
"""
