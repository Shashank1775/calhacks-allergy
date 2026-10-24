"""Simulated kitchens with known truth: scenario corpora, error injection, and Laya's training and evaluation data.

Owner: sim (agents)
Step:  simulation (offline; not part of the live pipeline)
Reads: contracts (scenario, ticket, allergen_profile); LayaClient fakes from kitchen.verdict; StateClient (load tests)
Emits: scenario files, interaction_event and entity JSONL, laya_request/laya_response pairs, eval reports
Rules: truth from the simulated world, never kitchen.graph; 5 causal event streams; 6 synthetic features allergen-blind; 8 late entities unknown
Tasks: sim-01 to sim-11

Not built yet (phase gate: PLANNING).
"""
