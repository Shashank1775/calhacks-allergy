"""Model-agnostic Laya evaluation over any LayaClient, with a hard gate and a report.

Owner: sim (agents)
Step:  simulation
Reads: LayaClient (any implementation); the test split from laya_data.py
Emits: report.json and report.md (recall vs reference, precision, chain correctness, latency, malformed rate)
Rules: 2 gate: Laya never misses an allergen the reference finds (exit non-zero)
Tasks: sim-09

Not built yet (phase gate: PLANNING).
"""
