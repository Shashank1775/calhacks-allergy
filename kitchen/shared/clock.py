"""The only way code reads time: t_ms since shift start, injectable so replays and tests are deterministic.

Owner: platform (agents)
Step:  shared
Reads: the system monotonic clock (SystemClock only)
Emits: SystemClock (live), FakeClock (tests and replay; advanced by hand)
Rules: logic never calls time.time() or datetime.now(); it takes a Clock (AGENTS.md, Engineering conventions)

Not built yet.
"""
