"""Transcriber adapters: FakeTranscriber now, Cartesia Ink batch transcription later.

Owner: orders (agents)
Step:  orders
Reads: audio/wav from the tablet
Emits: transcript text (the server confirms it before it is parsed)
Rules: a transcript is only ever a note: it still goes through parsing and questions; any Ink error means "type it instead"
"""


class FakeTranscriber:
    """Transcriber that returns canned transcripts in order, then repeats the last one. For tests and offline demos."""

    def __init__(self, transcripts=("peanut allergy, she carries an EpiPen",)):
        self._transcripts = list(transcripts)
        self._calls = 0

    def transcribe(self, audio, *, sample_rate_hz):
        """Ignore the audio and return the next canned transcript."""
        text = self._transcripts[min(self._calls, len(self._transcripts) - 1)]
        self._calls += 1
        return text
