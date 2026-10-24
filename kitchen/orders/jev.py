"""JevClient adapters: FakeJev (deterministic keyword parser) now, the real Jev adapter after the spike.

Owner: orders (agents)
Step:  order intake (orders)
Reads: note text; bin label or prep-sheet text (JevClient)
Emits: ParseResult, AllergenProfile
Rules: 1 JevUnavailable never becomes a guess
Tasks: orders-01 (FakeJev), orders-03 (Jev spike, OQ-5)

Not built yet (phase gate: PLANNING).
"""
