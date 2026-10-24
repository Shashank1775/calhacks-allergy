"""LayaParser adapters: FakeLayaParser (keyword parser, for tests) and the fine-tuned Laya adapter.

Owner: orders (agents)
Step:  order intake (orders)
Reads: note text; bin label or prep-sheet text
Emits: ParseResult, AllergenProfile (built from Laya's answers to typed questions)
Rules: 1 LayaParserUnavailable never becomes a guess
Tasks: orders-01 (FakeLayaParser and the Laya adapter)

Not built yet.
"""
