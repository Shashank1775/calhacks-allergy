# Cartesia Ink batch transcription for voice tickets

- **Status:** accepted
- **Date:** 2026-10-23
- **Decided by:** director, orders manager

## Context

A server can speak a ticket note instead of typing it. The transcript is confirmed by the server before it is parsed, so live partial transcripts add nothing.

## Decision

- **`CartesiaInkTranscriber` calls the batch endpoint** `POST https://api.cartesia.ai/stt` with `model=ink-whisper`, `language=en` and the finished WAV clip. Batch takes WAV as is, returns the whole text at once, and costs a sixth of realtime `ink-2`.
- **The key stays on the orders server.** The adapter reads `CARTESIA_API_KEY` and sends it as `Authorization: Bearer`. The tablet never sees it. A missing key fails at startup with a clear message.
- **Pin `Cartesia-Version: 2026-03-01`.** Bump it only after testing with a real key.
- **Fail toward typing.** Any network error, timeout, non-2xx reply, malformed reply or empty transcript raises `TranscriberUnavailable`; `/transcribe` returns an error, and the tablet says "type the note instead". Offline also means typing. The timeout starts at 10 s (unverified).
- **Not now:** realtime `ink-2` with keyterm prompting. Revisit only if the eval set shows allergen words misheard.

## Consequences

- The adapter is built and tested against a fake HTTP transport, with no network in tests.
- Unverified until a real key is used: batch latency and the exact response field names.
