"""decide(): the one plain-code function that turns ticket, graph and Laya inputs into PASS or HOLD.

Owner: verdict (agents)
Step:  final analysis (verdict)
Reads: ticket; allergen_reach (exact); unknown ancestors; laya_response or its failure; mode; input health; VerdictSettings
Emits: verdict (decision, reasons, flagged, chain, versions)
Rules: 1 any error or doubt holds; 2 Laya adds holds, never removes; 4 allergen units only, exceptions never suppress; 8 unknown ancestor holds
Tasks: verdict-01, verdict-04, verdict-08

Not built yet (phase gate: PLANNING).
"""
