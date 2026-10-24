"""Voice: turn a verdict into a short line with numbers as words ("Hold seven"), and say it.
Speaker implementations: FakeSpeaker now, Cartesia Sonic later. Non-blocking; stale lines are dropped.

Owner: outputs (agents)
Step:  gate and screens
Reads: Verdict, Ticket.number
Emits: audio out (Speaker)
Rules: voice is never on the critical path to HOLD

Not built yet.
"""
