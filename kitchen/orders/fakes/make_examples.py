"""Write example responses for every Orders endpoint to fakes/http/, for the tablet's mock server (outputs, MSW).

Owner: orders (agents)
Step:  orders
Reads: the real Orders API, run in-process on FakeLayaParser and in-memory state
Emits: kitchen/orders/fakes/http/<endpoint>.json, each {"method", "path", "request", "status", "response"}
Rules: examples come from the real code, so they always match the contracts; rerun after any API change

    uv run python -m kitchen.orders.fakes.make_examples
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from kitchen.orders.api import InMemoryState, create_app
from kitchen.orders.laya_parser import FakeLayaParser
from kitchen.orders.tickets import TicketDesk
from kitchen.orders.transcribe import FakeTranscriber

OUT = Path(__file__).with_name("http")


class StartOfShift:
    """A clock stuck two minutes into the shift, so examples don't change between runs."""

    def now_ms(self):
        return 120_000


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
    print(f"wrote {len(list(OUT.glob('*.json')))} examples to {OUT}")


if __name__ == "__main__":
    main()
