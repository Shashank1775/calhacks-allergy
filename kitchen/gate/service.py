"""Gate service on the Pi: a pure gate state machine plus the loop that feeds it verdicts and health and drives the driver and speaker.

Owner: outputs (agents)
Step:  pass gate and voice (outputs)
Reads: StateClient.subscribe('verdict'), ticket revisions, plate at the pass, vision /v1/health
Emits: GateDriver.show, Speaker.say
Rules: 1 closed by default, fault on silence/stale health/exception; 2 renders decisions only; 9 only a current-revision PASS opens
Tasks: outputs-03 (state machine), outputs-05 (service)

Not built yet (phase gate: PLANNING).
"""
