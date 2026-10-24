"""Local runner: the whole kitchen on one laptop, on in-memory state (no partner database): a golden scenario
replayed, or the camera live (--live).

Owner: platform (agents)
Step:  end-to-end harness (the same shift as tests/e2e, served over HTTP on a live clock)
Reads: one golden scenario (contracts/scenarios/<name>.json); Kitchen from tests/e2e/conftest.py (the wiring);
       Orders HTTP API requests from the tablet; ORDERS_CORS_ORIGINS; LAYA_MODEL_DIR and LAYA_PLATE_MODEL_DIR (the
       fine-tuned Laya checkpoints, optional); live: camera or video frames, a zones file, VISION_ settings
Emits: the Orders HTTP API, plus GET /state: a read-only snapshot of every table in contracts/state-tables.md, with
       mode "replay" or "live"; gate states and spoken holds on stdout (FakeGate, FakeSpeaker); live: the Vision API
Rules: fail closed: every verdict comes from kitchen.verdict, and the gate ticks every TICK_MS even after a failed
       scenario step, so it never freezes on its last state; no unanswered questions: the scenario's tickets are
       typed through the Orders API and its questions, like any tablet ticket; late changes recheck everything:
       amendments go through the Orders API too, which triggers the verdict service's recheck

The wiring is tests/e2e's Kitchen, so the runner and the end-to-end tests run the same thing: one FakeStateClient,
the Orders API on TicketDesk with a LayaParser, VerdictService with FakeProvenanceGraph and a LayaPlateCheck, and
GateService with FakeGate and FakeSpeaker. The two Laya parts are the fine-tuned checkpoints when LAYA_MODEL_DIR and
LAYA_PLATE_MODEL_DIR are set, and FakeLayaParser and FakeLayaCheck when they aren't. The scenario's world (bins,
plates, bindings, interaction edges) is loaded at start, its tickets are typed through the Orders API, and its
amendments and plates reaching the pass play on the clock. Vision isn't running, so the gate's health reader is the
clock itself.

    uv run python tools/run_local.py knife-carries-peanut-to-plate      # then point web/ at http://localhost:8001
    LAYA_MODEL_DIR=data/models/laya-v1 LAYA_PLATE_MODEL_DIR=data/models/laya-plate-v2 \\
        uv run --with laya==0.4.14 python tools/run_local.py knife-carries-peanut-to-plate   # the real models

Live mode (--live) drives the same services from a camera instead of a scenario: vision's frame loop feeds the
partner's EntityStore and the interactions step, the partner's ProvenanceGraph reads what they write, the verdict
service judges each plate that reaches the pass, and the gate watches vision's health. The partner's LiveRisk writes
each tool, hand and plate's propagated allergens into entity_state, so the camera overlays on /expo color a knife that
just scooped satay (display only: verdicts ask the graph directly). It serves the Vision API
(stream, health, start plate) on VISION_PORT next to the Orders API and GET /state:

    uv run python tools/run_local.py --live --webcam 0 --zones zones.json          # web/: WEB_VISION_URL=:8002 too
    uv run python tools/run_local.py --live --video data/recordings/prep.mp4 --zones zones.json
    VISION_DETECTOR=hailo .venv/bin/python tools/run_local.py --live --picamera --zones zones.json   # on the Pi

The interactions step plugs in by name (--interactions, default kitchen.interactions.events:live_consumer): a
factory called with (state, clock) that returns a frame consumer writing edges with StateClient.record_interaction.
Until it exists, or whenever it fails or falls behind, the graph refuses to answer, so every plate holds (fail
closed): without edges the graph would see no touches and could clear a plate that touched an allergen.
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
from kitchen.orders.api import parse_cors_origins as parse_orders_cors_origins
from kitchen.orders.laya_parser import FakeLayaParser, LocalLayaParser
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
from kitchen.shared.interfaces import GraphUnavailable
from kitchen.shared.models import CONTRACTS_DIR
from kitchen.verdict.laya_check import FakeLayaCheck, LocalLayaCheck

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


def laya_models(note_dir: str | None, plate_dir: str | None) -> tuple:
    """The note parser and the plate check: the fine-tuned Laya checkpoint in each folder, or the fake where the
    folder isn't set (None or empty, as in .env.example).

    A folder that is set but won't load raises LayaParserUnavailable or LayaCheckUnavailable, so the runner never
    quietly judges plates with a fake the owner didn't ask for.
    """
    parser = LocalLayaParser(note_dir) if note_dir else FakeLayaParser()
    plate_check = LocalLayaCheck(plate_dir) if plate_dir else FakeLayaCheck()
    return parser, plate_check


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

    def __init__(self, scenario, clock, cors_origins: list[str] | None = None, parser=None, plate_check=None) -> None:
        """`parser` and `plate_check` are the Laya parts (laya_models()); left out, they're the fakes."""
        self.scenario = read_scenario(scenario)
        # The world without its tickets: those are typed through the Orders API in start().
        world = dict(self.scenario, tickets=[], amendments=[])
        state = FakeStateClient()
        self.mirror = StateMirror(state)  # subscribed before anything is written
        load_scenario(state, world)
        graph = FakeProvenanceGraph(world)
        kitchen = load_harness().Kitchen
        self.shift = kitchen(
            state=state,
            graph=graph,
            scenario=world,
            clock=clock,
            cors_origins=cors_origins or [],
            parser=parser or FakeLayaParser(),
            plate_check=plate_check or FakeLayaCheck(),
        )
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
            "mode": "replay",
            "t_ms": self.shift.clock.now_ms(),
            "tables": self.mirror.snapshot(),
        }


# ---------- live mode: the camera drives the same services ----------

# The interactions step's factory, called as factory(state, clock), returning a vision frame consumer.
INTERACTIONS_STEP = "kitchen.interactions.events:live_consumer"

# The interactions step must have handled a frame this recently, or the graph can't be trusted. The same 1 s the gate
# allows vision's frames (GateSettings.health_limit_ms).
INTERACTIONS_STALE_MS = 1000


def load_factory(spec: str):
    """The callable named "module:attribute", e.g. "kitchen.interactions.events:live_consumer"."""
    module_name, _, attribute = spec.partition(":")
    if not attribute:
        raise ValueError(f"{spec!r} isn't module:attribute")
    return getattr(importlib.import_module(module_name), attribute)


class InteractionsSlot:
    """The interactions step as a vision frame consumer, with a health check the graph reads before it answers.

    The step comes from the factory named by `spec`. If it can't be imported or built, every frame is skipped and
    problem() says why, forever. Once it runs, problem() is None only while its last call succeeded and came within
    INTERACTIONS_STALE_MS.
    """

    def __init__(self, state, clock, spec=INTERACTIONS_STEP) -> None:
        """`spec` names the factory ("module:attribute"), or is the factory itself (tests)."""
        self.clock = clock
        self.spec = spec
        self.consumer = None
        self.missing: str | None = None
        self._lock = threading.Lock()
        self._last_ok_ms: int | None = None
        self._last_error_ms: int | None = None
        try:
            factory = spec if callable(spec) else load_factory(spec)
            self.consumer = factory(state, clock)
        except Exception as error:
            self.missing = f"the interactions step {spec} isn't available ({type(error).__name__}: {error})"
            logger.warning("%s: every plate will hold until it is", self.missing)

    def __call__(self, output) -> None:
        """Hand one frame to the step. An error is recorded, then raised again for the frame loop to log."""
        if self.consumer is None:
            return
        try:
            self.consumer(output)
        except Exception:
            with self._lock:
                self._last_error_ms = self.clock.now_ms()
            raise
        with self._lock:
            self._last_ok_ms = self.clock.now_ms()

    def problem(self) -> str | None:
        """Why the graph can't be trusted right now, or None when the step is healthy."""
        if self.missing:
            return self.missing
        with self._lock:
            last_ok, last_error = self._last_ok_ms, self._last_error_ms
        if last_error is not None and (last_ok is None or last_error >= last_ok):
            return "the interactions step failed on the latest frame"
        if last_ok is None:
            return "the interactions step hasn't handled a frame yet"
        if self.clock.now_ms() - last_ok > INTERACTIONS_STALE_MS:
            return f"the interactions step hasn't handled a frame in {INTERACTIONS_STALE_MS} ms"
        return None


class SerializedMoves:
    """A StateClient whose move_entity holds `lock`, and passes every other call straight through.

    The entity store syncs through this and LiveRisk holds the same lock while it reads an entity and writes its row,
    so a store row never lands in between and a risk row never carries an older zone or t_ms than the store just wrote
    (which would take a plate off the pass for the verdict service and the gate).
    """

    def __init__(self, state, lock) -> None:
        self._state = state
        self._lock = lock

    def move_entity(self, row) -> None:
        with self._lock:
            self._state.move_entity(row)

    def __getattr__(self, name: str):
        return getattr(self._state, name)


class GuardedGraph:
    """The partner's ProvenanceGraph, refusing to answer while the interactions step isn't healthy.

    Rule: fail closed. The graph only knows the touches the interactions step wrote. Without them it would report a
    plate untouched and the verdict could pass it, so it raises GraphUnavailable instead, which the verdict service
    turns into a HOLD with the reason.
    """

    def __init__(self, graph, slot: InteractionsSlot) -> None:
        self.graph = graph
        self.slot = slot

    def _check(self) -> None:
        problem = self.slot.problem()
        if problem:
            raise GraphUnavailable(f"can't see touches: {problem}")

    def reach(self, plate_id: str, as_of_t_ms: int):
        self._check()
        return self.graph.reach(plate_id, as_of_t_ms)

    def unknown_ancestors(self, plate_id: str, as_of_t_ms: int):
        self._check()
        return self.graph.unknown_ancestors(plate_id, as_of_t_ms)

    def subgraph(self, plate_id: str, as_of_t_ms: int):
        self._check()
        return self.graph.subgraph(plate_id, as_of_t_ms)

    def version(self) -> str:
        return self.graph.version()


class LiveRunner:
    """The kitchen driven by a camera: vision, the entity store, interactions, the graph, verdict and the gate on one
    FakeStateClient, with the Orders API and GET /state in `app` and the Vision API in `vision_app`.

    Feed frames with run_frames() (a source until it ends) or step_frame() (one frame; tests). Call sync() to write the
    entity store to the database and update_risk() to write the entities' live allergens (start() does both in the
    background), and tick() a few times a second for the gate.
    """

    def __init__(
        self,
        clock,
        zones: list,
        vision_settings=None,
        detector=None,
        parser=None,
        plate_check=None,
        interactions=INTERACTIONS_STEP,
        cors_origins: list[str] | None = None,
        vision_cors_origins: list[str] | None = None,
    ) -> None:
        from kitchen.entities.store import EntityStore
        from kitchen.gate.driver import FakeGate
        from kitchen.gate.service import GateService
        from kitchen.gate.voice import FakeSpeaker
        from kitchen.graph import LiveRisk, ProvenanceGraph
        from kitchen.orders.api import create_app
        from kitchen.orders.bins import save_bins
        from kitchen.orders.tickets import TicketDesk
        from kitchen.orders.transcribe import FakeTranscriber
        from kitchen.verdict.service import VerdictService
        from kitchen.vision import server as vision_server
        from kitchen.vision.common import VisionSettings
        from kitchen.vision.run import build_frame_loop, entity_store_consumer
        from kitchen.vision.zones import push_zones

        self.clock = clock
        self.zones = zones
        self.settings = vision_settings or VisionSettings()
        self.state = FakeStateClient()
        self.mirror = StateMirror(self.state)  # subscribed before anything is written
        push_zones(self.state, zones)
        save_bins(self.state)  # the hard-coded demo bins, as the Orders API writes them at startup

        self.interactions = InteractionsSlot(self.state, clock, interactions)
        self.graph = GuardedGraph(ProvenanceGraph(self.state), self.interactions)
        self.verdict_service = VerdictService(self.state, self.graph, plate_check or FakeLayaCheck(), clock)
        self.verdict_service.start()

        self.store = EntityStore(clock)
        write_lock = threading.Lock()  # the store's rows and live risk's rows, one at a time (SerializedMoves)
        self.store_state = SerializedMoves(self.state, write_lock)
        # The overlays' allergens; the verdict doesn't read what it writes.
        self.risk = LiveRisk(self.state, clock, write_lock=write_lock)
        self.status = None
        self.loop = build_frame_loop(
            self.settings, zones, [entity_store_consumer(self.store), self.interactions], detector=detector
        )
        self.status = vision_server.VisionStatus(self.loop.detector.backend, self.settings.track_lost_ms)
        self.loop.status = self.status

        self.gate = FakeGate(quiet=True)
        self.speaker = FakeSpeaker(quiet=True)
        self.gate_loop = GateService(self.state, self.gate, self.speaker, clock, read_health=self.last_frame_t_ms)

        desk = TicketDesk(parser or FakeLayaParser(), self.state, clock)
        self.app = create_app(desk, FakeTranscriber(), cors_origins=cors_origins or [])
        self.app.add_api_route("/state", self.state_snapshot, methods=["GET"])
        self.vision_app = vision_server.create_app(self.status, self.bind, vision_cors_origins or [])

    def last_frame_t_ms(self) -> int | None:
        """Vision's /health last_frame_t_ms, read in-process for the gate."""
        return self.status.health()["last_frame_t_ms"]

    def bind(self, ticket_id: str) -> str:
        """POST /plates/start: bind the one plate at the plating spot to the ticket, or refuse (vision's rules)."""
        from kitchen.vision.plates import start_plate

        return start_plate(
            self.state,
            self.status.recent_observations(),
            self.zones,
            ticket_id,
            self.clock.now_ms(),
            self.settings.plate_bind_max_age_ms,
            self.settings.track_lost_ms,
        )

    def start(self) -> None:
        """Sync the entity store to the database at 5 Hz, and keep the entities' live allergens current, in the
        background."""
        self.store.start_sync(self.store_state, hz=5.0)
        self.risk.start(hz=5.0)

    def step_frame(self, frame):
        """Run one frame through vision and its consumers."""
        return self.loop.step(frame)

    def run_frames(self, source) -> int:
        """Run frames until the source ends; returns how many. A detector error propagates, and frames stop."""
        return self.loop.run(source)

    def sync(self) -> None:
        """Write the entity store to the database once (what start() does at 5 Hz)."""
        self.store.sync_once(self.store_state)

    def update_risk(self) -> None:
        """Write the entities' live allergens to the database once (what start() does in the background)."""
        self.risk.update_once()

    def tick(self) -> str:
        """One gate loop tick; returns the state shown."""
        return self.gate_loop.tick()

    def state_snapshot(self) -> dict:
        """GET /state: every table, marked live so the screens don't show REPLAY."""
        return {"scenario_id": "live", "mode": "live", "t_ms": self.clock.now_ms(), "tables": self.mirror.snapshot()}


def live_source(args, clock):
    """The camera or video the live runner reads, played like a live camera."""
    from kitchen.vision.camera import PiCameraSource, VideoSource, WebcamSource
    from kitchen.vision.run import as_live

    if args.video:
        return as_live(VideoSource(args.video, clock), clock)
    if args.picamera:
        return PiCameraSource(clock)
    return WebcamSource(args.webcam, clock)


def run_live_frames(runner: LiveRunner, source) -> None:
    """The frame loop's thread. When it stops, frames stop, /health goes stale and the gate shows fault."""
    try:
        count = runner.run_frames(source)
        logger.warning("the camera source ended after %d frames: the gate will show fault", count)
    except Exception:
        logger.exception("the frame loop stopped on an error: the gate will show fault")


def run_live_gate(runner: LiveRunner) -> None:
    """Tick the gate every TICK_MS, forever, printing each gate change, each spoken line and interactions problems."""
    shown = None
    spoken = 0
    problem = None
    while True:
        try:
            runner.tick()
        except Exception:
            logger.exception("a gate tick failed")
        calls = runner.gate.calls
        if calls and calls[-1] != shown:
            shown = calls[-1]
            print(f"[gate] {shown[0]}" + (f" #{shown[1]}" if shown[1] is not None else ""), flush=True)
        for line in runner.speaker.lines[spoken:]:
            print(f"[voice] {line}", flush=True)
        spoken = len(runner.speaker.lines)
        if runner.interactions.problem() != problem:
            problem = runner.interactions.problem()
            print(f"[interactions] {problem or 'ok'}", flush=True)
        time.sleep(TICK_MS / 1000)


def main_live(args, origins: list[str], parser, plate_check) -> int:
    """Live mode's CLI: the camera, the Vision API on VISION_PORT, and the Orders API plus GET /state on --port."""
    import uvicorn

    from kitchen.shared.config import load_settings, log_settings
    from kitchen.vision.common import VisionSettings
    from kitchen.vision.detect import open_detector
    from kitchen.vision.run import serve_in_background
    from kitchen.vision.server import parse_cors_origins
    from kitchen.vision.zones import load_zones

    settings = load_settings(VisionSettings, "VISION_")
    log_settings(settings)
    zones = load_zones(args.zones)
    # The clock starts once the detector has loaded (seconds, for ONNX), as in vision's own CLI: otherwise the load
    # would use up shift_start_ms and every object on the counter would start with unknown history.
    detector = open_detector(settings)
    clock = SystemClock()
    runner = LiveRunner(
        clock,
        zones,
        vision_settings=settings,
        detector=detector,
        parser=parser,
        plate_check=plate_check,
        interactions=args.interactions,
        cors_origins=origins,
        vision_cors_origins=parse_cors_origins(settings.cors_origins),
    )
    runner.start()
    serve_in_background(runner.vision_app, settings.port)
    source = live_source(args, clock)
    threading.Thread(target=run_live_frames, args=(runner, source), daemon=True, name="frames").start()
    threading.Thread(target=run_live_gate, args=(runner,), daemon=True, name="gate").start()
    source_name = args.video or ("the Pi camera" if args.picamera else f"webcam {args.webcam}")
    print(
        f"run_local: LIVE from {source_name}, Orders API and GET /state on "
        f"http://localhost:{args.port}, Vision API on http://localhost:{settings.port}",
        flush=True,
    )
    uvicorn.run(runner.app, host="0.0.0.0", port=args.port, log_level="warning")
    return 0


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


def orders_cors_origins() -> list[str]:
    """The browser origins the Orders API and GET /state allow, from ORDERS_CORS_ORIGINS ("a, b"; spaces are fine)."""
    return parse_orders_cors_origins(os.environ.get("ORDERS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS))


def main(argv: list[str] | None = None) -> int:
    """CLI: start the runner on a golden scenario, the kitchen loop in a thread, and the HTTP server in front."""
    import uvicorn

    names = [path.stem for path in GOLDEN_SCENARIO_PATHS]
    cli = argparse.ArgumentParser(description="Run the whole kitchen locally: replay a golden scenario, or --live.")
    cli.add_argument("scenario", nargs="?", default="knife-carries-peanut-to-plate", choices=names)
    cli.add_argument("--port", type=int, default=DEFAULT_PORT)
    cli.add_argument("--live", action="store_true", help="drive the kitchen from a camera instead of a scenario")
    source = cli.add_mutually_exclusive_group()
    source.add_argument("--webcam", type=int, help="live: a webcam index, e.g. 0")
    source.add_argument("--video", type=Path, help="live: a video file, played like a live camera")
    source.add_argument("--picamera", action="store_true", help="live: the Raspberry Pi camera (on the Pi)")
    cli.add_argument("--zones", type=Path, help="live: zones.json (kitchen/vision/zones.py)")
    cli.add_argument("--interactions", default=INTERACTIONS_STEP, help="live: the interactions step's factory")
    args = cli.parse_args(argv)
    if args.live and (args.zones is None or (args.webcam is None and args.video is None and not args.picamera)):
        cli.error("--live needs --zones and one of --webcam, --video or --picamera")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # the scenario's own Orders API calls, one line each
    origins = orders_cors_origins()
    path = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == args.scenario)
    note_dir, plate_dir = os.environ.get("LAYA_MODEL_DIR"), os.environ.get("LAYA_PLATE_MODEL_DIR")
    print(
        f"run_local: notes parsed by {note_dir or 'FakeLayaParser'}, plates checked by {plate_dir or 'FakeLayaCheck'}",
        flush=True,
    )
    parser, plate_check = laya_models(note_dir, plate_dir)
    if args.live:
        return main_live(args, origins, parser, plate_check)
    runner = LocalRunner(path, SystemClock(), cors_origins=origins, parser=parser, plate_check=plate_check)
    print(f"run_local: {args.scenario} on http://localhost:{args.port} (GET /state), CORS for {origins}", flush=True)
    runner.start()
    threading.Thread(target=run_kitchen, args=(runner,), daemon=True, name="kitchen").start()
    uvicorn.run(runner.app, host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
