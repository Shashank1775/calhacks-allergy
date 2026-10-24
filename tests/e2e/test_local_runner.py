"""The local runner (tools/run_local.py): this harness's Kitchen played on its own clock and served over HTTP.

Owner: platform (agents)
Step:  end-to-end harness
Reads: tools/run_local.py (LocalRunner, loaded by path); contracts/scenarios/*.json; contracts/state-tables.md
Emits: nothing
Rules: fail closed: the gate ticks even after a scenario step fails; no unanswered questions and late changes recheck
       everything: the runner's tickets and amendments go through the Orders API, so the golden verdicts prove it
"""

import importlib.util
import json
from pathlib import Path

import pytest

from kitchen.gate.voice import number_words
from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS
from kitchen.shared.models import validate

RUNNER = Path(__file__).resolve().parents[2] / "tools" / "run_local.py"
KNIFE_SCENARIO = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == "knife-carries-peanut-to-plate")
WEB_ORIGIN = "http://localhost:5173"
ANSWERS = {"severity": "allergy", "trace_sensitive": True}

# Every table in contracts/state-tables.md, written out here so GET /state is checked against the spec, not itself.
STATE_TABLES = {
    "allergen",
    "zone",
    "ticket",
    "bin_profile",
    "entity",
    "entity_state",
    "plate_binding",
    "interaction",
    "verdict",
}


def load_runner():
    """tools/run_local.py as a module. tools/ isn't a package, so it's loaded by path."""
    spec = importlib.util.spec_from_file_location("run_local", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_local = load_runner()


def replay(path, clock=None):
    """A started LocalRunner, stepped every TICK_MS on a FakeClock through the whole scenario and one tick more."""
    clock = clock or FakeClock()
    runner = run_local.LocalRunner(path, clock, cors_origins=[WEB_ORIGIN])
    runner.start()
    while not runner.done():
        runner.step()
        clock.advance(run_local.TICK_MS)
    runner.step()
    return runner


def gate_changes(runner):
    """Every (state, ticket number) the gate showed, with consecutive repeats dropped."""
    changes = []
    for call in runner.shift.gate.calls:
        if not changes or changes[-1] != call:
            changes.append(call)
    return changes


def latest_revision(scenario, ticket):
    """The scenario's last revision of a ticket: its last amendment, or the ticket itself."""
    amendments = [a for a in scenario.get("amendments", []) if a["ticket_id"] == ticket["ticket_id"]]
    return amendments[-1] if amendments else ticket


@pytest.mark.safety
@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_runner_plays_each_golden_scenario_to_its_expected_verdicts(path):
    """On its own clock, with its tickets typed through the Orders API, the runner reaches every expected verdict in
    order, the gate shows each one with the ticket's number, and every hold is spoken."""
    runner = replay(path)
    scenario = runner.scenario
    state = runner.shift.state
    numbers = {ticket["ticket_id"]: ticket["number"] for ticket in scenario["tickets"]}
    ticket_of = {plate["plate_id"]: plate["ticket_id"] for plate in scenario["plates_at_pass"]}

    verdicts = [(v["plate_id"], v["ticket_revision"], v["decision"]) for v in state.list_verdicts()]
    assert verdicts == [(e["plate_id"], e["ticket_revision"], e["decision"]) for e in scenario["expected"]]

    shown = [(e["decision"], numbers[ticket_of[e["plate_id"]]]) for e in scenario["expected"]]
    assert gate_changes(runner) == [("idle", None), *shown]

    holds = [number for decision, number in shown if decision == "hold"]
    assert [line.split(",")[0] for line in runner.shift.speaker.lines] == [f"Hold {number_words(n)}" for n in holds]

    for ticket in scenario["tickets"]:
        wanted = latest_revision(scenario, ticket)
        stored = state.get_ticket(ticket["ticket_id"])
        assert (stored["revision"], stored["number"], stored["forbidden"]) == (
            wanted["revision"],
            wanted["number"],
            sorted(wanted["forbidden"]),
        )


def test_state_serves_every_table_read_only():
    """GET /state has exactly the tables of contracts/state-tables.md, each row in its contract shape, matching what
    the StateClient holds; nothing can be written through it."""
    runner = replay(KNIFE_SCENARIO)
    state = runner.shift.state
    body = runner.shift.orders.get("/state").json()

    assert body["scenario_id"] == "knife-carries-peanut-to-plate"
    assert isinstance(body["t_ms"], int)
    assert set(body["tables"]) == STATE_TABLES
    tables = body["tables"]

    assert tables["ticket"] == [validate(ticket, "ticket") for ticket in tables["ticket"]]
    assert tables["ticket"] == [state.get_ticket("t1")]
    assert tables["verdict"] == [validate(verdict, "verdict") for verdict in state.list_verdicts()]
    assert tables["interaction"] == [validate(event, "interaction_event") for event in state.list_interactions()]
    for profile in tables["bin_profile"]:
        validate({key: value for key, value in profile.items() if key != "entity_id"}, "allergen_profile")
    assert [(row["state_id"], row["entity_id"], row["zone"]) for row in tables["entity_state"]] == [
        ("s1", "plate-t1", "pass")
    ]
    assert tables["plate_binding"] == [{"plate_id": "plate-t1", "ticket_id": "t1", "t_ms": 500}]
    assert {row["id"] for row in tables["allergen"]} >= {"peanut", "milk", "sesame"}
    assert json.loads(json.dumps(body)) == body

    for method in ("POST", "PUT", "DELETE"):
        assert runner.shift.orders.request(method, "/state").status_code == 405


def test_a_tablet_ticket_shows_up_in_state_beside_the_scenarios():
    """The tablet and the expo board read the same tables: a ticket sent through the runner's Orders API is in the
    next GET /state, next to the scenario's own."""
    runner = replay(KNIFE_SCENARIO)

    sent = runner.shift.send_note("allergic to sesame and egg", number=12, answers=ANSWERS)

    tickets = runner.shift.orders.get("/state").json()["tables"]["ticket"]
    assert [ticket["ticket_id"] for ticket in tickets] == ["t1", "t12"]
    assert tickets[1] == validate(sent, "ticket")


def test_state_answers_the_web_origin_only():
    """The browser screens call the runner from the web dev server, so CORS lets that origin in, and only that one."""
    runner = run_local.LocalRunner(KNIFE_SCENARIO, FakeClock(), cors_origins=[WEB_ORIGIN])
    preflight = {"Origin": WEB_ORIGIN, "Access-Control-Request-Method": "GET"}

    assert runner.shift.orders.options("/state", headers=preflight).headers["access-control-allow-origin"] == WEB_ORIGIN
    other = runner.shift.orders.get("/state", headers={"Origin": "http://elsewhere.test"})
    assert "access-control-allow-origin" not in other.headers


@pytest.mark.safety
def test_a_failed_step_still_ticks_the_gate():
    """Fail closed: when a scenario step throws, the gate still ticks that step (closed), and the failed item isn't
    replayed on every later tick."""
    clock = FakeClock()
    runner = run_local.LocalRunner(KNIFE_SCENARIO, clock)
    runner.start()

    def camera_glitch(plate_id):
        raise RuntimeError("plate write failed (test)")

    runner.shift.plate_reaches_pass = camera_glitch
    clock.set(3000)
    with pytest.raises(RuntimeError):
        runner.step()

    assert runner.shift.gate_log == [(3000, "idle")]
    assert runner.done()
    clock.set(3250)
    assert runner.step() == "idle"
