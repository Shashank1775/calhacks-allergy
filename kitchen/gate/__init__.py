"""Pass gate and voice: make each verdict physical (servo arm, red/green light, buzzer) and spoken ("Hold seven").

Owner: outputs (agents)
Step:  pass gate and voice (outputs)
Reads: verdict and verdict_step (StateClient.subscribe), ticket revisions, vision /v1/health
Emits: GateDriver.show, Speaker.say
Rules: 1 closed by default, fault on stale or broken feed; 2 never recompute a decision; 9 old-revision PASS holds
Tasks: outputs-03, outputs-04, outputs-05, outputs-11

Not built yet (phase gate: PLANNING).
"""
