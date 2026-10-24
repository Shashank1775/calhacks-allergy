"""decide(): the one plain-code function that turns the ticket and the graph's reachability into PASS or HOLD.

Owner: verdict (agents)
Step:  final analysis (verdict)
Reads: ticket; allergen_reach (exact); unknown ancestors; input health; VerdictSettings
Emits: verdict (decision, reasons, flagged, chain, versions)
Rules: 1 any error or doubt holds; 2 plain code, no model in the decision; 4 allergen units only, exceptions never suppress; 8 unknown ancestor holds
Tasks: verdict-01, verdict-04, verdict-08

Not built yet.
"""
