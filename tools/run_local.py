"""Local runner: the whole kitchen on one laptop, with no camera and no partner database.

Owner: platform (agents)
Step:  end-to-end harness (the same shift as tests/e2e, served over HTTP on a live clock)
Reads: one golden scenario (contracts/scenarios/<name>.json); Kitchen from tests/e2e/conftest.py (the wiring);
       Orders HTTP API requests from the tablet; ORDERS_CORS_ORIGINS
Emits: the Orders HTTP API, plus GET /state: a read-only snapshot of every table in contracts/state-tables.md;
       gate states and spoken holds on stdout (FakeGate, FakeSpeaker)
Rules: fail closed: every verdict comes from kitchen.verdict, and the gate ticks every TICK_MS even after a failed
       scenario step, so it never freezes on its last state; no unanswered questions: the scenario's tickets are
       typed through the Orders API and its questions, like any tablet ticket; late changes recheck everything:
       amendments go through the Orders API too, which triggers the verdict service's recheck

The wiring is tests/e2e's Kitchen, so the runner and the end-to-end tests run the same thing: one FakeStateClient,
the Orders API on TicketDesk with FakeLayaParser, VerdictService with FakeProvenanceGraph and FakeLayaCheck, and
GateService with FakeGate and FakeSpeaker. The scenario's world (bins, plates, bindings, interaction edges) is loaded
at start, its tickets are typed through the Orders API, and its amendments and plates reaching the pass play on the
clock. Vision isn't running, so the gate's health reader is the clock itself.

    uv run python tools/run_local.py knife-carries-peanut-to-plate      # then point web/ at http://localhost:8001
"""

import argparse
import importlib.util
import json
import logging
import os
import sys
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from kitchen.orders.api import DEFAULT_CORS_ORIGINS, DEFAULT_PORT
from kitchen.orders.tickets import PASS_THRESHOLDS
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
from kitchen.shared.models import CONTRACTS_DIR

logger = logging.getLogger("run_local")

HARNESS = Path(__file__).resolve().parents[1] / "tests" / "e2e" / "conftest.py"

# The gate polls a few times a second (kitchen/gate/README.md); the scenario plays on the same tick.
TICK_MS = 250

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


def load_harness():
    """tests/e2e/conftest.py as a plain module, for its Kitchen. Neither tests/ nor tools/ is a package, so it's
    loaded by path, the way the harness's own determinism test loads it."""
    spec = importlib.util.spec_from_file_location("e2e_harness", HARNESS)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


# ---------- the scenario's tickets, typed through the Orders API ----------


def answers_for(ticket: dict) -> dict:
    """The one-tap answers that make a draft say what the scenario's ticket says, keyed by question field.

    Severity is the one the ticket's pass threshold stands for, and traces always matter (leaning toward HOLD).
    """
    return {"severity": SEVERITY_FOR_THRESHOLD[ticket["pass_threshold"]], "trace_sensitive": True}


def change_text(original_note: str, amended_note: str) -> str:
    """What the server adds on an amendment: the amended note minus the original ("no peanut, also no dairy" →
    "also no dairy"). The Orders API joins the two again before it parses."""
    if amended_note.startswith(original_note):
        return amended_note[len(original_note) :].strip(" ,.;")
    return amended_note


def check_forbidden(sent: dict, wanted: dict) -> dict:
    """Return the sent ticket, or raise ValueError unless orders forbade exactly what the scenario's ticket forbids.

    The pass threshold isn't compared: it comes from what the parser read in the note (the scenario's "peanut
    allergy" parses as allergy, 0.95, where the scenario says 0.99), and no golden verdict depends on it.
    """
    if sent["forbidden"] != sorted(wanted["forbidden"]):
        raise ValueError(
            f"orders sent {sent['ticket_id']} r{sent['revision']} forbidding {sent['forbidden']}, "
            f"but the scenario's ticket forbids {sorted(wanted['forbidden'])}"
        )
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


# ---------- the runner ----------


class LocalRunner:
    """One golden scenario played through tests/e2e's Kitchen on `clock`, with every table mirrored for GET /state.

    Call start() once, then step() every TICK_MS. tests/e2e runs it on a FakeClock and main() on a SystemClock.
    VerdictService runs only inside the state's change callbacks, which FakeStateClient delivers one at a time under
    its lock, so the HTTP threads and the kitchen thread never run it at once.
    """

    def __init__(self, scenario, clock, cors_origins: list[str] | None = None) -> None:
        self.scenario = read_scenario(scenario)
        # The world without its tickets: those are typed through the Orders API in start().
        world = dict(self.scenario, tickets=[], amendments=[])
        state = FakeStateClient()
        self.mirror = StateMirror(state)  # subscribed before anything is written
        load_scenario(state, world)
        graph = FakeProvenanceGraph(world)
        kitchen = load_harness().Kitchen
        self.shift = kitchen(state=state, graph=graph, scenario=world, clock=clock, cors_origins=cors_origins or [])
        self.app = self.shift.app
        self.app.add_api_route("/state", self.state_snapshot, methods=["GET"])
        self.timeline = [item for item in scenario_timeline(self.scenario) if item.kind != "event"]
        self._next_item = 0

    def start(self) -> None:
        """Type the scenario's tickets through the Orders API, as a server would on the tablet."""
        for ticket in self.scenario["tickets"]:
            check_forbidden(self.shift.send_note(ticket["note"], ticket["number"], answers_for(ticket)), ticket)

    def step(self) -> str:
        """Play every amendment and plate arrival that's due, then tick the gate once. Returns the gate state shown."""
        now = self.shift.clock.now_ms()
        try:
            while self._next_item < len(self.timeline) and self.timeline[self._next_item].t_ms <= now:
                item = self.timeline[self._next_item]
                self._next_item += 1  # past it first, so an item that fails isn't replayed every tick
                self.play(item)
        finally:
            shown = self.shift.tick()  # even after a failed item, so the gate never freezes on its last state
        return shown

    def play(self, item) -> None:
        """An amendment typed through the Orders API, or a plate arriving at the pass."""
        if item.kind == "amendment":
            original = next(t for t in self.scenario["tickets"] if t["ticket_id"] == item.record["ticket_id"])
            change = change_text(original["note"], item.record["note"])
            check_forbidden(self.shift.amend(item.record["ticket_id"], change, answers_for(item.record)), item.record)
        elif item.kind == "plate_at_pass":
            self.shift.plate_reaches_pass(item.record["plate_id"])

    def done(self) -> bool:
        """True once every amendment and plate arrival has been played."""
        return self._next_item >= len(self.timeline)

    def state_snapshot(self) -> dict:
        """GET /state: every table, plus the scenario being replayed and the shift's t_ms."""
        return {
            "scenario_id": self.scenario["scenario_id"],
            "t_ms": self.shift.clock.now_ms(),
            "tables": self.mirror.snapshot(),
        }


def run_kitchen(runner: LocalRunner) -> None:
    """Step the runner every TICK_MS, forever, printing each gate change and each spoken line. Never raises."""
    shown = None
    spoken = 0
    while True:
        try:
            runner.step()
        except Exception:
            logger.exception("a scenario step failed; the kitchen keeps running")
        calls = runner.shift.gate.calls
        if calls and calls[-1] != shown:
            shown = calls[-1]
            print(f"[gate] {shown[0]}" + (f" #{shown[1]}" if shown[1] is not None else ""), flush=True)
        for line in runner.shift.speaker.lines[spoken:]:
            print(f"[voice] {line}", flush=True)
        spoken = len(runner.shift.speaker.lines)
        time.sleep(TICK_MS / 1000)


def main(argv: list[str] | None = None) -> int:
    """CLI: start the runner on a golden scenario, the kitchen loop in a thread, and the HTTP server in front."""
    import uvicorn

    names = [path.stem for path in GOLDEN_SCENARIO_PATHS]
    parser = argparse.ArgumentParser(description="Run the whole kitchen locally on fakes, replaying a golden scenario.")
    parser.add_argument("scenario", nargs="?", default="knife-carries-peanut-to-plate", choices=names)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # the scenario's own Orders API calls, one line each
    origins = os.environ.get("ORDERS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    path = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == args.scenario)
    runner = LocalRunner(path, SystemClock(), cors_origins=origins)
    print(f"run_local: {args.scenario} on http://localhost:{args.port} (GET /state), CORS for {origins}", flush=True)
    runner.start()
    threading.Thread(target=run_kitchen, args=(runner,), daemon=True, name="kitchen").start()
    uvicorn.run(runner.app, host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
