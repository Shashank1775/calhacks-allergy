"""Repeated Mistakes: group held chains by source, tool kind and allergen, and suggest a fix.

Owner: verdict (agents)
Step:  final analysis (verdict)
Reads: verdict, chain_step (StateClient)
Emits: mistake_pattern via StateClient.put_mistake_pattern
Rules: none (analysis only; never changes a verdict)
Tasks: M4 (not in the seed backlog yet)

Not built yet (phase gate: PLANNING).
"""
