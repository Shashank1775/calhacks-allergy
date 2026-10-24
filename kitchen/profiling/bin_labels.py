"""Bin profiling: Laya reads a bin's label or prep sheet and assigns allergen units with probabilities, listing unclear ingredients.

Owner: orders (agents)
Step:  entity profiling (orders)
Reads: label text or prep-sheet text (LayaParser.profile_bin)
Emits: AllergenProfile (bin_profile table) via StateClient.put_bin_profile
Rules: 1 unclear ingredients go to the chef, never assumed clean
Tasks: orders-09

Not built yet.
"""
