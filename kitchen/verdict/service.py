"""Verdict service: on plate-at-pass or a ticket amendment, query the graph, decide, and record the verdict.
Also FakeVerdictFeed, which replays scenario verdicts on a Clock for outputs to build against.

Owner: verdict (agents)
Step:  verdict
Reads: StateClient (ticket, entity zone, plate_binding); ProvenanceGraph (incl. subgraph); LayaPlateCheck; Clock
Emits: verdict via StateClient.record_verdict
Rules: fail closed: StateUnavailable, GraphUnavailable, LayaCheckUnavailable or a stale plate holds;
       late changes recheck everything: amendments recompute from full history

Not built yet.
"""
