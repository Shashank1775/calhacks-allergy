"""Final analysis: decide PASS or HOLD for each plate at the pass, and explain the chain.

Owner: verdict (agents)
Step:  final analysis (verdict)
Reads: ticket, entity, plate_binding (StateClient); allergen_reach, laya_request (ProvenanceGraph); laya_response (LayaClient)
Emits: verdict, chain_step, mistake_pattern (StateClient)
Rules: 1 any doubt or error holds; 2 models only tighten; 4 allergens, never contacts; 8 unknown ancestor holds; 9 amendments recheck everything
Tasks: verdict-01 to verdict-10

Not built yet (phase gate: PLANNING).
"""
