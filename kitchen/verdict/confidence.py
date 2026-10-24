"""Clean-confidence: how sure we are that the plate carries none of the ticket's forbidden allergens.

Owner: verdict (agents)
Step:  final analysis (verdict)
Reads: allergen_reach probabilities for forbidden units; input health
Emits: verdict.confidence
Rules: 1 missing input means zero confidence; 2 Laya can never raise it; monotone, more evidence never raises it
Tasks: verdict-02 (ADR), verdict-01

Not built yet.
"""
