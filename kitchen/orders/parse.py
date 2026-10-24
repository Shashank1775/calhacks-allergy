"""Parse pipeline: normalize the note, look it up in the cache, call JevClient, and build the ParseResult draft.

Owner: orders (agents)
Step:  order intake (orders)
Reads: note text, source typed|voice; JevClient; cache
Emits: ParseResult
Rules: 3 every low-confidence field gets a question; never drop a mentioned allergen
Tasks: orders-01

Not built yet (phase gate: PLANNING).
"""
