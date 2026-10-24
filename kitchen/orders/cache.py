"""Normalized-note parse cache, keyed by parser name and version, so a repeated note parses instantly and works offline.

Owner: orders (agents)
Step:  order intake (orders)
Reads: normalized note text, parser name and version
Emits: cached ParseResult (cached=true)
Rules: 1 a cache miss with Jev down is an error, never a guess
Tasks: orders-05

Not built yet (phase gate: PLANNING).
"""
