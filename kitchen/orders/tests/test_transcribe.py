"""Tests for the transcribers. Cartesia is reached through a fake HTTP transport: no network, no key."""

import httpx
import pytest

from kitchen.orders.transcribe import CartesiaInkTranscriber, FakeTranscriber, TranscriberUnavailable


def cartesia_replying(status, body, seen=None):
    """A CartesiaInkTranscriber whose 'network' always gives this reply, recording each request in `seen`."""

    def reply(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return CartesiaInkTranscriber("test-key", transport=httpx.MockTransport(reply))


def test_cartesia_returns_the_transcript():
    seen = []
    text = cartesia_replying(200, {"text": " no shrimp please "}, seen).transcribe(b"RIFF....", sample_rate_hz=16_000)
    assert text == "no shrimp please"
    request = seen[0]
    assert request.url == "https://api.cartesia.ai/stt"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["cartesia-version"] == "2026-03-01"
    assert b"ink-whisper" in request.content


@pytest.mark.safety
@pytest.mark.parametrize(("status", "body"), [(500, {"error": "boom"}), (200, {"text": "  "}), (200, {"words": []})])
def test_a_cartesia_failure_means_type_it_instead(status, body):
    with pytest.raises(TranscriberUnavailable):
        cartesia_replying(status, body).transcribe(b"RIFF....", sample_rate_hz=16_000)


def test_a_missing_key_fails_at_startup():
    with pytest.raises(ValueError):
        CartesiaInkTranscriber("")


def test_the_fake_repeats_its_last_transcript():
    fake = FakeTranscriber(["one", "two"])
    assert [fake.transcribe(b"", sample_rate_hz=16_000) for _ in range(3)] == ["one", "two", "two"]
