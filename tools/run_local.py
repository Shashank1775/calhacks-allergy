"""Local runner: the whole kitchen on one laptop, with no camera and no partner database.

Owner: platform (agents)
Step:  repo (end-to-end wiring; tests/e2e drives the same LocalKitchen on a FakeClock)
Reads: one golden scenario (contracts/scenarios/<name>.json); Orders HTTP API requests from the tablet;
       ORDERS_CORS_ORIGINS
Emits: the Orders HTTP API, plus GET /state: a read-only snapshot of every table in contracts/state-tables.md;
       gate states and spoken holds on stdout (FakeGate, FakeSpeaker)
Rules: fail closed: every verdict comes from kitchen.verdict on the shared FakeStateClient, and the gate ticks even
       when a scenario step fails, so it never freezes on its last state; no unanswered questions: the scenario's
       tickets go through TicketDesk and its question flow like any tablet ticket; late changes recheck everything:
       amendments go through TicketDesk.amend, which triggers the verdict service's recheck

One process, one FakeStateClient. TicketDesk (with FakeLayaParser) sends tickets, VerdictService judges plates with
FakeProvenanceGraph and FakeLayaCheck, and GateService drives FakeGate and FakeSpeaker. The scenario's bins, plates
and bindings are loaded at start, its tickets are sent through the desk, and its interaction edges, amendments and
plates reaching the pass play on the clock. Vision isn't running, so the gate's health reader is the clock itself.

    uv run python tools/run_local.py knife-carries-peanut-to-plate      # then point web/ at http://localhost:8001
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from dataclasses import asdict, is_dataclass

from kitchen.gate.driver import FakeGate
from kitchen.gate.service import GateService
from kitchen.gate.voice import FakeSpeaker
from kitchen.orders.api import DEFAULT_CORS_ORIGINS, DEFAULT_PORT, create_app
from kitchen.orders.laya_parser import FakeLayaParser
from kitchen.orders.tickets import PASS_THRESHOLDS, TicketDesk
from kitchen.orders.transcribe import FakeTranscriber
from kitchen.shared.clock import SystemClock
from kitchen.shared.fakes import (
    GOLDEN_SCENARIO_PATHS,
    FakeProvenanceGraph,
    FakeStateClient,
    load_scenario,
    read_scenario,
    scenario_timeline,
)
from kitchen.shared.fakes.state_client import TABLES
from kitchen.shared.models import CONTRACTS_DIR, EntityState, Zone
from kitchen.verdict.laya_check import FakeLayaCheck
from kitchen.verdict.service import VerdictService

logger = logging.getLogger("run_local")

# The gate polls a few times a second (kitchen/gate/README.md); the scenario plays on the same tick.
TICK_MS = 250

PASS_ZONE = Zone(zone_id="pass", kind="pass", polygon=[(0.8, 0.0), (1.0, 0.0), (1.0, 1.0), (0.8, 1.0)])

# Each ticket's pass threshold names the strictest severity on it, so a scripted answer can name it back.
SEVERITY_FOR_THRESHOLD = {threshold: severity for severity, threshold in PASS_THRESHOLDS.items()}

# The primary key of each table that holds one row per key (contracts/state-tables.md). entity_state is append-only
# and allergen is seeded from contracts/allergen_units.json, so neither is here.
TABLE_KEYS = {
    "zone": "zone_id",
    "ticket": "ticket_id",
    "bin_profile": "entity_id",
    "entity": "entity_id",
    "plate_binding": "plate_id",
    "interaction": "event_id",
    "verdict": "verdict_id",
}


# ---------- the scenario's tickets, through the orders desk ----------


def scripted_answer(question: dict, ticket: dict):
    """The option a server would tap so the draft says what the scenario's ticket says.

    "Keep X off?" is yes exactly when the ticket forbids X. Severity is the one the ticket's pass threshold stands
    for. Traces always matter, and an allowed-ingredient exception is always refused: both lean toward HOLD.
    """
    field = question["target"]["field"]
    values = [option["value"] for option in question["options"]]
    if field == "allergen_unit_id":
        return next((value for value in values if value in ticket["forbidden"]), None)
    if field == "severity":
        return SEVERITY_FOR_THRESHOLD[ticket["pass_threshold"]]
    if field == "trace_sensitive":
        return True
    return False


def answer_every_question(desk: TicketDesk, draft: dict, ticket: dict) -> dict:
    """Answer each of the draft's one-tap questions with scripted_answer(). Returns the draft after the last answer."""
    for question in draft["questions"]:
        draft = desk.answer(draft["draft_id"], question["question_id"], scripted_answer(question, ticket))
    return draft


def check_forbidden(sent: dict, wanted: dict) -> None:
    """Raise ValueError unless orders forbade exactly the allergens the scenario's ticket forbids.

    The pass threshold isn't compared: it comes from what the parser read in the note (the scenario's "peanut
    allergy" parses as allergy, 0.95, where the scenario says 0.99), and no golden verdict depends on it.
    """
    if sent["forbidden"] != sorted(wanted["forbidden"]):
        raise ValueError(
            f"orders sent {sent['ticket_id']} r{sent['revision']} forbidding {sent['forbidden']}, "
            f"but the scenario's ticket forbids {sorted(wanted['forbidden'])}"
        )


def send_ticket(desk: TicketDesk, ticket: dict) -> dict:
    """Type the scenario ticket's note, answer every question, and send it with the ticket's number."""
    draft = desk.parse(ticket["note"], ticket["source"])
    draft = answer_every_question(desk, draft, ticket)
    sent = desk.send(draft["draft_id"], ticket["number"])
    check_forbidden(sent, ticket)
    return sent


def change_text(original_note: str, amended_note: str) -> str:
    """What the server adds on an amendment: the amended note minus the original ("no peanut, also no dairy" →
    "also no dairy"). TicketDesk.amend joins the two again before it parses."""
    if amended_note.startswith(original_note):
        return amended_note[len(original_note) :].strip(" ,.;")
    return amended_note


def amend_ticket(desk: TicketDesk, original: dict, amendment: dict) -> dict:
    """Amend a sent ticket the way the tablet does: the change goes through TicketDesk.amend and the question flow."""
    draft = desk.amend(amendment["ticket_id"], change_text(original["note"], amendment["note"]))
    draft = answer_every_question(desk, draft, amendment)
    sent = desk.send(draft["draft_id"])
    check_forbidden(sent, amendment)
    return sent


# ---------- GET /state ----------


def allergen_rows() -> list[dict]:
    """The allergen table, seeded from contracts/allergen_units.json as the partner's database seeds it."""
    units = json.loads((CONTRACTS_DIR / "allergen_units.json").read_text())["units"]
    return [{"id": unit["id"], "name": unit["name"], "default_severity": unit["default_severity"]} for unit in units]


class StateMirror:
    """Every table in contracts/state-tables.md as JSON-ready rows, kept current from the StateClient's change feed.

    It reads only through subscribe(), the way a screen subscribed to SpacetimeDB sees the tables: one row per primary
    key (an update replaces it), and entity_state appended in order with a numbered state_id. Create it before
    anything is written, or it misses those rows.
    """

    def __init__(self, state) -> None:
        self._lock = threading.Lock()
        self._keyed: dict[str, dict[str, dict]] = {table: {} for table in TABLE_KEYS}
        self._entity_states: list[dict] = []
        self._allergens = allergen_rows()
        for table in TABLES:
            if table != "allergen":  # nothing writes it; it's seeded above
                state.subscribe(table, self.on_change)

    def on_change(self, change) -> None:
        """Store one changed row. Dataclass rows (zone, plate_binding, entity_state) become dicts."""
        row = asdict(change.row) if is_dataclass(change.row) else change.row
        with self._lock:
            if change.table == "entity_state":
                self._entity_states.append({"state_id": f"s{len(self._entity_states) + 1}", **row})
            else:
                self._keyed[change.table][row[TABLE_KEYS[change.table]]] = row

    def snapshot(self) -> dict[str, list[dict]]:
        """Every table's rows right now, keyed by table name. Rows are never edited after they're stored."""
        with self._lock:
            tables = {table: list(rows.values()) for table, rows in self._keyed.items()}
            tables["entity_state"] = list(self._entity_states)
        tables["allergen"] = list(self._allergens)
        return {table: tables[table] for table in TABLES}


# ---------- the kitchen ----------


class LocalKitchen:
    """Orders, verdict and gate on one FakeStateClient, playing one golden scenario on `clock`.

    Call start() once, then step() every tick. tests/e2e runs this on a FakeClock; main() runs it on a SystemClock.
    VerdictService runs only inside the state's change callbacks, which FakeStateClient delivers one at a time under
    its lock, so the HTTP threads and the kitchen thread never run it at once.
    """

    def __init__(self, scenario, clock) -> None:
        self.scenario = read_scenario(scenario)
        self.clock = clock
        self.state = FakeStateClient()
        self.mirror = StateMirror(self.state)
        self.desk = TicketDesk(FakeLayaParser(), self.state, clock)
        self.graph = FakeProvenanceGraph(self.scenario)
        self.verdicts = VerdictService(self.state, self.graph, FakeLayaCheck(), clock)
        self.gate_driver = FakeGate(quiet=True)
        self.speaker = FakeSpeaker(quiet=True)
        # Vision isn't running: the clock stands in for its /health last_frame_t_ms, which always advances.
        self.gate = GateService(self.state, self.gate_driver, self.speaker, clock, read_health=clock.now_ms)
        self.timeline = scenario_timeline(self.scenario)
        self._next_item = 0

    def start(self) -> None:
        """Calibrate the pass, start the verdict service, load the scenario's world and send its tickets.

        The world is everything but tickets and edges: tickets come from the orders desk, and edges are recorded on
        the clock as the scenario plays.
        """
        self.state.set_zones([PASS_ZONE])
        self.verdicts.start()
        load_scenario(self.state, {**self.scenario, "tickets": [], "events": []})
        for ticket in self.scenario["tickets"]:
            send_ticket(self.desk, ticket)

    def step(self) -> str:
        """Play every timeline item that's due, then tick the gate once. Returns the gate state shown."""
        now = self.clock.now_ms()
        try:
            while self._next_item < len(self.timeline) and self.timeline[self._next_item].t_ms <= now:
                item = self.timeline[self._next_item]
                self._next_item += 1  # past it first, so an item that fails isn't replayed every tick
                self.play(item, now)
        finally:
            shown = self.gate.tick()  # even after a failed item, so the gate never freezes on its last state
        return shown

    def play(self, item, now: int) -> None:
        """Write one timeline item: an interaction edge, an amendment, or a plate arriving at the pass."""
        if item.kind == "event":
            self.state.record_interaction(item.record)
        elif item.kind == "amendment":
            original = next(t for t in self.scenario["tickets"] if t["ticket_id"] == item.record["ticket_id"])
            amend_ticket(self.desk, original, item.record)
        elif item.kind == "plate_at_pass":
            arrival = EntityState(item.record["plate_id"], 1, now, PASS_ZONE.zone_id, None, {}, False)
            self.state.move_entity(arrival)

    def done(self) -> bool:
        """True once every timeline item has been played."""
        return self._next_item >= len(self.timeline)


def create_local_app(kitchen: LocalKitchen, cors_origins: list[str]):
    """The Orders HTTP API on the kitchen's desk, plus GET /state: a read-only snapshot of every table."""
    app = create_app(kitchen.desk, FakeTranscriber(), cors_origins)

    @app.get("/state")
    def state_snapshot():
        return {
            "scenario_id": kitchen.scenario["scenario_id"],
            "t_ms": kitchen.clock.now_ms(),
            "tables": kitchen.mirror.snapshot(),
        }

    return app


def run_kitchen(kitchen: LocalKitchen) -> None:
    """Step the kitchen every TICK_MS, forever, printing each gate change and each spoken line. Never raises."""
    shown = None
    spoken = 0
    while True:
        try:
            kitchen.step()
        except Exception:
            logger.exception("a scenario step failed; the kitchen keeps running")
        if kitchen.gate_driver.calls and kitchen.gate_driver.calls[-1] != shown:
            shown = kitchen.gate_driver.calls[-1]
            print(f"[gate] {shown[0]}" + (f" #{shown[1]}" if shown[1] is not None else ""), flush=True)
        for line in kitchen.speaker.lines[spoken:]:
            print(f"[voice] {line}", flush=True)
        spoken = len(kitchen.speaker.lines)
        time.sleep(TICK_MS / 1000)


def main(argv: list[str] | None = None) -> int:
    """CLI: start the kitchen on a golden scenario, the kitchen loop in a thread, and the HTTP server in front."""
    import uvicorn

    names = [path.stem for path in GOLDEN_SCENARIO_PATHS]
    parser = argparse.ArgumentParser(description="Run the whole kitchen locally on fakes, replaying a golden scenario.")
    parser.add_argument("scenario", nargs="?", default="knife-carries-peanut-to-plate", choices=names)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    path = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == args.scenario)
    kitchen = LocalKitchen(path, SystemClock())
    kitchen.start()
    threading.Thread(target=run_kitchen, args=(kitchen,), daemon=True, name="kitchen").start()

    origins = os.environ.get("ORDERS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    print(f"run_local: {args.scenario} on http://localhost:{args.port} (GET /state), CORS for {origins}", flush=True)
    uvicorn.run(create_local_app(kitchen, origins), host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
