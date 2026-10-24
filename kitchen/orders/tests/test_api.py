"""Tests for the Orders HTTP API, through FastAPI's test client."""

import io
import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kitchen.orders.api import create_app, parse_cors_origins
from kitchen.orders.laya_parser import FakeLayaParser, LayaParserUnavailable
from kitchen.orders.tickets import TicketDesk
from kitchen.orders.transcribe import FakeTranscriber


class DownParser:
    """A LayaParser whose model is down."""

    def parse_note(self, note, *, source="typed"):
        raise LayaParserUnavailable("model not loaded (test)")


def client_for(state, clock, parser=None):
    desk = TicketDesk(parser or FakeLayaParser(), state, clock)
    app = create_app(desk, FakeTranscriber(["no shrimp please"]), ["http://localhost:5173"])
    return TestClient(app)


def wav_bytes():
    """A tenth of a second of silence as a WAV file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 1_600)
    return buffer.getvalue()


def test_note_to_ticket(state, clock, check_schema):
    client = client_for(state, clock)
    draft = client.post("/parse", json={"note": "allergic to nuts, carries an epipen", "source": "typed"}).json()
    assert check_schema("parse_result", draft) == []
    assert draft["sendable"] is False

    for question in draft["questions"]:
        draft = client.post(
            f"/drafts/{draft['draft_id']}/answers",
            json={"question_id": question["question_id"], "value": question["options"][0]["value"]},
        ).json()
    assert draft["sendable"] is True

    ticket = client.post(f"/drafts/{draft['draft_id']}/send", json={"number": 7}).json()
    assert check_schema("ticket", ticket) == []
    assert ticket["forbidden"] == ["peanut", "tree_nut"]


@pytest.mark.safety
def test_send_with_an_open_question_is_a_409(state, clock):
    client = client_for(state, clock)
    draft = client.post("/parse", json={"note": "no nuts", "source": "typed"}).json()
    response = client.post(f"/drafts/{draft['draft_id']}/send", json={"number": 1})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unanswered_questions"


@pytest.mark.safety
def test_laya_down_is_an_error_not_an_empty_draft(state, clock):
    response = client_for(state, clock, DownParser()).post("/parse", json={"note": "peanut allergy", "source": "typed"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "laya_unavailable"
    assert state.tickets == {}


def test_amend_returns_a_new_draft(state, clock):
    client = client_for(state, clock)
    draft = client.post("/parse", json={"note": "peanut allergy, EpiPen", "source": "typed"}).json()
    client.post(f"/drafts/{draft['draft_id']}/send", json={"number": 5})
    amended = client.post("/tickets/t5/amend", json={"note": "also no shrimp"}).json()
    assert amended["note"] == "peanut allergy, EpiPen. also no shrimp"


def test_a_bad_body_gets_the_standard_error(state, clock):
    response = client_for(state, clock).post("/parse", json={"note": "peanut allergy", "source": "fax"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_transcribe_returns_text(state, clock):
    client = client_for(state, clock)
    response = client.post("/transcribe", content=wav_bytes(), headers={"content-type": "audio/wav"})
    assert response.json() == {"text": "no shrimp please"}
    assert client.post("/transcribe", content=b"not audio").json()["error"]["code"] == "bad_audio"


def test_health_and_cors(state, clock):
    client = client_for(state, clock)
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.json() == {"ok": True}
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_origins_tolerate_spaces_and_a_trailing_comma(state, clock):
    """ORDERS_CORS_ORIGINS="a, b," must let both in: CORS compares origins exactly, so " b" would never match."""
    origins = parse_cors_origins("http://localhost:5173, http://192.168.1.10:5173,")
    assert origins == ["http://localhost:5173", "http://192.168.1.10:5173"]

    app = create_app(TicketDesk(FakeLayaParser(), state, clock), FakeTranscriber(), origins)
    preflight = {"Origin": "http://192.168.1.10:5173", "Access-Control-Request-Method": "POST"}
    response = TestClient(app).options("/parse", headers=preflight)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://192.168.1.10:5173"


def test_the_mock_server_examples_match_the_contracts(check_schema):
    examples = Path(__file__).resolve().parents[1] / "fakes" / "http"
    schema_for = {
        "parse": "parse_result",
        "answers": "parse_result",
        "amend": "parse_result",
        "send": "ticket",
        "send_amendment": "ticket",
    }
    for name, schema in schema_for.items():
        example = json.loads((examples / f"{name}.json").read_text())
        assert example["status"] == 200
        assert check_schema(schema, example["response"]) == []
    unanswered = json.loads((examples / "send_unanswered.json").read_text())
    assert (unanswered["status"], unanswered["response"]["error"]["code"]) == (409, "unanswered_questions")
