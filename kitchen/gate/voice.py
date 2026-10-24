"""Speaker implementations: ConsoleSpeaker now, Cartesia Sonic after the spike; non-blocking and drops stale lines.

Owner: outputs (agents)
Step:  pass gate and voice (outputs)
Reads: spoken line from lines.py
Emits: audio out (Speaker)
Rules: none (voice is never on the critical path to HOLD)
Tasks: outputs-04 (ConsoleSpeaker), outputs-11 (Sonic spike)

Not built yet.
"""
