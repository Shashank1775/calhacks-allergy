"""The local runner (tools/run_local.py): this harness's Kitchen played on its own clock and served over HTTP.

Owner: platform (agents)
Step:  end-to-end harness
Reads: tools/run_local.py (LocalRunner, loaded by path); contracts/scenarios/*.json; contracts/state-tables.md;
       LAYA_MODEL_DIR and LAYA_PLATE_MODEL_DIR for the slow real-model run
Emits: nothing
Rules: fail closed: the gate ticks even after a scenario step fails; no unanswered questions and late changes recheck
       everything: the runner's tickets and amendments go through the Orders API, so the golden verdicts prove it
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from kitchen.gate.voice import number_words
from kitchen.orders.laya_parser import FakeLayaParser
from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS
from kitchen.shared.interfaces import LayaCheckUnavailable, LayaParserUnavailable
from kitchen.shared.models import CONTRACTS_DIR, validate
from kitchen.verdict.laya_check import FakeLayaCheck

RUNNER = Path(__file__).resolve().parents[2] / "tools" / "run_local.py"
KNIFE_SCENARIO = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == "knife-carries-peanut-to-plate")
DAIRY_SCENARIO = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == "late-amendment-adds-dairy")
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


def replay(path, clock=None, **laya):
    """A started LocalRunner, stepped every TICK_MS on a FakeClock through the whole scenario and one tick more.

    `laya` may set the runner's parser and plate_check; left out, they're the fakes.
    """
    clock = clock or FakeClock()
    runner = run_local.LocalRunner(path, clock, cors_origins=[WEB_ORIGIN], **laya)
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


def assert_golden_run(runner):
    """The runner reached every expected verdict in order, the gate showed each one with the ticket's number, every
    hold was spoken, and orders stored each ticket's latest revision with the scenario's forbidden allergens."""
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


@pytest.mark.safety
@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_runner_plays_each_golden_scenario_to_its_expected_verdicts(path):
    """On its own clock, with its tickets typed through the Orders API, the runner reaches every expected verdict."""
    assert_golden_run(replay(path))


@pytest.fixture(scope="module")
def real_laya():
    """The fine-tuned note parser and plate check from LAYA_MODEL_DIR and LAYA_PLATE_MODEL_DIR, loaded once.

    Skips unless both are set and the laya package is installed, as in CI.
    """
    note_dir, plate_dir = os.environ.get("LAYA_MODEL_DIR"), os.environ.get("LAYA_PLATE_MODEL_DIR")
    if not (note_dir and plate_dir):
        pytest.skip("set LAYA_MODEL_DIR and LAYA_PLATE_MODEL_DIR to run the runner on the real Laya models")
    # `python -m pytest`: a bare `pytest` under `uv run --with` starts the project's interpreter, which can't see laya.
    pytest.importorskip("laya", reason="run `uv run --with laya==0.4.14 python -m pytest ...` for the real models")
    parser, plate_check = run_local.laya_models(note_dir, plate_dir)
    return {"parser": parser, "plate_check": plate_check}


@pytest.mark.slow
@pytest.mark.safety
@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_runner_on_the_real_laya_models_reaches_every_golden_verdict(path, real_laya):
    """With laya-v1 reading the notes and laya-plate-v2 checking the plates, the runner still reaches every expected
    verdict, and each verdict records the plate model's checkpoint."""
    runner = replay(path, **real_laya)

    assert_golden_run(runner)
    assert {v["versions"]["laya"] for v in runner.shift.state.list_verdicts()} == {real_laya["plate_check"].version}


class RecordingParser:
    """The fake parser, noting every note it reads, so a test can see which parser the Orders API used."""

    def __init__(self):
        self.fake = FakeLayaParser()
        self.notes = []

    def parse_note(self, note, *, source="typed"):
        self.notes.append(note)
        return self.fake.parse_note(note, source=source)


class NamedPlateCheck(FakeLayaCheck):
    """The fake plate check under another checkpoint name, so a test can see which one judged the plate."""

    version = "stub-plate-check"


def test_runner_uses_the_parser_and_plate_check_it_is_given():
    """The Laya parts handed to LocalRunner are the ones the Orders API and the verdict service use."""
    parser = RecordingParser()
    runner = replay(KNIFE_SCENARIO, parser=parser, plate_check=NamedPlateCheck())

    assert parser.notes == ["peanut allergy"]
    assert [verdict["versions"]["laya"] for verdict in runner.shift.state.list_verdicts()] == ["stub-plate-check"]


def test_laya_models_are_the_fakes_only_when_no_folder_is_set():
    """Unset or empty folders give the fakes. A folder that won't load raises instead of falling back, so the runner
    never judges plates with a fake the owner didn't ask for."""
    for unset in (None, ""):
        parser, plate_check = run_local.laya_models(unset, unset)
        assert (type(parser), type(plate_check)) == (FakeLayaParser, FakeLayaCheck)

    with pytest.raises(LayaParserUnavailable):
        run_local.laya_models("no/such/laya-checkpoint", None)
    with pytest.raises(LayaCheckUnavailable):
        run_local.laya_models(None, "no/such/plate-checkpoint")


def test_state_serves_every_table_read_only():
    """GET /state has exactly the tables of contracts/state-tables.md, each holding the knife scenario's rows in
    their contract shape; nothing can be written through it."""
    runner = replay(KNIFE_SCENARIO)
    state = runner.shift.state
    body = runner.shift.orders.get("/state").json()

    assert body["scenario_id"] == "knife-carries-peanut-to-plate"
    assert isinstance(body["t_ms"], int)
    assert set(body["tables"]) == STATE_TABLES
    tables = body["tables"]

    units = json.loads((CONTRACTS_DIR / "allergen_units.json").read_text())["units"]
    assert tables["allergen"] == [{key: unit[key] for key in ("id", "name", "default_severity")} for unit in units]
    assert tables["zone"] == [
        {"zone_id": "pass", "kind": "pass", "polygon": [[0.8, 0.0], [1.0, 0.0], [1.0, 1.0], [0.8, 1.0]]}
    ]
    assert tables["ticket"] == [validate(state.get_ticket("t1"), "ticket")]
    assert tables["bin_profile"] == [
        {"entity_id": b["entity_id"], **b["allergen_profile"]} for b in runner.scenario["bins"]
    ]
    # Identity fields only: zone, box and allergens live in entity_state (contracts/state-tables.md).
    assert sorted(tables["entity"], key=lambda row: row["entity_id"]) == [
        {"entity_id": "bin-1", "kind": "bin", "label": "satay sauce", "origin": "shift_start", "first_seen_t_ms": 0},
        {"entity_id": "bin-3", "kind": "bin", "label": "steamed rice", "origin": "shift_start", "first_seen_t_ms": 0},
        {"entity_id": "plate-t1", "kind": "plate", "label": "plate-t1", "origin": "shift_start", "first_seen_t_ms": 0},
        {
            "entity_id": "tool-knife-1",
            "kind": "tool",
            "label": "tool-knife-1",
            "origin": "shift_start",
            "first_seen_t_ms": 0,
        },
    ]
    assert [(row["state_id"], row["entity_id"], row["zone"]) for row in tables["entity_state"]] == [
        ("s1", "plate-t1", "pass")
    ]
    assert tables["plate_binding"] == [{"plate_id": "plate-t1", "ticket_id": "t1", "t_ms": 500}]
    assert tables["interaction"] == [validate(event, "interaction_event") for event in state.list_interactions()]
    assert tables["verdict"] == [validate(verdict, "verdict") for verdict in state.list_verdicts()]

    for method in ("POST", "PUT", "DELETE"):
        assert runner.shift.orders.request(method, "/state").status_code == 405


def test_state_keeps_only_the_latest_ticket_revision():
    """A ticket row holds the latest revision (contracts/state-tables.md): after the amendment, GET /state has one t5
    row, at revision 2, so the expo board can't show revision 1's PASS as current."""
    runner = replay(DAIRY_SCENARIO)

    tickets = runner.shift.orders.get("/state").json()["tables"]["ticket"]

    assert [(ticket["ticket_id"], ticket["revision"], ticket["forbidden"]) for ticket in tickets] == [
        ("t5", 2, ["milk", "peanut"])
    ]


def test_scripted_answers_name_the_severity_of_the_tickets_threshold():
    """The runner answers a severity question with the severity the scenario ticket's pass threshold stands for, so
    orders sends that same threshold; traces always matter."""
    assert run_local.answers_for({"pass_threshold": 0.99}) == {"severity": "anaphylactic", "trace_sensitive": True}
    assert run_local.answers_for({"pass_threshold": 0.95}) == {"severity": "allergy", "trace_sensitive": True}
    assert run_local.answers_for({"pass_threshold": 0.90})["severity"] == "intolerance"
    assert run_local.answers_for({"pass_threshold": 0.80})["severity"] == "preference"


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


def test_orders_cors_origins_tolerate_spaces_and_a_trailing_comma(monkeypatch):
    """An iPad origin after "a, " used to be refused (#206): the runner strips each origin and drops empty ones."""
    monkeypatch.setenv("ORDERS_CORS_ORIGINS", f"{WEB_ORIGIN}, http://192.168.1.10:5173,")
    origins = run_local.orders_cors_origins()
    assert origins == [WEB_ORIGIN, "http://192.168.1.10:5173"]

    runner = run_local.LocalRunner(KNIFE_SCENARIO, FakeClock(), cors_origins=origins)
    preflight = {"Origin": "http://192.168.1.10:5173", "Access-Control-Request-Method": "GET"}
    response = runner.shift.orders.options("/state", headers=preflight)
    assert response.headers["access-control-allow-origin"] == "http://192.168.1.10:5173"


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
