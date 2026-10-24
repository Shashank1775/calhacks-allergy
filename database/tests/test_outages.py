"""SpacetimeStateClient when the server misbehaves: errors, a hang, junk answers. A stub server, so it runs in CI.

Owner: partner (written by an agent at the owner's request)
Step:  database
Reads: database/state_client.py; kitchen/shared/contract_tests/state_client.py (one call per StateClient method)
Emits: nothing
Rules: fail closed: every method raises StateUnavailable on a 5xx, a timeout, a missing identity or an answer it
       can't read, never an empty or made-up result; a write that landed is never reported as failed
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from database.state_client import SpacetimeStateClient
from kitchen.shared.contract_tests.state_client import _CALLS
from kitchen.shared.interfaces import StateUnavailable

READS = sorted(name for name in _CALLS if name.startswith(("get_", "list_")) or name == "subscribe")
EMPTY_RESULT = [{"schema": {"elements": []}, "rows": []}]


class Stub(BaseHTTPRequestHandler):
    """Answers the client's three endpoints as the test says: identity, reducer calls and SQL."""

    behavior: dict = {}

    def do_POST(self):  # noqa: N802 (http.server's name)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/v1/identity":
            answer = self.behavior.get("identity", "token")
        elif "/call/" in self.path:
            answer = self.behavior.get("call", "ok")
        else:
            answer = self.behavior.get("sql", "empty")
        if answer == "hang":
            time.sleep(1.5)
            answer = "empty"
        status, body = {
            "token": (200, {"identity": "stub", "token": "stub-token"}),
            "ok": (200, None),
            "empty": (200, EMPTY_RESULT),
            "junk": (200, "not the answer you expected"),
            "503": (503, "unavailable"),
        }[answer]
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def stub():
    """A stub server on a free port; set `stub.behavior` before calling the client."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (Stub,), {"behavior": {}}))
    # A short poll interval, so shutdown() doesn't wait half a second per test.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield server
    server.shutdown()


def client_for(server, timeout_s: float = 0.5) -> SpacetimeStateClient:
    """A client pointed at the stub."""
    return SpacetimeStateClient(f"http://127.0.0.1:{server.server_address[1]}", "stub-db", timeout_s=timeout_s)


@pytest.mark.safety
@pytest.mark.parametrize("method", sorted(_CALLS))
def test_a_server_error_raises_state_unavailable(stub, method):
    stub.RequestHandlerClass.behavior = {"call": "503", "sql": "503"}
    with pytest.raises(StateUnavailable):
        _CALLS[method](client_for(stub))


@pytest.mark.safety
@pytest.mark.parametrize("method", sorted(_CALLS))
def test_a_server_that_never_answers_raises_state_unavailable(stub, method):
    """The gate needs every call to time out on its own (kitchen/gate/service.py): a hang is an outage."""
    stub.RequestHandlerClass.behavior = {"call": "hang", "sql": "hang"}
    with pytest.raises(StateUnavailable):
        _CALLS[method](client_for(stub, timeout_s=0.3))


@pytest.mark.safety
@pytest.mark.parametrize("identity", ["503", "junk"])
@pytest.mark.parametrize("method", sorted(_CALLS))
def test_no_identity_raises_state_unavailable(stub, method, identity):
    stub.RequestHandlerClass.behavior = {"identity": identity}
    with pytest.raises(StateUnavailable):
        _CALLS[method](client_for(stub))


@pytest.mark.safety
@pytest.mark.parametrize("method", READS)
def test_an_unreadable_answer_raises_state_unavailable(stub, method):
    """A read never turns an answer it can't decode into an empty table: the graph would see a clean kitchen."""
    stub.RequestHandlerClass.behavior = {"sql": "junk"}
    with pytest.raises(StateUnavailable):
        _CALLS[method](client_for(stub))


def test_a_write_that_landed_is_not_reported_failed_when_only_its_follow_up_poll_fails(stub):
    """Raising here would make orders think the ticket wasn't sent; its changes come with the next poll instead."""
    stub.RequestHandlerClass.behavior = {"sql": "empty"}
    client = client_for(stub)
    client.subscribe("verdict", lambda change: None)
    stub.RequestHandlerClass.behavior = {"call": "ok", "sql": "503"}

    _CALLS["record_verdict"](client)
    client.close()
