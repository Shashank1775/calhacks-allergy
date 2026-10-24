"""decide(): turns the ticket and the graph's reachability into PASS or HOLD with every reason. Pure.
Also clean_confidence() and VerdictSettings, the one home for thresholds, timeouts and the staleness window.

Owner: verdict (agents)
Step:  verdict
Reads: ticket revision; allergen_reach; unknown ancestors; input health; VerdictSettings
Emits: verdict (decision, confidence, reasons, flagged, chain, versions)
Rules: fail closed: any error or doubt holds and sets confidence to 0; plain-code verdict, no model in the decision;
       allergen units only, exceptions never suppress a flag; an unknown ancestor holds

Not built yet.
"""
