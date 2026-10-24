"""Exact reachability: allergens reaching a plate with their chains, the plate's subgraph for Laya, and unknown-history ancestors.

Owner: partner (human). Agents never edit this file.
Step:  provenance graph
Reads: graph and propagated probabilities
Emits: allergen_reach (ProvenanceGraph.reach), laya_request (subgraph), unknown ancestor IDs
Rules: 1 failures raise GraphUnavailable; 5 chains strictly increasing in t_ms; 8 unwashed unknown ancestors always reported
Tasks: partner

Not built yet (phase gate: PLANNING).
"""
