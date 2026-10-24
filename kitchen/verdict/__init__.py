"""Verdict: decide PASS or HOLD for each plate at the pass, in plain code, and explain the chain.

Owner: verdict (agents)
Step:  verdict
Reads: ticket, entity, plate_binding (StateClient); allergen_reach, unknown ancestors and the plate subgraph
       (ProvenanceGraph); Laya's plate check (LayaPlateCheck)
Emits: verdict, chain included (StateClient.record_verdict)
Rules: fail closed; plain-code verdict (Laya's plate check only adds holds); allergens, not contacts;
       known-clean source (an unknown ancestor holds); late changes recheck everything

Built so far: policy.py (decide()), service.py (the verdict service), laya_check.py (Laya's plate check) and
training/ (the plate-check dataset, evaluation and golden-scenario check).
"""
