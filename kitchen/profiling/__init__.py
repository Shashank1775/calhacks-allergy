"""Entity profiling: give every bin an allergen profile at shift start, with chef confirmation for unclear ingredients.

Owner: orders (agents)
Step:  entity profiling (orders)
Reads: bin label text or photo, prep sheets, optional online menu
Emits: AllergenProfile via StateClient.put_bin_profile; chef confirmations
Rules: 1 unclear never means clean; 4 profiles are allergen units
Tasks: orders-09, orders-04 (Apify spike)

Not built yet.
"""
