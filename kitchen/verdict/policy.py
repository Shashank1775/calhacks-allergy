"""decide(): turns the ticket and the graph's reachability into PASS or HOLD with every reason. Pure.
Also clean_confidence() and VerdictSettings, the one home for thresholds, timeouts and the staleness window.

Owner: verdict (agents)
Step:  verdict
Reads: ticket revision; allergen_reach; unknown ancestors; Laya's hold reasons (laya_check.laya_hold_reasons);
       input health; VerdictSettings
Emits: verdict (decision, confidence, reasons, flagged, chain, versions)
Rules: fail closed: any error or doubt holds and sets confidence to 0; plain-code verdict: Laya's reasons are only
       appended, never used to drop one;
       allergen units only (ticket.forbidden); an unknown ancestor holds

Not built yet.
"""
