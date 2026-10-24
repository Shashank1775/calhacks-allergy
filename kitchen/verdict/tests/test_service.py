"""Tests for the verdict service's triggers, recheck on amendment, fail-closed reads, and FakeVerdictFeed.

Owner: verdict (agents)
Step:  verdict
Reads: kitchen.verdict.service; FakeStateClient, FakeProvenanceGraph and FakeClock (platform); golden scenarios
Emits: nothing
Rules: fail closed and late changes recheck everything, amendments and late edges alike (safety tests)
"""

import pytest

from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import (
    GOLDEN_SCENARIO_PATHS,
    FakeProvenanceGraph,
    FakeStateClient,
    load_scenario,
    read_scenario,
)
from kitchen.shared.interfaces import LayaCheckUnavailable, StateUnavailable
from kitchen.shared.models import EntityState, Zone
from kitchen.verdict.laya_check import FakeLayaCheck
from kitchen.verdict.service import UNBOUND_TICKET_ID, FakeVerdictFeed, VerdictService

SCENARIOS = {path.stem: path for path in GOLDEN_SCENARIO_PATHS}
PASS_ZONE = Zone(zone_id="pass", kind="pass", polygon=[(0.8, 0.0), (1.0, 0.0), (1.0, 1.0)])
PLATING = Zone(zone_id="plating-spot", kind="plating_spot", polygon=[(0.0, 0.0), (0.2, 0.0), (0.2, 1.0)])


class CountingGraph:
    """Wraps a ProvenanceGraph and counts reach() calls, to show every judgment asks the graph again."""

    def __init__(self, graph):
        self.graph = graph
        self.reach_calls = 0

    def version(self):
        return self.graph.version()

    def reach(self, plate_id, as_of_t_ms):
        self.reach_calls += 1
        return self.graph.reach(plate_id, as_of_t_ms)

    def unknown_ancestors(self, plate_id, as_of_t_ms):
        return self.graph.unknown_ancestors(plate_id, as_of_t_ms)

    def subgraph(self, plate_id, as_of_t_ms):
        return self.graph.subgraph(plate_id, as_of_t_ms)


def kitchen(name):
    """A started service over one golden scenario, with a pass zone and a plating spot. Returns its parts."""
    state = FakeStateClient()
    scenario = load_scenario(state, SCENARIOS[name])
    state.set_zones([PASS_ZONE, PLATING])
    clock = FakeClock()
    graph = CountingGraph(FakeProvenanceGraph(scenario))
    service = VerdictService(state, graph, FakeLayaCheck(), clock)
    service.start()
    return state, clock, graph, service, scenario


def move(state, clock, entity_id, zone, t_ms):
    """Set the clock to t_ms and append a state row putting entity_id in zone."""
    clock.set(t_ms)
    state.move_entity(EntityState(entity_id, 1, t_ms, zone, None, {}, False))


def codes(verdict):
    return [reason["code"] for reason in verdict["reasons"]]


def test_triggers_on_entering_the_pass_once():
    state, clock, _, _, _ = kitchen("knife-carries-peanut-to-plate")

    move(state, clock, "plate-t1", "plating-spot", 2000)
    move(state, clock, "plate-t1", "pass", 3000)
    move(state, clock, "plate-t1", "pass", 3200)  # still at the pass: no new verdict
    move(state, clock, "tool-knife-1", "pass", 3300)  # not a plate

    (verdict,) = state.list_verdicts()
    assert verdict["trigger"] == "plate_at_pass"
    assert verdict["decided_t_ms"] == 3000
    assert verdict["decision"] == "hold"


def test_triggers_again_when_the_plate_comes_back():
    state, clock, _, _, _ = kitchen("wash-resets-knife")

    move(state, clock, "plate-t2", "pass", 3000)
    move(state, clock, "plate-t2", "plating-spot", 3500)
    move(state, clock, "plate-t2", "pass", 3800)

    assert [verdict["decided_t_ms"] for verdict in state.list_verdicts()] == [3000, 3800]


@pytest.mark.safety
def test_amendment_full_recheck():
    """Late changes recheck everything: an amendment rejudges the plate from the graph, not from the last verdict."""
    state, clock, graph, _, scenario = kitchen("late-amendment-adds-dairy")
    move(state, clock, "plate-t5", "pass", 3000)
    assert graph.reach_calls == 1

    clock.set(4000)
    state.put_ticket(scenario["amendments"][0])

    first, second = state.list_verdicts()
    assert (first["ticket_revision"], first["decision"]) == (1, "pass")
    assert (second["ticket_revision"], second["trigger"], second["decision"]) == (2, "ticket_amended", "hold")
    assert codes(second)[0] == "forbidden_allergen"
    assert graph.reach_calls == 2


@pytest.mark.safety
def test_graph_failure_holds():
    """Fail closed: GraphUnavailable while judging gives a HOLD with reason error."""
    state, clock, graph, _, _ = kitchen("wash-resets-knife")
    graph.graph.set_unavailable(True)

    move(state, clock, "plate-t2", "pass", 3000)

    (verdict,) = state.list_verdicts()
    assert verdict["decision"] == "hold"
    assert verdict["confidence"] == 0.0
    assert any("GraphUnavailable" in reason.get("detail", "") for reason in verdict["reasons"])


@pytest.mark.safety
def test_dependency_failure_holds_and_records_when_the_database_is_back():
    """Fail closed: with the database down the plate holds, and the HOLD is written once it's back."""
    state, clock, _, service, _ = kitchen("wash-resets-knife")
    clock.set(3000)
    state.set_unavailable(True)

    verdict = service.judge("plate-t2", "plate_at_pass")

    assert verdict["decision"] == "hold"
    assert any("StateUnavailable" in reason.get("detail", "") for reason in verdict["reasons"])
    assert service.pending == [verdict]

    state.set_unavailable(False)
    service.flush()
    assert state.list_verdicts() == [verdict]
    assert service.pending == []


@pytest.mark.safety
def test_input_health_unbound_plate_holds():
    """Fail closed: a plate at the pass that was never bound to a ticket holds with missing_input."""
    state, clock, _, _, _ = kitchen("wash-resets-knife")
    state.register_entity(dict(state.get_entity("plate-t2"), entity_id="plate-stray", label="plate-stray"))

    move(state, clock, "plate-stray", "pass", 3000)

    (verdict,) = state.list_verdicts()
    assert verdict["ticket_id"] == UNBOUND_TICKET_ID
    assert verdict["decision"] == "hold"
    assert codes(verdict)[0] == "missing_input"


@pytest.mark.safety
def test_input_health_missing_ticket_holds():
    """Fail closed: a binding to a ticket the database doesn't have holds with missing_input."""
    state, clock, _, _, _ = kitchen("wash-resets-knife")
    state.register_entity(dict(state.get_entity("plate-t2"), entity_id="plate-t9", label="plate-t9"))
    state.bind_plate("plate-t9", "t9", 100)

    move(state, clock, "plate-t9", "pass", 3000)

    (verdict,) = state.list_verdicts()
    assert verdict["decision"] == "hold"
    assert "missing_input" in codes(verdict)


@pytest.mark.safety
def test_input_health_stale_track_holds():
    """Fail closed: rejudging a plate vision hasn't seen for longer than the staleness window holds with stale_track."""
    state, clock, _, service, _ = kitchen("wash-resets-knife")
    move(state, clock, "plate-t2", "pass", 3000)

    clock.set(3000 + service.settings.stale_track_ms + 1)
    verdict = service.judge("plate-t2", "ticket_amended")

    assert verdict["decision"] == "hold"
    assert "stale_track" in codes(verdict)


def test_a_broken_state_feed_never_raises_into_the_writer():
    """The service's callbacks swallow and log their own errors, so a writer like move_entity never sees them."""
    state, clock, _, service, _ = kitchen("wash-resets-knife")
    service.state = None  # every read now fails inside the callback

    move(state, clock, "plate-t2", "pass", 3000)

    assert state.list_verdicts() == []


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_fake_verdict_feed_replays_expected_verdicts(name):
    state = FakeStateClient()
    scenario = load_scenario(state, SCENARIOS[name])
    feed = FakeVerdictFeed(state, scenario)

    feed.play_until(0)
    early = state.list_verdicts()
    feed.play_until(10**9)
    feed.play_until(10**9)  # replaying again adds nothing

    verdicts = state.list_verdicts()
    assert early == [] or all(verdict["decided_t_ms"] == 0 for verdict in early)
    assert [(v["plate_id"], v["ticket_revision"], v["decision"]) for v in verdicts] == [
        (e["plate_id"], e["ticket_revision"], e["decision"]) for e in scenario["expected"]
    ]


@pytest.mark.safety
def test_an_exception_reading_inputs_still_records_a_hold():
    """Fail closed: even an error read_inputs doesn't expect ends in a recorded HOLD with reason error."""
    state, clock, _, service, _ = kitchen("wash-resets-knife")

    def broken(plate_id, now):
        raise RuntimeError("unexpected")

    service.read_inputs = broken
    clock.set(3000)

    verdict = service.judge("plate-t2", "plate_at_pass")

    assert verdict["decision"] == "hold"
    assert state.list_verdicts() == [verdict]
    assert any("RuntimeError" in reason.get("detail", "") for reason in verdict["reasons"])


class ScriptedLaya:
    """A LayaPlateCheck that gives a fixed answer, or raises LayaCheckUnavailable when `answer` is None."""

    version = "scripted"

    def __init__(self, answer):
        self.answer = answer
        self.subgraphs = []

    def check(self, subgraph, forbidden):
        self.subgraphs.append(subgraph)
        if self.answer is None:
            raise LayaCheckUnavailable("model not loaded (test)")
        return {unit: self.answer.get(unit, 0.0) for unit in forbidden}


@pytest.mark.safety
def test_a_failing_plate_check_holds_without_losing_the_graphs_answer():
    """Fail closed: when Laya can't answer, the plate holds with laya_unavailable and the graph's flags still count."""
    state, clock, _, service, _ = kitchen("knife-carries-peanut-to-plate")
    service.plate_check = ScriptedLaya(None)

    move(state, clock, "plate-t1", "pass", 3000)

    (verdict,) = state.list_verdicts()
    assert verdict["decision"] == "hold"
    assert {"forbidden_allergen", "laya_unavailable"} <= set(codes(verdict))
    assert verdict["chain"]  # the graph's chain survived the Laya failure
    assert "laya" not in verdict["versions"]  # no checkpoint judged this plate


@pytest.mark.safety
def test_the_plate_check_can_hold_a_plate_the_graph_would_pass():
    """Laya is stricter only, and that cuts one way: it can hold a plate the graph clears."""
    state, clock, _, service, _ = kitchen("wash-resets-knife")
    laya = ScriptedLaya({"peanut": 0.5})
    service.plate_check = laya

    move(state, clock, "plate-t2", "pass", 3000)

    (verdict,) = state.list_verdicts()
    assert verdict["decision"] == "hold"
    assert codes(verdict) == ["laya_flagged"]
    assert laya.subgraphs[0]["plate_id"] == "plate-t2"
    assert laya.subgraphs[0]["as_of_t_ms"] == 3000


def test_fake_laya_check_is_recorded_on_every_verdict():
    state, clock, _, _, _ = kitchen("wash-resets-knife")
    move(state, clock, "plate-t2", "pass", 3000)

    (verdict,) = state.list_verdicts()
    assert verdict["decision"] == "pass"
    assert verdict["versions"]["laya"] == "fake"


@pytest.mark.safety
def test_a_malformed_ticket_still_records_a_hold():
    """Fail closed: a ticket missing its forbidden list is a recorded HOLD with reason error, not silence."""
    state, clock, _, _, _ = kitchen("wash-resets-knife")
    real_get_ticket = state.get_ticket

    def ticket_without_forbidden(ticket_id):
        ticket = real_get_ticket(ticket_id)
        del ticket["forbidden"]
        return ticket

    state.get_ticket = ticket_without_forbidden

    move(state, clock, "plate-t2", "pass", 3000)

    (verdict,) = state.list_verdicts()
    assert verdict["decision"] == "hold"
    assert "error" in codes(verdict)


@pytest.mark.safety
def test_an_amendment_is_retried_when_its_plates_cant_be_listed():
    """Late changes recheck everything: if listing the amended ticket's plates fails, the recheck waits and happens."""
    state, clock, _, service, scenario = kitchen("late-amendment-adds-dairy")
    move(state, clock, "plate-t5", "pass", 3000)
    real_list_verdicts = state.list_verdicts

    def down(ticket_id=None):
        raise RuntimeError("timeout (test)")

    state.list_verdicts = down
    clock.set(4000)
    state.put_ticket(scenario["amendments"][0])
    assert service.amended == {"t5"}

    state.list_verdicts = real_list_verdicts
    service.retry()

    assert [(v["ticket_revision"], v["decision"]) for v in state.list_verdicts()] == [(1, "pass"), (2, "hold")]
    assert service.amended == set()


@pytest.mark.safety
def test_a_verdict_that_cant_be_recorded_doesnt_block_later_holds():
    """Fail closed: a verdict the database rejects for good is dropped, so the HOLDs after it still land."""
    state, clock, _, service, _ = kitchen("wash-resets-knife")
    real_record = state.record_verdict
    rejected = []

    def reject_first(verdict):
        if not rejected:
            rejected.append(verdict["verdict_id"])
            raise ValueError("rejected for good (test)")
        real_record(verdict)

    state.record_verdict = reject_first
    clock.set(3000)
    service.judge("plate-t2", "plate_at_pass")
    clock.set(3100)
    later = service.judge("plate-t2", "ticket_amended")

    assert rejected and service.pending == []
    assert state.list_verdicts() == [later]


# ---------- late edges (contracts/apis.md: "An edge landed late") ----------


class EdgeAwareGraph:
    """A stand-in graph that reads edges from the state: a plate carries peanut once a recorded edge into it has
    started by `as_of`. Enough to show a recheck sees an edge the first verdict couldn't."""

    def __init__(self, state):
        self.state = state

    def version(self):
        return "edge-aware-stub"

    def reach(self, plate_id, as_of_t_ms):
        edges = [e for e in self.state.list_interactions(until_t_ms=as_of_t_ms) if plate_id in (e["a_id"], e["b_id"])]
        if not edges:
            return []
        edge = edges[-1]
        source = edge["a_id"] if edge["b_id"] == plate_id else edge["b_id"]
        step = {
            "step": 1,
            "event_id": edge["event_id"],
            "from_id": source,
            "to_id": plate_id,
            "t_ms": edge["t_start_ms"],
            "action": edge["action"],
            "allergen_unit_id": "peanut",
            "step_confidence": edge["probability"],
        }
        return [{"allergen_unit_id": "peanut", "probability": 1.0, "chain": [step]}]

    def unknown_ancestors(self, plate_id, as_of_t_ms):
        return []

    def subgraph(self, plate_id, as_of_t_ms):
        return {"plate_id": plate_id, "as_of_t_ms": as_of_t_ms, "nodes": [], "edges": []}


class QuietLaya:
    """A plate check that finds nothing, so these tests see only what the graph says."""

    version = "quiet-stub"

    def check(self, subgraph, forbidden):
        return {unit: 0.0 for unit in forbidden}


def late_edge_kitchen():
    """The knife scenario's world with no edges yet, judged by EdgeAwareGraph. Returns state, clock and service."""
    state = FakeStateClient()
    load_scenario(state, dict(SCENARIOS_LOADED["knife-carries-peanut-to-plate"], events=[]))
    state.set_zones([PASS_ZONE, PLATING])
    clock = FakeClock()
    service = VerdictService(state, EdgeAwareGraph(state), QuietLaya(), clock)
    service.start()
    return state, clock, service


def edge(event_id, a_id, b_id, t_start_ms):
    """A contact edge from the knife scenario, re-timed and re-pointed."""
    record = {**SCENARIOS_LOADED["knife-carries-peanut-to-plate"]["events"][1]}
    return {
        **record,
        "event_id": event_id,
        "a_id": a_id,
        "b_id": b_id,
        "t_start_ms": t_start_ms,
        "t_end_ms": t_start_ms + 400,
    }


SCENARIOS_LOADED = {name: read_scenario(path) for name, path in SCENARIOS.items()}


@pytest.mark.safety
def test_an_edge_written_after_a_pass_that_started_before_it_rechecks_the_plate():
    """#183: the knife touched the plate before it reached the pass, but the edge was written after the PASS. The
    plate is judged again from its full history and holds."""
    state, clock, _ = late_edge_kitchen()
    move(state, clock, "plate-t1", "pass", 3000)
    clock.set(3500)
    state.record_interaction(edge("e-late", "tool-knife-1", "plate-t1", 2000))

    first, second = state.list_verdicts()
    assert (first["trigger"], first["decision"]) == ("plate_at_pass", "pass")
    assert (second["trigger"], second["decision"], second["decided_t_ms"]) == ("late_edge", "hold", 3500)
    assert "forbidden_allergen" in codes(second)


@pytest.mark.safety
def test_an_edge_touching_a_plate_at_the_pass_after_its_verdict_rechecks_it():
    """A dirty hand touches a plate that already passed while it waits at the pass: it holds."""
    state, clock, _ = late_edge_kitchen()
    move(state, clock, "plate-t1", "pass", 3000)
    clock.set(4200)
    state.record_interaction(edge("e-after", "hand-p1-left", "plate-t1", 3800))

    assert [(v["trigger"], v["decision"]) for v in state.list_verdicts()] == [
        ("plate_at_pass", "pass"),
        ("late_edge", "hold"),
    ]


def test_an_edge_after_the_verdict_that_doesnt_touch_the_plate_changes_nothing():
    state, clock, _ = late_edge_kitchen()
    move(state, clock, "plate-t1", "pass", 3000)
    clock.set(4200)
    state.record_interaction(edge("e-elsewhere", "tool-knife-1", "bin-1", 3800))

    assert [v["trigger"] for v in state.list_verdicts()] == ["plate_at_pass"]


def test_an_edge_before_any_verdict_judges_nothing():
    """A plate is first judged when it reaches the pass; an edge alone never judges an unjudged plate."""
    state, clock, _ = late_edge_kitchen()
    clock.set(2500)
    state.record_interaction(edge("e-early", "tool-knife-1", "plate-t1", 2000))

    assert state.list_verdicts() == []


@pytest.mark.safety
def test_a_recheck_the_database_refused_is_retried_and_lands():
    """Late changes recheck everything: a HOLD from a late edge that couldn't be written waits in pending, and isn't
    counted as said, so it lands once the database is back."""
    state, clock, service = late_edge_kitchen()
    move(state, clock, "plate-t1", "pass", 3000)
    record_verdict = state.record_verdict

    def unavailable(verdict):
        raise StateUnavailable("database down (test)")

    state.record_verdict = unavailable
    clock.set(3500)
    state.record_interaction(edge("e-late", "tool-knife-1", "plate-t1", 2000))
    assert len(service.pending) == 1

    state.record_verdict = record_verdict
    service.retry()
    assert [(v["trigger"], v["decision"]) for v in state.list_verdicts()] == [
        ("plate_at_pass", "pass"),
        ("late_edge", "hold"),
    ]
    assert service.pending == []


@pytest.mark.safety
def test_a_recheck_in_the_same_millisecond_is_not_dropped_as_a_duplicate():
    """The recheck's verdict_id differs from the verdict it replaces, so the database keeps both."""
    state, clock, _ = late_edge_kitchen()
    move(state, clock, "plate-t1", "pass", 3000)
    state.record_interaction(edge("e-late", "tool-knife-1", "plate-t1", 2000))

    first, second = state.list_verdicts()
    assert first["verdict_id"] != second["verdict_id"]
    assert (first["decided_t_ms"], second["decided_t_ms"], second["decision"]) == (3000, 3000, "hold")


@pytest.mark.safety
def test_rechecks_for_two_edges_in_the_same_millisecond_all_land():
    """Each edge triggers its own recheck; the one that sees the plate contact must not be lost as a duplicate of an
    earlier one in the same millisecond, or the plate would keep its PASS."""
    state, clock, _ = late_edge_kitchen()
    move(state, clock, "plate-t1", "pass", 3000)
    clock.set(3500)
    state.record_interaction(edge("e-scoop", "tool-knife-1", "bin-1", 1000))
    state.record_interaction(edge("e-touch", "tool-knife-1", "plate-t1", 2000))

    # The scoop-only recheck still passes, which the plate's PASS already says, so only the HOLD is written.
    verdicts = state.list_verdicts()
    assert [(v["trigger"], v["decision"]) for v in verdicts] == [("plate_at_pass", "pass"), ("late_edge", "hold")]
    assert len({v["verdict_id"] for v in verdicts}) == 2


# From the review of #186 (the runner's live-mode session): rechecks must stop once nothing new can reach the plate.


class LateEdgeGraph(CountingGraph):
    """FakeProvenanceGraph's answers, plus peanut at the plate once any edge named late-* is in the database."""

    def __init__(self, graph, state):
        super().__init__(graph)
        self.state = state

    def reach(self, plate_id, as_of_t_ms):
        reach = super().reach(plate_id, as_of_t_ms)
        if any(event["event_id"].startswith("late-") for event in self.state.list_interactions()):
            reach = reach + [{"allergen_unit_id": "peanut", "probability": 1.0, "chain": []}]
        return reach


def clean_plate_kitchen():
    """clean-plate-from-stack (plate-t6 passes for t6, which forbids peanut), over a graph that learns of late edges."""
    state = FakeStateClient()
    scenario = load_scenario(state, SCENARIOS["clean-plate-from-stack"])
    state.set_zones([PASS_ZONE, PLATING])
    clock = FakeClock()
    graph = LateEdgeGraph(FakeProvenanceGraph(scenario), state)
    VerdictService(state, graph, FakeLayaCheck(), clock).start()
    return state, clock, graph


def short_edge(event_id, a_id, b_id, t_start_ms, action="transfer.contact"):
    """A 100 ms edge."""
    return {
        "event_id": event_id,
        "t_start_ms": t_start_ms,
        "t_end_ms": t_start_ms + 100,
        "a_id": a_id,
        "b_id": b_id,
        "action": action,
        "probability": 0.9,
        "direction": "both",
        "classifier": {"name": "zone_rules", "version": "test"},
    }


def test_edges_that_cant_reach_a_judged_plate_dont_rejudge_it():
    """An edge after the verdict between other entities, or an unimportant one, can't change the plate's answer."""
    state, clock, graph = clean_plate_kitchen()
    move(state, clock, "plate-t6", "pass", 3000)
    calls = graph.reach_calls

    clock.set(4000)
    state.record_interaction(short_edge("late-after", "tool-knife-9", "bin-1", 3500, action="transfer.scoop"))
    state.record_interaction(short_edge("late-nothing", "hand-p1-left", "plate-t6", 2900, action="unimportant"))

    assert len(state.list_verdicts()) == 1
    assert graph.reach_calls == calls


@pytest.mark.safety
def test_a_recheck_that_changes_nothing_writes_nothing():
    """A plate that already holds for the same reasons gets no new row per touch, so its hold is spoken once."""
    state, clock, graph = clean_plate_kitchen()
    move(state, clock, "plate-t6", "pass", 3000)
    clock.set(3600)
    state.record_interaction(short_edge("late-touch", "hand-p1-left", "plate-t6", 2900))
    calls = graph.reach_calls

    clock.set(3900)
    state.record_interaction(short_edge("late-again", "hand-p1-left", "plate-t6", 3800))

    assert graph.reach_calls == calls + 1  # judged again from full history...
    assert [verdict["decision"] for verdict in state.list_verdicts()] == ["pass", "hold"]  # ...with nothing new to say


def test_edges_after_the_plates_last_touch_stop_rejudging_it():
    """Rechecks don't widen the window: once the plate's last touch is in, other edges in the kitchen leave it alone."""
    state, clock, graph = clean_plate_kitchen()
    move(state, clock, "plate-t6", "pass", 3000)
    clock.set(3600)
    state.record_interaction(short_edge("late-touch", "hand-p1-left", "plate-t6", 2900))  # ends at 3000
    calls = graph.reach_calls

    for n, written in enumerate(range(3900, 9000, 300)):
        clock.set(written)
        state.record_interaction(short_edge(f"late-busy-{n}", "hand-p2-left", "bin-1", written - 500))

    assert graph.reach_calls == calls


@pytest.mark.safety
def test_a_restarted_service_still_rechecks_a_plate_judged_before_it():
    """The reach cutoffs come back from the verdicts in the database, so a late edge after a restart still rechecks."""
    state, clock, first = late_edge_kitchen()
    move(state, clock, "plate-t1", "pass", 3000)
    first.stop()
    restarted = VerdictService(state, EdgeAwareGraph(state), QuietLaya(), clock)
    restarted.start()

    clock.set(3500)
    state.record_interaction(edge("e-late", "tool-knife-1", "bin-1", 1000))
    state.record_interaction(edge("e-touch", "tool-knife-1", "plate-t1", 2000))

    assert [v["decision"] for v in state.list_verdicts()][-1] == "hold"
