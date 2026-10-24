"""Write an example exchange for every Orders endpoint to fakes/http/, for the tablet's mock server (outputs, MSW).

Owner: orders (agents)
Step:  orders
Reads: the real Orders API, run in-process on FakeLayaParser and in-memory state
Emits: kitchen/orders/fakes/http/<endpoint>.json, each {"method", "path", "request", "status", "response"}
Rules: examples come from the real code, so they always match the contracts; rerun after any API change
       (draft IDs are random, so they change on every run)

    uv run python -m kitchen.orders.fakes.make_examples
"""

import io
import json
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from kitchen.orders.api import InMemoryState, create_app
from kitchen.orders.laya_parser import FakeLayaParser
from kitchen.orders.tickets import TicketDesk
from kitchen.orders.transcribe import FakeTranscriber

OUT = Path(__file__).with_name("http")


class StartOfShift:
    """A clock stuck two minutes into the shift, so t_ms values don't change between runs."""

    def now_ms(self):
        return 120_000


def silent_wav():
    """A tenth of a second of 16 kHz silence as WAV bytes."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 1_600)
    return buffer.getvalue()


def record(client, name, method, path, request=None):
    """Call one endpoint and save the exchange as fakes/http/<name>.json."""
    response = client.request(method, path, json=request)
    example = {
        "method": method,
        "path": path,
        "request": request,
        "status": response.status_code,
        "response": response.json(),
    }
    (OUT / f"{name}.json").write_text(json.dumps(example, indent=2) + "\n")
    return response.json()


def main():
    desk = TicketDesk(FakeLayaParser(), InMemoryState(), StartOfShift())
    client = TestClient(create_app(desk, FakeTranscriber(), ["http://localhost:5173"]))
    OUT.mkdir(exist_ok=True)

    draft = record(
        client, "parse", "POST", "/parse", {"note": "allergic to nuts, carries an epipen", "source": "typed"}
    )
    draft_path = f"/drafts/{draft['draft_id']}"
    record(client, "send_unanswered", "POST", f"{draft_path}/send", {"number": 7})
    for question in draft["questions"]:
        answer = {"question_id": question["question_id"], "value": question["options"][0]["value"]}
        record(client, "answers", "POST", f"{draft_path}/answers", answer)
    record(client, "send", "POST", f"{draft_path}/send", {"number": 7})
    amended = record(client, "amend", "POST", "/tickets/t7/amend", {"note": "also no dairy"})
    for question in amended["questions"]:
        answer = {"question_id": question["question_id"], "value": question["options"][0]["value"]}
        client.post(f"/drafts/{amended['draft_id']}/answers", json=answer)
    record(client, "send_amendment", "POST", f"/drafts/{amended['draft_id']}/send", {})
    record(client, "health", "GET", "/health")

    transcript = client.post("/transcribe", content=silent_wav(), headers={"content-type": "audio/wav"})
    example = {
        "method": "POST",
        "path": "/transcribe",
        "request": "<audio/wav bytes>",
        "status": transcript.status_code,
        "response": transcript.json(),
    }
    (OUT / "transcribe.json").write_text(json.dumps(example, indent=2) + "\n")
    print(f"wrote {len(list(OUT.glob('*.json')))} examples to {OUT}")


if __name__ == "__main__":
    main()
