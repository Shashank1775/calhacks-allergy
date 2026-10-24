"""Transcriber adapters: CartesiaInkTranscriber (Cartesia Ink batch speech-to-text) and FakeTranscriber (canned text).

Owner: orders (agents)
Step:  orders
Reads: audio/wav from the tablet; CARTESIA_API_KEY
Emits: transcript text (the server confirms it before it is parsed)
Rules: a transcript is only ever a note: it still goes through parsing and questions;
       any Ink error means "type it instead" (TranscriberUnavailable), never an empty note
"""

import httpx

CARTESIA_STT_URL = "https://api.cartesia.ai/stt"
CARTESIA_VERSION = "2026-03-01"  # pinned; bump only after testing with a real key (docs/decisions/cartesia-speech.md)
TIMEOUT_S = 10.0


class TranscriberUnavailable(Exception):  # noqa: N818 (named in docs/decisions/cartesia-speech.md)
    """Speech-to-text failed. The tablet asks the server to type the note instead."""


class CartesiaInkTranscriber:
    """Batch transcription with Cartesia Ink (model ink-whisper): send the finished WAV clip, get the whole text back."""

    def __init__(self, api_key, transport=None):
        if not api_key:
            raise ValueError("CARTESIA_API_KEY is missing: set it in .env (see .env.example)")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}", "Cartesia-Version": CARTESIA_VERSION},
            timeout=TIMEOUT_S,
            transport=transport,  # tests pass an httpx.MockTransport; None means the real network
        )

    def transcribe(self, audio, *, sample_rate_hz):
        """Return the transcript. Any network error, non-2xx reply or empty text raises TranscriberUnavailable."""
        try:
            response = self._client.post(
                CARTESIA_STT_URL,
                files={"file": ("note.wav", audio, "audio/wav")},
                data={"model": "ink-whisper", "language": "en"},
            )
            response.raise_for_status()
            text = response.json()["text"].strip()  # field name unverified until a real key is used
        except (httpx.HTTPError, KeyError, ValueError, AttributeError) as error:
            raise TranscriberUnavailable(f"Cartesia Ink failed: {error}") from error
        if not text:
            raise TranscriberUnavailable("Cartesia Ink returned an empty transcript")
        return text


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
