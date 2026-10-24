"""Orders HTTP API: /transcribe, /parse, /drafts/{id}/answers, /drafts/{id}/send, /tickets/{id}/amend, /health.

Owner: orders (agents)
Step:  orders
Reads: HTTP requests from the outputs tablet screen; LAYA_MODEL_DIR, ORDERS_CORS_ORIGINS, ORDERS_PORT
Emits: ParseResult and Ticket as JSON; the standard error body; CORS for ORDERS_CORS_ORIGINS
Rules: no unanswered questions: /send returns 409 unanswered_questions;
       fail closed: when Laya can't parse, /parse returns 503 laya_unavailable, never an empty result

    uv run python -m kitchen.orders.api     # the fine-tuned Laya if LAYA_MODEL_DIR is set, else the keyword fake
"""

import io
import os
import time
import wave
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kitchen.orders.bins import save_bins
from kitchen.orders.laya_parser import FakeLayaParser, LocalLayaParser
from kitchen.orders.tickets import OrdersError, TicketDesk
from kitchen.orders.transcribe import FakeTranscriber
from kitchen.shared.interfaces import LayaParserUnavailable, StateUnavailable

DEFAULT_CORS_ORIGINS = "http://localhost:5173"  # the web dev server (Vite)
DEFAULT_PORT = 8001


class ParseRequest(BaseModel):
    note: str
    source: Literal["typed", "voice"]


class AnswerRequest(BaseModel):
    question_id: str
    value: Any  # required; null is a real answer ("No, don't keep it off")


class SendRequest(BaseModel):
    number: int | None = None  # needed for a new ticket; an amendment keeps its ticket's number


class AmendRequest(BaseModel):
    note: str


def error_response(status, code, message):
    """The standard error body from contracts/apis.md (HTTP conventions)."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def wav_sample_rate(audio):
    """The sample rate from a WAV header. Raises OrdersError if the body isn't a WAV file."""
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            return wav.getframerate()
    except (wave.Error, EOFError) as error:
        raise OrdersError(400, "bad_audio", f"send audio/wav: {error}") from error


def create_app(desk, transcriber, cors_origins):
    """The Orders API around a TicketDesk and a Transcriber."""
    app = FastAPI(title="Orders API")
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(OrdersError)
    def orders_error(request, exc):
        return error_response(exc.status, exc.code, str(exc))

    @app.exception_handler(LayaParserUnavailable)
    def laya_unavailable(request, exc):
        return error_response(503, "laya_unavailable", str(exc))

    @app.exception_handler(StateUnavailable)
    def state_unavailable(request, exc):
        return error_response(503, "state_unavailable", str(exc))

    @app.exception_handler(RequestValidationError)
    def bad_request(request, exc):
        return error_response(400, "bad_request", str(exc.errors()))

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/transcribe")
    async def transcribe(request: Request):
        audio = await request.body()
        sample_rate_hz = wav_sample_rate(audio)
        try:
            text = transcriber.transcribe(audio, sample_rate_hz=sample_rate_hz)
        except Exception as error:
            raise OrdersError(
                502, "transcribe_failed", f"speech-to-text failed, type the note instead: {error}"
            ) from error
        return {"text": text}

    @app.post("/parse")
    def parse(body: ParseRequest):
        return desk.parse(body.note, body.source)

    @app.post("/drafts/{draft_id}/answers")
    def answer(draft_id: str, body: AnswerRequest):
        return desk.answer(draft_id, body.question_id, body.value)

    @app.post("/drafts/{draft_id}/send")
    def send(draft_id: str, body: SendRequest):
        return desk.send(draft_id, body.number)

    @app.post("/tickets/{ticket_id}/amend")
    def amend(ticket_id: str, body: AmendRequest):
        return desk.amend(ticket_id, body.note)

    return app


# ---------- stand-ins until the real database client and clock exist ----------


class InMemoryState:
    """Keeps tickets and bin profiles in memory so the Orders API runs on its own.

    Swap in the partner's StateClient once database/ ships one; it has the same three methods.
    """

    def __init__(self):
        self.tickets = {}
        self.bin_profiles = {}

    def put_ticket(self, ticket):
        self.tickets[ticket["ticket_id"]] = ticket  # the table keeps only the latest revision

    def get_ticket(self, ticket_id):
        return self.tickets.get(ticket_id)

    def put_bin_profile(self, entity_id, profile):
        self.bin_profiles[entity_id] = profile


class ShiftClock:
    """t_ms since this process started, standing in for the platform's SystemClock."""

    def __init__(self):
        self._start = time.monotonic()

    def now_ms(self):
        return int((time.monotonic() - self._start) * 1000)


def main():
    import uvicorn

    model_dir = os.environ.get("LAYA_MODEL_DIR")
    parser = LocalLayaParser(model_dir) if model_dir else FakeLayaParser()
    print(f"orders: parsing notes with {'Laya from ' + model_dir if model_dir else 'the keyword fake'}")

    state = InMemoryState()
    save_bins(state)  # hard-coded demo bins (kitchen/orders/bins.py)
    desk = TicketDesk(parser, state, ShiftClock())

    origins = os.environ.get("ORDERS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    app = create_app(desk, FakeTranscriber(), origins)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("ORDERS_PORT", DEFAULT_PORT)))


if __name__ == "__main__":
    main()
