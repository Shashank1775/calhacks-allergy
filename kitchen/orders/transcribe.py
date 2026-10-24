"""Transcriber adapters: TextFileTranscriber (fake) now, Cartesia Ink after the spike.

Owner: orders (agents)
Step:  order intake (orders)
Reads: audio/wav from the tablet (Transcriber)
Emits: transcript text (the server confirms it before parsing)
Rules: none (a transcript is only ever a note; it still goes through parse and questions)
Tasks: orders-04 (Cartesia Ink spike), orders-11

Not built yet.
"""
