"""Simulated kitchens with known truth: scenario corpora, error injection, and rendered ConvLSTM training clips.

Owner: sim (agents)
Step:  simulation (offline; not part of the live pipeline)
Reads: contracts (scenario, ticket, allergen_profile); StateClient (load tests)
Emits: scenario files, interaction_event and entity JSONL, clip datasets
Rules: truth from the simulated world, never kitchen.graph; 5 causal event streams; 6 synthetic features allergen-blind; 8 late entities unknown
Tasks: sim-01 to sim-11

Not built yet.
"""
