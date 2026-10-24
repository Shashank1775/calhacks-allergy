"""The simulated world: kitchen layout, bins with profiles, cooks, tools, recipes, and the true allergen process (seeded).

Owner: sim (agents)
Step:  simulation
Reads: allergen_units.json; allergen_profile and ticket shapes; a seed
Emits: world runs (true transfers, true washes, true plate contents)
Rules: 5 an entity carries only after true contact; 8 fresh plates leave a clean_stack zone; truth independent of kitchen.graph
Tasks: sim-01, sim-02

Not built yet (phase gate: PLANNING).
"""
