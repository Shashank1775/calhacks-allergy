"""Verdict: decide PASS or HOLD for each plate at the pass, in plain code, and explain the chain.

Owner: verdict (agents)
Step:  verdict
Reads: ticket, entity, plate_binding (StateClient); allergen_reach and unknown ancestors (ProvenanceGraph)
Emits: verdict, chain included (StateClient.record_verdict)
Rules: fail closed; plain-code verdict; allergens, not contacts; known-clean source (an unknown ancestor holds);
       late changes recheck everything

Not built yet.
"""
