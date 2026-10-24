"""Simulator: kitchens whose truth we know, as test scenarios and as ConvLSTM training clips.

Owner: sim (agents)
Step:  simulator (offline; not part of the live pipeline)
Reads: contracts (scenario, ticket, allergen_profile, interaction_clip); a seed
Emits: scenario files, interaction_event and entity JSONL, clip datasets
Rules: truth comes from the simulated world, never from kitchen.graph or kitchen.verdict; time runs forward;
       blind classifier (clips carry no allergens and no realistic utensils); known-clean source (late entities are unknown)

Not built yet.
"""
