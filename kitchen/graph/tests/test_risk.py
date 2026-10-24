"""LiveRisk: the allergens the camera overlays show, written as entity_state rows, and the ways it must not disturb
anything else.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: kitchen.graph (LiveRisk, ProvenanceGraph); FakeStateClient, FakeClock and the golden scenarios (kitchen.shared);
       VerdictService (kitchen.verdict), to show a risk row doesn't judge a plate again
Emits: nothing
Rules: known-clean source (unknown history shows unknown until a wash); fail closed (an error writes nothing and never
       shows clean); the verdict is untouched (no row changes what the graph answers or moves an entity)
"""

import time

import pytest

from kitchen.graph import LiveRisk, ProvenanceGraph
from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS, FakeStateClient, load_scenario
from kitchen.shared.fakes.scenarios import scenario_entity
from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import EntityState, Zone
from kitchen.verdict.laya_check import FakeLayaCheck
from kitchen.verdict.service import VerdictService

SATAY = {"probabilities": {"peanut": 0.98, "soy": 0.9}, "unknown": False, "source": "manual"}
RICE = {"probabilities": {}, "unknown": False, "source": "manual"}
LATER = 10_000
BOX = [0.1, 0.2, 0.3, 0.4]


def edge(event_id, t_ms, a_id, b_id, action="transfer.contact", probability=0.9, direction="both"):
    """An interaction edge lasting 400 ms."""
    return {
        "event_id": event_id,
        "t_start_ms": t_ms,
        "t_end_ms": t_ms + 400,
        "a_id": a_id,
        "b_id": b_id,
        "action": action,
        "probability": probability,
        "direction": direction,
        "classifier": {"name": "sim_ground_truth", "version": "0"},
    }


def scoop(event_id="e-scoop", t_ms=1000):
    """The knife scoops satay out of bin-1."""
    return edge(event_id, t_ms, "tool-knife-1", "bin-1", "transfer.scoop", 0.95, "b_to_a")


def wash(event_id="e-wash", t_ms=2000, entity_id="tool-knife-1"):
    """A wash of one entity: a self-loop."""
    return edge(event_id, t_ms, entity_id, entity_id, "clean.wash", 0.98, "a_to_b")


class Shift:
    """A database with a satay bin, a rice bin, a knife, a hand and a plate from shift start, LiveRisk on it, and every
    entity_state row written (as the overlays see them) in `rows`."""

    def __init__(self, events=(), unknown=()):
        self.state = FakeStateClient()
        self.clock = FakeClock(LATER)
        for entity_id in ("bin-1", "bin-3", "tool-knife-1", "hand-p1-left", "plate-t1"):
            self.state.register_entity(scenario_entity(entity_id, "shift_start", 0))
        for entity_id in unknown:
            self.state.register_entity(scenario_entity(entity_id, "unknown", 1500))
        self.state.put_bin_profile("bin-1", SATAY)
        self.state.put_bin_profile("bin-3", RICE)
        self.rows = []
        self.state.subscribe("entity_state", lambda change: self.rows.append(change.row))
        for event in events:
            self.state.record_interaction(event)
        self.risk = LiveRisk(self.state, self.clock)

    def shown(self, entity_id) -> dict:
        """The allergens the database joins to the entity: what the next row copies forward."""
        return self.state.get_entity(entity_id)["allergen_profile"]["probabilities"]

    def rows_of(self, entity_id) -> list:
        return [row for row in self.rows if row.entity_id == entity_id]


def wait_until(condition, timeout_s=3.0):
    """True once condition() holds, polling for up to timeout_s."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_knife_that_scooped_satay_shows_peanut():
    shift = Shift([scoop()])

    assert shift.risk.update_once() is True

    assert shift.shown("tool-knife-1") == pytest.approx({"peanut": 0.931, "soy": 0.855})
    (row,) = shift.rows_of("tool-knife-1")
    assert row.caused_by == "e-scoop"  # without caused_by the database joins ignore the allergens
    assert row.unknown is False
    assert max(row.probabilities.values()) >= 0.5  # what the overlay colors red


def test_a_wash_lowers_it():
    shift = Shift([scoop()])
    shift.risk.update_once()
    assert shift.shown("tool-knife-1")["peanut"] > 0.9

    shift.state.record_interaction(wash())
    shift.risk.update_once()

    assert shift.shown("tool-knife-1")["peanut"] == pytest.approx(0.019)
    assert shift.rows_of("tool-knife-1")[-1].caused_by == "e-wash"


def test_a_touch_carries_it_on_to_the_plate_and_a_clean_touch_writes_nothing():
    shift = Shift([scoop(), edge("e-touch", 2000, "tool-knife-1", "plate-t1")])
    shift.risk.update_once()

    assert shift.shown("plate-t1") == pytest.approx({"peanut": 0.98 * 0.95 * 0.9, "soy": 0.9 * 0.95 * 0.9}, abs=1e-3)

    rows_before = len(shift.rows)
    shift.state.record_interaction(edge("e-hand", 3000, "hand-p1-left", "bin-3"))  # a clean hand on clean rice
    shift.risk.update_once()
    assert len(shift.rows) == rows_before


def test_nothing_changed_writes_nothing():
    shift = Shift([scoop()])
    shift.risk.update_once()
    rows_after_first = len(shift.rows)

    assert shift.risk.update_once() is True
    assert len(shift.rows) == rows_after_first


def test_versions_count_the_rows_of_each_entity():
    shift = Shift([scoop()])
    shift.risk.update_once()
    shift.state.record_interaction(wash())
    shift.risk.update_once()

    assert [row.version for row in shift.rows_of("tool-knife-1")] == [1, 2]


@pytest.mark.safety
def test_unknown_history_shows_unknown_until_washed():
    """Known-clean source: a mid-shift spoon may carry anything, and says so until a wash resets it."""
    shift = Shift([edge("e-spoon", 2000, "tool-spoon-9", "tool-knife-1")], unknown=["tool-spoon-9"])
    shift.risk.update_once()

    assert shift.state.get_entity("tool-spoon-9")["allergen_profile"]["unknown"] is True
    knife = shift.state.get_entity("tool-knife-1")["allergen_profile"]
    assert knife["probabilities"]["sesame"] == pytest.approx(0.9)  # what the spoon may have left on it
    assert knife["unknown"] is False  # only the unknown-origin entity itself is flagged: the graph reads the flag back

    shift.state.record_interaction(wash("e-rinse", 3000, "tool-spoon-9"))
    shift.risk.update_once()

    spoon = shift.state.get_entity("tool-spoon-9")["allergen_profile"]
    assert spoon["unknown"] is False
    assert spoon["probabilities"]["sesame"] == pytest.approx(0.02)


@pytest.mark.safety
def test_a_tiny_risk_is_never_shown_as_clean():
    """Two rinses leave 0.04% of a spoon's unknown history. Rounded to 3 decimals that is 0, which the overlay would
    color clean, so the smallest number shown is 0.001."""
    washes = [wash("e-rinse-1", 3000, "tool-spoon-9"), wash("e-rinse-2", 4000, "tool-spoon-9")]
    shift = Shift(washes, unknown=["tool-spoon-9"])

    shift.risk.update_once()

    assert shift.shown("tool-spoon-9")["sesame"] == 0.001


def test_a_risk_row_moves_nothing():
    shift = Shift([scoop()])
    shift.state.move_entity(EntityState("tool-knife-1", 1, 1500, "plating-spot", BOX, {}, False))

    shift.risk.update_once()  # the clock reads 10 s, long after the camera last saw the knife

    (row,) = [row for row in shift.rows_of("tool-knife-1") if row.caused_by is not None]
    assert (row.t_ms, row.zone, row.box) == (1500, "plating-spot", BOX)
    knife = shift.state.get_entity("tool-knife-1")
    assert (knife["last_seen_t_ms"], knife["zone"], knife["box"]) == (1500, "plating-spot", BOX)


@pytest.mark.safety
def test_a_plate_at_the_pass_is_not_judged_again_by_a_risk_row():
    """A risk row copies the plate's zone, so VerdictService sees a plate that is already at the pass, not an arrival."""
    state = FakeStateClient()
    scenario = load_scenario(
        state, next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == "knife-carries-peanut-to-plate")
    )
    state.set_zones([Zone(zone_id="pass", kind="pass", polygon=[(0.8, 0.0), (1.0, 0.0), (1.0, 1.0)])])
    clock = FakeClock(3000)
    VerdictService(state, ProvenanceGraph(state), FakeLayaCheck(), clock).start()
    plate_id = scenario["plates_at_pass"][0]["plate_id"]
    state.move_entity(EntityState(plate_id, 1, 3000, "pass", BOX, {}, False))
    assert [v["trigger"] for v in state.list_verdicts()] == ["plate_at_pass"]

    assert LiveRisk(state, clock).update_once() is True

    assert state.get_entity(plate_id)["allergen_profile"]["probabilities"]["peanut"] > 0.5  # the row landed
    plate = state.get_entity(plate_id)
    assert (plate["zone"], plate["last_seen_t_ms"]) == ("pass", 3000)
    state.move_entity(EntityState(plate_id, 2, 3200, "pass", BOX, {}, False))  # the entity store's next row
    assert [v["trigger"] for v in state.list_verdicts()] == ["plate_at_pass"]


@pytest.mark.safety
@pytest.mark.parametrize("path", GOLDEN_SCENARIO_PATHS, ids=lambda path: path.stem)
def test_risk_rows_never_change_what_the_graph_answers(path):
    """The verdict reads the same database the rows go into, so a row the graph read back as a start would change it."""
    state = FakeStateClient()
    scenario = load_scenario(state, path)
    graph = ProvenanceGraph(state)

    def answers():
        return [
            (graph.reach(p["plate_id"], p["t_ms"]), graph.unknown_ancestors(p["plate_id"], p["t_ms"]),
             graph.subgraph(p["plate_id"], p["t_ms"]))
            for p in scenario["plates_at_pass"]
        ]  # fmt: skip

    before = answers()
    LiveRisk(state, FakeClock(LATER)).update_once()
    assert answers() == before


@pytest.mark.safety
def test_bins_get_no_rows():
    """A row would become the bin's start profile in the graph, which would count the same contact twice."""
    events = [scoop(), edge("e-rice", 2000, "tool-knife-1", "bin-3", direction="a_to_b")]
    events.append(edge("e-serve", 3000, "bin-3", "plate-t1", direction="a_to_b"))
    shift = Shift(events)
    graph = ProvenanceGraph(shift.state)
    reach_before = graph.reach("plate-t1", LATER)

    shift.risk.update_once()

    assert shift.rows_of("bin-3") == [] and shift.rows_of("bin-1") == []
    assert shift.shown("bin-3") == {}
    assert graph.reach("plate-t1", LATER) == reach_before


@pytest.mark.safety
def test_a_failed_update_writes_nothing():
    shift = Shift([scoop()])
    shift.state.set_unavailable()
    with pytest.raises(StateUnavailable):
        shift.risk.update_once()
    shift.state.set_unavailable(False)
    assert shift.rows == []


@pytest.mark.safety
def test_a_failed_write_keeps_what_the_screen_showed_and_is_tried_again(monkeypatch):
    """An error never turns into a clean row: the knife keeps showing its peanut until the lower number lands."""
    shift = Shift([scoop()])
    shift.risk.update_once()
    shift.state.record_interaction(wash())
    move_entity = shift.state.move_entity

    def fail_once(state):
        monkeypatch.setattr(shift.state, "move_entity", move_entity)
        raise RuntimeError("write failed")

    monkeypatch.setattr(shift.state, "move_entity", fail_once)
    with pytest.raises(RuntimeError):
        shift.risk.update_once()
    assert shift.shown("tool-knife-1")["peanut"] > 0.9

    shift.risk.update_once()
    assert shift.shown("tool-knife-1")["peanut"] == pytest.approx(0.019)
    assert [row.version for row in shift.rows_of("tool-knife-1")] == [1, 2]  # the failed write used no version


def test_an_entity_the_database_lacks_waits_for_its_registration():
    shift = Shift([wash("e-rinse", 1000, "tool-spoon-9")])

    assert shift.risk.update_once() is False  # the rinsed spoon isn't registered yet
    assert shift.rows == []

    shift.state.register_entity(scenario_entity("tool-spoon-9", "unknown", 500))
    assert shift.risk.update_once() is True
    assert shift.shown("tool-spoon-9")["sesame"] == pytest.approx(0.02)


def test_it_updates_in_the_background_when_an_edge_lands():
    shift = Shift([scoop()])  # already in the database when it starts
    shift.risk.start(hz=100)
    try:
        assert wait_until(lambda: shift.shown("tool-knife-1").get("peanut", 0) > 0.9)

        shift.state.record_interaction(wash())  # lands while it runs
        assert wait_until(lambda: shift.shown("tool-knife-1").get("peanut", 1) < 0.05)
    finally:
        shift.risk.stop()


def test_a_failing_update_is_tried_again_by_the_background_thread():
    shift = Shift([scoop()])
    update_once = shift.risk.update_once
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) <= 2:
            raise StateUnavailable("database unreachable")
        return update_once()

    shift.risk.update_once = flaky
    shift.risk.start(hz=100)
    try:
        assert wait_until(lambda: shift.shown("tool-knife-1").get("peanut", 0) > 0.9)
        assert len(calls) >= 3
    finally:
        shift.risk.stop()


def test_the_update_rate_must_be_positive():
    with pytest.raises(ValueError):
        Shift().risk.start(hz=0)
