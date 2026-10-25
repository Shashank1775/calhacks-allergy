"""The local runner's live mode: frames through vision, the entity store, the interactions slot, the real graph,
LiveRisk, verdict and the gate, on one FakeStateClient and a FakeClock.

Owner: platform (agents)
Step:  end-to-end harness
Reads: tools/run_local.py (LiveRunner, InteractionsSlot, loaded by path); a scripted detector instead of a camera
Emits: nothing
Rules: fail closed: with the interactions step missing, broken or behind, every plate holds, and when frames stop the
       gate faults; plain-code verdict: what passes or holds comes from kitchen.verdict on the partner's graph, and the
       allergens LiveRisk writes for the camera overlays change neither the verdict nor the gate
"""

import importlib.util
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from kitchen.shared.clock import FakeClock
from kitchen.shared.models import Zone
from kitchen.vision.common import Detection, SourceFrame, VisionSettings

RUNNER = Path(__file__).resolve().parents[2] / "tools" / "run_local.py"
ANSWERS = {"severity": "allergy", "trace_sensitive": True}

ZONES = [
    Zone("slot-1", "bin_slot", [(0.0, 0.0), (0.2, 0.0), (0.2, 0.4), (0.0, 0.4)]),
    Zone("plating-spot", "plating_spot", [(0.35, 0.3), (0.65, 0.3), (0.65, 0.7), (0.35, 0.7)]),
    Zone("pass", "pass", [(0.75, 0.0), (1.0, 0.0), (1.0, 1.0), (0.75, 1.0)]),
]
SETTINGS = VisionSettings(shift_start_ms=3000)  # everything first seen before 3 s starts clean (shift_start)
BIN = Detection("bowl", (0.05, 0.1, 0.15, 0.2), 0.9)  # bin-1: satay, peanut (kitchen/orders/bins.py)
KNIFE = Detection("knife", (0.25, 0.8, 0.3, 0.95), 0.9)
FRAME_MS = 100


def load_runner():
    spec = importlib.util.spec_from_file_location("run_local", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_local = load_runner()


class ScriptedDetector:
    """Returns whatever objects the test put on the counter, for every frame."""

    backend = "onnx"

    def __init__(self):
        self.objects = []

    def detect(self, frame):
        return list(self.objects), []


def plate_at(x_center: float) -> Detection:
    return Detection("bowl", (x_center - 0.05, 0.45, x_center + 0.05, 0.55), 0.9)


# ---------- stand-in interactions steps, built as factory(state, clock) ----------


def quiet_step(state, clock):
    """Healthy, and sees no touches."""
    return lambda output: None


def broken_step(state, clock):
    """Fails on every frame."""

    def consume(output):
        raise RuntimeError("zone rules crashed")

    return consume


def peanut_knife_step(state, clock):
    """Reports the knife scooping satay from bin-1, then touching the plate, once both have happened (t >= 1300)."""
    written = []

    def consume(output):
        if written or output.observation["t_ms"] < 1300:
            return
        classifier = {"name": "zone_rules", "version": "test"}
        state.record_interaction(
            {
                "event_id": "e-scoop",
                "t_start_ms": 1100,
                "t_end_ms": 1150,
                "a_id": "tool-knife-1",
                "b_id": "bin-1",
                "action": "transfer.scoop",
                "probability": 0.95,
                "direction": "b_to_a",
                "classifier": classifier,
            }
        )
        state.record_interaction(
            {
                "event_id": "e-touch",
                "t_start_ms": 1200,
                "t_end_ms": 1250,
                "a_id": "tool-knife-1",
                "b_id": "plate-1",
                "action": "transfer.contact",
                "probability": 0.9,
                "direction": "both",
                "classifier": classifier,
            }
        )
        written.append(True)

    return consume


# ---------- driving a shift ----------


class Shift:
    """A LiveRunner on a FakeClock, fed scripted frames, with a peanut-allergy ticket for table 1."""

    def __init__(self, interactions, zones_path: Path | None = None):
        self.clock = FakeClock(0)
        self.detector = ScriptedDetector()
        self.runner = run_local.LiveRunner(
            self.clock,
            ZONES,
            vision_settings=SETTINGS,
            detector=self.detector,
            interactions=interactions,
            zones_path=zones_path,
        )
        self.orders = TestClient(self.runner.app)
        self._frame_id = 0

    def frame(self, objects: list) -> None:
        """One frame of `objects`, FRAME_MS after the last, through vision and the entity store only: nothing is written
        to the database yet."""
        self.detector.objects = objects
        self.clock.advance(FRAME_MS)
        rgb = np.full((96, 128, 3), self._frame_id % 256, dtype=np.uint8)
        self.runner.step_frame(SourceFrame(self._frame_id, self.clock.now_ms(), rgb))
        self._frame_id += 1

    def frames(self, count: int, objects: list) -> None:
        """`count` frames of `objects`, FRAME_MS apart, each synced to the database (entity rows, then live risk) and
        followed by a gate tick."""
        for _ in range(count):
            self.frame(objects)
            self.runner.sync()
            self.runner.update_risk()
            self.runner.tick()

    def send_ticket(self, note: str, number: int) -> dict:
        draft = self.orders.post("/parse", json={"note": note, "source": "typed"}).json()
        for question in draft["questions"]:
            draft = self.orders.post(
                f"/drafts/{draft['draft_id']}/answers",
                json={"question_id": question["question_id"], "value": ANSWERS[question["target"]["field"]]},
            ).json()
        response = self.orders.post(f"/drafts/{draft['draft_id']}/send", json={"number": number})
        assert response.status_code == 200, response.json()
        return response.json()

    def plate_to_pass(self) -> str:
        """Set up the counter, bind the plate for table 1, slide it to the pass. Returns the plate's ID."""
        self.frames(12, [BIN, KNIFE, plate_at(0.5)])
        ticket = self.send_ticket("peanut allergy", 1)
        plate_id = self.runner.bind(ticket["ticket_id"])
        for step in range(1, 20):  # small steps, so the tracker keeps the same plate
            self.frames(1, [BIN, KNIFE, plate_at(0.5 + 0.02 * step)])
        self.frames(3, [BIN, KNIFE, plate_at(0.88)])
        return plate_id

    def verdict(self, plate_id: str) -> dict:
        verdicts = [v for v in self.runner.state.list_verdicts() if v["plate_id"] == plate_id]
        assert verdicts, "no verdict was recorded for the plate at the pass"
        return max(verdicts, key=lambda v: v["decided_t_ms"])

    def rows_of(self, entity_id: str) -> list[dict]:
        """The entity's entity_state rows in GET /state, in the order they were written."""
        rows = self.orders.get("/state").json()["tables"]["entity_state"]
        return [row for row in rows if row["entity_id"] == entity_id]

    def overlay_row(self, entity_id: str) -> dict:
        """What /expo draws the entity from: its latest row, the highest t_ms and the one written last on a tie
        (web/src/gate.ts latest_states)."""
        return max(enumerate(self.rows_of(entity_id)), key=lambda numbered: (numbered[1]["t_ms"], numbered[0]))[1]


# ---------- tests ----------


@pytest.mark.safety
def test_without_the_interactions_step_every_plate_holds():
    """Fail closed: no edges would look like no touches, so the graph refuses to answer and the plate holds."""
    shift = Shift(interactions="kitchen.interactions.no_such_module:live_consumer")
    plate_id = shift.plate_to_pass()

    verdict = shift.verdict(plate_id)
    assert verdict["decision"] == "hold"
    assert any("interactions step" in reason.get("detail", "") for reason in verdict["reasons"])
    assert shift.runner.tick() == "hold"


def test_a_clean_plate_passes_when_interactions_see_no_touch():
    shift = Shift(interactions=quiet_step)
    plate_id = shift.plate_to_pass()

    assert plate_id == "plate-1"
    assert shift.verdict(plate_id)["decision"] == "pass"
    assert shift.runner.tick() == "pass"


@pytest.mark.safety
def test_a_knife_that_scooped_satay_holds_the_peanut_plate():
    shift = Shift(interactions=peanut_knife_step)
    plate_id = shift.plate_to_pass()

    verdict = shift.verdict(plate_id)
    assert verdict["decision"] == "hold"
    assert {"code": "forbidden_allergen", "allergen_unit_id": "peanut"} in [
        {k: r[k] for k in ("code", "allergen_unit_id") if k in r} for r in verdict["reasons"]
    ]
    assert [step["from_id"] for step in verdict["chain"]][:1] == ["bin-1"]
    assert shift.runner.tick() == "hold"


def test_the_knife_that_scooped_satay_shows_peanut_on_the_overlay_and_a_wash_lowers_it():
    shift = Shift(interactions=peanut_knife_step)
    plate_id = shift.plate_to_pass()

    knife = shift.overlay_row("tool-knife-1")
    assert knife["probabilities"]["peanut"] >= 0.5  # /expo colors a box red from this
    assert knife["unknown"] is False and knife["box"] is not None  # still drawn where the camera sees it
    assert shift.runner.state.get_entity("tool-knife-1")["allergen_profile"]["probabilities"]["peanut"] >= 0.5

    shift.runner.state.record_interaction(
        {
            "event_id": "e-rinse",
            "t_start_ms": 3000,
            "t_end_ms": 3050,
            "a_id": "tool-knife-1",
            "b_id": "tool-knife-1",
            "action": "clean.wash",
            "probability": 0.98,
            "direction": "a_to_b",
            "classifier": {"name": "zone_rules", "version": "test"},
        }
    )
    shift.frames(2, [BIN, KNIFE, plate_at(0.88)])

    assert shift.overlay_row("tool-knife-1")["probabilities"]["peanut"] < 0.5
    assert shift.overlay_row("plate-1")["probabilities"]["peanut"] >= 0.5  # the plate keeps what the knife gave it
    assert shift.verdict(plate_id)["decision"] == "hold"


def test_start_keeps_the_overlay_current_in_the_background(monkeypatch):
    shift = Shift(interactions=peanut_knife_step)
    monkeypatch.setattr(shift.runner, "update_risk", lambda: None)  # nothing writes live risk by hand
    shift.plate_to_pass()
    assert "peanut" not in shift.overlay_row("tool-knife-1")["probabilities"]

    shift.runner.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and "peanut" not in shift.overlay_row("tool-knife-1")["probabilities"]:
            time.sleep(0.02)
        assert shift.overlay_row("tool-knife-1")["probabilities"]["peanut"] >= 0.5
    finally:
        shift.runner.risk.stop()
        shift.runner.store.stop_sync()


@pytest.mark.safety
def test_risk_rows_change_neither_the_gate_nor_the_verdicts():
    """LiveRisk's rows copy the plate's zone and time, so the plate is judged once, where it was, and the gate holds."""
    shift = Shift(interactions=peanut_knife_step)
    plate_id = shift.plate_to_pass()

    assert [row["caused_by"] for row in shift.rows_of(plate_id) if row["caused_by"]] == ["e-touch"]  # a risk row landed
    assert shift.runner.state.get_entity(plate_id)["zone"] == "pass"
    assert [v["trigger"] for v in shift.runner.state.list_verdicts() if v["plate_id"] == plate_id] == ["plate_at_pass"]
    assert shift.runner.tick() == "hold"


def knife_edge(event_id, action, t_ms, other_id, probability, direction):
    """An edge between the knife and `other_id`, lasting 50 ms."""
    return {
        "event_id": event_id,
        "t_start_ms": t_ms,
        "t_end_ms": t_ms + 50,
        "a_id": "tool-knife-1",
        "b_id": other_id,
        "action": action,
        "probability": probability,
        "direction": direction,
        "classifier": {"name": "zone_rules", "version": "test"},
    }


@pytest.mark.safety
def test_a_risk_row_written_as_the_plate_reaches_the_pass_neither_moves_it_nor_judges_it_twice():
    """The entity store puts the plate on the pass between LiveRisk's read of the plate and its write. A risk row that
    copied the older zone would take the plate off the pass for the verdict service (a second plate_at_pass judgment)
    and for the gate (no plate at the pass). The runner has the store's rows wait for the risk row instead."""
    shift = Shift(interactions=quiet_step)
    state = shift.runner.state
    shift.frames(12, [BIN, KNIFE, plate_at(0.5)])
    plate_id = shift.runner.bind(shift.send_ticket("peanut allergy", 1)["ticket_id"])
    for step in range(1, 13):  # to x = 0.74, just short of the pass
        shift.frames(1, [BIN, KNIFE, plate_at(0.5 + 0.02 * step)])
    state.record_interaction(knife_edge("e-scoop", "transfer.scoop", 1100, "bin-1", 0.95, "b_to_a"))
    shift.runner.update_risk()
    shift.frame([BIN, KNIFE, plate_at(0.76)])  # on the pass for the entity store, not yet in the database
    state.record_interaction(knife_edge("e-touch", "transfer.contact", 1200, plate_id, 0.9, "both"))

    real_get_entity = state.get_entity
    sync_threads = []

    def get_entity_as_the_store_syncs(entity_id):
        entity = real_get_entity(entity_id)
        if entity_id == plate_id and not sync_threads:
            sync_threads.append(threading.Thread(target=shift.runner.sync))
            sync_threads[0].start()
            sync_threads[0].join(timeout=0.3)  # a store that isn't held up writes inside the gap
        return entity

    state.get_entity = get_entity_as_the_store_syncs
    shift.runner.update_risk()
    sync_threads[0].join()
    state.get_entity = real_get_entity

    rows = shift.rows_of(plate_id)
    assert [row["t_ms"] for row in rows] == sorted(row["t_ms"] for row in rows)  # no row takes the plate back in time
    assert [row["caused_by"] for row in rows if row["caused_by"]] == ["e-touch"]  # the risk row did land
    assert state.get_entity(plate_id)["zone"] == "pass"
    shift.frames(2, [BIN, KNIFE, plate_at(0.76)])
    assert [v["trigger"] for v in state.list_verdicts() if v["plate_id"] == plate_id] == ["plate_at_pass"]
    assert shift.runner.tick() == "hold"


@pytest.mark.safety
def test_a_broken_interactions_step_holds_the_plate():
    shift = Shift(interactions=broken_step)
    plate_id = shift.plate_to_pass()

    verdict = shift.verdict(plate_id)
    assert verdict["decision"] == "hold"
    assert any("failed on the latest frame" in reason.get("detail", "") for reason in verdict["reasons"])


@pytest.mark.safety
def test_an_interactions_step_that_falls_behind_is_not_trusted():
    clock = FakeClock(0)
    slot = run_local.InteractionsSlot(state=None, clock=clock, spec=quiet_step)
    assert slot.problem() == "the interactions step hasn't handled a frame yet"

    slot(output=None)
    assert slot.problem() is None

    clock.advance(run_local.INTERACTIONS_STALE_MS + 1)
    assert "hasn't handled a frame in" in slot.problem()


@pytest.mark.safety
def test_the_gate_faults_when_frames_stop():
    shift = Shift(interactions=quiet_step)
    shift.plate_to_pass()
    assert shift.runner.tick() == "pass"

    shift.clock.advance(1500)  # no frames: vision's last_frame_t_ms stops advancing
    assert shift.runner.tick() == "fault"


def test_state_is_marked_live_and_the_vision_api_binds_through_the_shift():
    shift = Shift(interactions=quiet_step)
    shift.frames(6, [BIN, KNIFE, plate_at(0.5)])
    ticket = shift.send_ticket("peanut allergy", 1)

    snapshot = shift.orders.get("/state").json()
    assert snapshot["mode"] == "live"
    assert {zone["zone_id"] for zone in snapshot["tables"]["zone"]} == {"slot-1", "plating-spot", "pass"}

    vision = TestClient(shift.runner.vision_app)
    response = vision.post("/plates/start", json={"ticket_id": ticket["ticket_id"]})
    assert response.status_code == 200, response.json()
    assert response.json() == {"plate_id": "plate-1"}
    assert vision.get("/health").json()["last_frame_t_ms"] == shift.clock.now_ms()


def test_live_mode_reads_the_pi_camera_when_asked(monkeypatch):
    """--picamera gives the live runner vision's PiCameraSource (picamera2 stands in here; it exists only on the Pi)."""
    from argparse import Namespace
    from types import SimpleNamespace

    from kitchen.vision.camera import PiCameraSource

    class Camera:
        camera_controls = {}

        def create_video_configuration(self, main):
            return {"main": main}

        def configure(self, config):
            pass

        def start(self):
            pass

    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=Camera))
    args = Namespace(video=None, webcam=None, picamera=True)

    assert isinstance(run_local.live_source(args, FakeClock(0)), PiCameraSource)


def test_a_zone_change_from_the_web_app_reaches_the_zone_table_and_the_next_frame(tmp_path):
    """PUT /zones on the runner's Vision API: the pass moved under the knife shows in GET /state's zone table (what the
    gate and the screens read) and in the knife's next entity row, and the knife and bin keep their IDs."""
    shift = Shift(quiet_step, zones_path=tmp_path / "zones.json")
    vision = TestClient(shift.runner.vision_app)
    shift.frames(3, [BIN, KNIFE])
    assert shift.overlay_row("tool-knife-1")["zone"] is None

    moved_pass = [[0.2, 0.7], [0.4, 0.7], [0.4, 1.0], [0.2, 1.0]]  # under the knife, centre (0.275, 0.875)
    layout = {"zones": [zone for zone in vision.get("/zones").json()["zones"] if zone["zone_id"] != "pass"]}
    layout["zones"].append({"zone_id": "pass", "kind": "pass", "polygon": moved_pass})
    assert vision.put("/zones", json=layout).status_code == 200

    zone_table = {row["zone_id"]: row for row in shift.orders.get("/state").json()["tables"]["zone"]}
    assert zone_table["pass"]["polygon"] == moved_pass
    shift.frames(1, [BIN, KNIFE])
    assert shift.overlay_row("tool-knife-1")["zone"] == "pass"
    assert shift.overlay_row("bin-1")["zone"] == "slot-1"
    assert (tmp_path / "zones.json").exists()
