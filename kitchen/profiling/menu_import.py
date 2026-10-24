"""Optional menu import: an Apify Actor pulls the restaurant's online menu to seed prep sheets.

Owner: orders (agents)
Step:  entity profiling (orders)
Reads: menu URL (Orders HTTP API /v1/menu/import)
Emits: prep-sheet text for bin_labels; item count
Rules: none (seeds only; the chef still confirms)
Tasks: orders-04 (spike); build in M4

Not built yet (phase gate: PLANNING).
"""
