"""Transcriber adapters: FakeTranscriber now, Cartesia Ink batch transcription later.

Owner: orders (agents)
Step:  orders
Reads: audio/wav from the tablet
Emits: transcript text (the server confirms it before it is parsed)
Rules: a transcript is only ever a note: it still goes through parsing and questions; any Ink error means "type it instead"

Not built yet.
"""
