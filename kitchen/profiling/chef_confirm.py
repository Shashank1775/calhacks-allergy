"""Chef confirmation: one confirmation per unclear ingredient; answers update the bin profile and set confirmed_by_chef when none remain.

Owner: orders (agents)
Step:  entity profiling (orders)
Reads: unclear_ingredients from bin_labels; chef answers (Orders HTTP API /confirm)
Emits: StateClient.open_chef_confirmation / answer_chef_confirmation; updated AllergenProfile
Rules: 1 'none of these' is an explicit answer, never a default
Tasks: orders-09

Not built yet.
"""
