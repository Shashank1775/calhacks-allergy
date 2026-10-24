"""Verdict service: on plate-at-pass or ticket amendment, query the graph, decide, and record the verdict atomically.

Also ScriptedVerdictSource: replays fixture verdicts and mistake patterns on a Clock, the fake that outputs builds against.

Owner: verdict (agents)
Step:  final analysis (verdict)
Reads: StateClient (ticket, entity zone, plate_binding); ProvenanceGraph; Clock
Emits: verdict and verdict_step via StateClient.record_verdict
Rules: 1 StateUnavailable, GraphUnavailable or stale plate holds; 9 amendments recompute from full history
Tasks: verdict-06, verdict-07, verdict-08, verdict-10

Not built yet.
"""
