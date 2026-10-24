"""LayaParser adapters: FakeLayaParser (keyword parser, for tests) and the fine-tuned Laya adapter.

Owner: orders (agents)
Step:  orders
Reads: note text; bin label text
Emits: ParseResult, AllergenProfile (built from Laya's answers to typed questions)
Rules: fail closed: LayaParserUnavailable never becomes a guess

Not built yet.
"""
