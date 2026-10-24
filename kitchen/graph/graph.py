"""Graph structure: nodes and time-ordered edges, built and kept current from StateClient.

Owner: partner (human). Agents never edit this file.
Step:  provenance graph
Reads: entity, bin_profile, plate_binding, interaction_event (StateClient)
Emits: in-memory graph used by propagate.py, reachability.py and risk.py
Rules: 5 edges kept in time order; 8 entity origin recorded and never changed
Tasks: partner

Not built yet (phase gate: PLANNING).
"""
