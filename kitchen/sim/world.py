"""The simulated world: layout, bins with profiles, cooks, tools, recipes, the true allergen process, and kitchen
mistakes injected on purpose (skipped wash, shared utensil, bare-hand contact, late entity, track loss, amendment).

Owner: sim (agents)
Step:  simulator
Reads: allergen_units.json; a seed; the placeholder transfer rates in sim settings (SIM_)
Emits: world runs: true transfers, true washes, true plate contents
Rules: time runs forward: an entity carries an allergen only after a true contact; fresh plates leave a clean_stack zone;
       a mistake changes the world, never the expected answer

Not built yet.
"""
