"""The end-to-end harness: one shift of the whole system on fakes, driven only through published entry points.

Owner: platform (agents)
Step:  end-to-end harness
Reads: contracts/scenarios/*.json; each step's public entry point: the Orders HTTP API (kitchen.orders.api),
       VerdictService (kitchen.verdict.service), GateService (kitchen.gate.service), and the published fakes
       (FakeStateClient, FakeProvenanceGraph, FakeClock, FakeLayaParser, FakeTranscriber, FakeLayaCheck, FakeGate,
       FakeSpeaker)
Emits: the `kitchen` fixture, a factory for Kitchen; Kitchen itself, which tools/run_local.py serves on a live clock
Rules: the harness never reaches into a team's internals and never computes a verdict itself: it plays the scenario
       and reads what the system wrote (verdict rows, gate states, spoken lines)

    typed note ──▶ Orders API (FakeLayaParser) ──▶ answers ──▶ ticket ──▶ StateClient
    scenario ──▶ bins, plate bindings, interaction events ──▶ StateClient
    plate reaches the pass ──▶ VerdictService (graph + Laya's plate check) ──▶ verdict ──▶ GateService ──▶ gate, voice
"""

import os
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from kitchen.gate.driver import FakeGate
from kitchen.gate.service import GateService
from kitchen.gate.voice import FakeSpeaker
from kitchen.orders.api import create_app
from kitchen.orders.laya_parser import FakeLayaParser
from kitchen.orders.tickets import TicketDesk
from kitchen.orders.transcribe import FakeTranscriber
from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import FakeProvenanceGraph, FakeStateClient, load_scenario, read_scenario, scenario_timeline
from kitchen.shared.models import EntityState, Zone
from kitchen.verdict.laya_check import FakeLayaCheck
from kitchen.verdict.service import VerdictService

PASS_ZONE = Zone(zone_id="pass", kind="pass", polygon=[(0.8, 0.0), (1.0, 0.0), (1.0, 1.0), (0.8, 1.0)])


@dataclass
class Kitchen:
    """One shift: the database, the Orders API, the verdict service and the gate loop, on one FakeClock.

    The camera is simulated by calling plate_reaches_pass(); vision's health is "a frame every tick".
    """

    state: object
    graph: object
    scenario: dict
    parser: object = field(default_factory=FakeLayaParser)  # the LayaParser behind the Orders API
    plate_check: object = field(default_factory=FakeLayaCheck)  # Laya's final plate check, in the verdict service
    clock: FakeClock = field(default_factory=FakeClock)
    gate: FakeGate = field(default_factory=lambda: FakeGate(quiet=True))
    speaker: FakeSpeaker = field(default_factory=lambda: FakeSpeaker(quiet=True))
    gate_log: list = field(default_factory=list)  # (t_ms, gate state) after every tick
    cors_origins: list = field(default_factory=list)  # browser origins the Orders API allows (tools/run_local.py)

    def __post_init__(self):
        self.state.set_zones([PASS_ZONE])
        self.verdict_service = VerdictService(self.state, self.graph, self.plate_check, self.clock)
        self.verdict_service.start()
        self.gate_loop = GateService(self.state, self.gate, self.speaker, self.clock, read_health=self.clock.now_ms)
        desk = TicketDesk(self.parser, self.state, self.clock)
        self.app = create_app(desk, FakeTranscriber(), cors_origins=self.cors_origins)  # tools/run_local.py serves it
        self.orders = TestClient(self.app)

    def at(self, t_ms: int) -> None:
        """Move the shift's clock to t_ms."""
        self.clock.set(t_ms)

    def plate_reaches_pass(self, plate_id: str) -> None:
        """What vision and the entity store write when a plate enters the pass zone."""
        self.state.move_entity(EntityState(plate_id, 1, self.clock.now_ms(), PASS_ZONE.zone_id, None, {}, False))

    def tick(self) -> str:
        """One gate loop tick; the state shown is logged with the time."""
        shown = self.gate_loop.tick()
        self.gate_log.append((self.clock.now_ms(), shown))
        return shown

    def play_scenario(self) -> None:
        """Play the scenario's amendments and plates reaching the pass in time order, ticking the gate after each."""
        for item in scenario_timeline(self.scenario):
            self.at(item.t_ms)
            if item.kind == "amendment":
                self.state.put_ticket(item.record)
            elif item.kind == "plate_at_pass":
                self.plate_reaches_pass(item.record["plate_id"])
            self.tick()

    def gate_at(self, t_ms: int) -> str:
        """The last gate state shown at or before t_ms."""
        return [shown for t, shown in self.gate_log if t <= t_ms][-1]

    # The Orders API, as the tablet calls it

    def send_note(self, note: str, number: int, answers: dict) -> dict:
        """Parse a typed note, answer its questions by target field ({"severity": ..., "trace_sensitive": ...}), send."""
        draft = self.orders.post("/parse", json={"note": note, "source": "typed"}).json()
        self.answer_all(draft, answers)
        response = self.orders.post(f"/drafts/{draft['draft_id']}/send", json={"number": number})
        assert response.status_code == 200, response.json()
        return response.json()

    def amend(self, ticket_id: str, change: str, answers: dict) -> dict:
        """Amend a ticket through the Orders API, answer the new draft's questions, and send revision + 1."""
        draft = self.orders.post(f"/tickets/{ticket_id}/amend", json={"note": change}).json()
        self.answer_all(draft, answers)
        response = self.orders.post(f"/drafts/{draft['draft_id']}/send", json={})
        assert response.status_code == 200, response.json()
        return response.json()

    def answer_all(self, draft: dict, answers: dict) -> dict:
        """Answer every question on a draft from `answers`, keyed by the field each question targets."""
        for question in draft["questions"]:
            value = answers[question["target"]["field"]]
            draft = self.orders.post(
                f"/drafts/{draft['draft_id']}/answers", json={"question_id": question["question_id"], "value": value}
            ).json()
        assert draft["sendable"], draft
        return draft


def make_state(config):
    """The StateClient for this run: FakeStateClient, or with `--state real` the partner's SpacetimeStateClient on
    the local test database (SPACETIMEDB_TEST_DB, default allergen-yhz81-test), emptied first.

    A real run with no database errors rather than skips, so a run that never touched the partner's code can't look
    green. Publish the test database as database/tests/conftest.py says.
    """
    if config.getoption("--state", default="fake") == "real":
        from database.state_client import SpacetimeStateClient

        client = SpacetimeStateClient(database=os.environ.get("SPACETIMEDB_TEST_DB", "allergen-yhz81-test"))
        client.start_shift()
        return client
    return FakeStateClient()


def make_graph(config, scenario, state):
    """The ProvenanceGraph for this run: FakeProvenanceGraph, or with `--graph real` the partner's, reading `state`."""
    if config.getoption("--graph", default="fake") == "real":
        from kitchen.graph import ProvenanceGraph

        return ProvenanceGraph(state)
    return FakeProvenanceGraph(scenario)


@pytest.fixture
def kitchen(request):
    """Factory: kitchen(scenario, with_tickets=True, parser=None) builds a Kitchen with the scenario's world loaded.

    with_tickets=False leaves the scenario's tickets and amendments out, so a test creates them through the Orders
    API instead. The graph still answers from the scenario's ground truth. `parser` replaces FakeLayaParser behind
    the Orders API, for a test that needs Laya to misread something.
    """
    states = []

    def build(scenario, with_tickets=True, parser=None):
        scenario = read_scenario(scenario)
        if not with_tickets:
            scenario = dict(scenario, tickets=[], amendments=[])
        state = make_state(request.config)
        states.append(state)
        load_scenario(state, scenario)
        graph = make_graph(request.config, scenario, state)
        return Kitchen(state=state, graph=graph, scenario=scenario, parser=parser or FakeLayaParser())

    yield build
    # A real client keeps polling after the test; closed, its subscribers can't react to the next test's writes.
    for state in states:
        if hasattr(state, "close"):
            state.close()
