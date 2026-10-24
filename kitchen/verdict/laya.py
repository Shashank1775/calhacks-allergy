"""LayaClient adapter (timeout, schema validation), plus the ExactLaya and AdversarialLaya fakes.

Owner: verdict (agents)
Step:  final analysis (verdict)
Reads: laya_request (from ProvenanceGraph.subgraph); ProvenanceGraph.reach (ExactLaya)
Emits: laya_response, or LayaUnavailable
Rules: 1 timeout or malformed means unavailable; 2 output can only add flags
Tasks: verdict-03, verdict-04

Not built yet.
"""
