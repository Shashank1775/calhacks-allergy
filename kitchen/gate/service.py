"""Gate service on the Pi: a pure gate state machine, plus the loop that feeds it verdicts and health and drives the
driver and speaker.

Owner: outputs (agents)
Step:  gate and screens
Reads: StateClient.subscribe('verdict'), ticket revisions, the plate at the pass, vision GET /health
Emits: GateDriver.show, Speaker.say
Rules: fail closed: fault on silence, stale health or any exception; only a PASS for the current revision opens

Not built yet.
"""
