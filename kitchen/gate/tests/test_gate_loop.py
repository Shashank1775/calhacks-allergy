"""Tests for the gate loop on StateClient: scenarios, stale revisions, feed loss, crowding and voice.

Owner: outputs (agents)
Step:  gate and screens
Reads: kitchen.gate.service; FakeStateClient and FakeClock (platform); FakeVerdictFeed (verdict); golden scenarios
Emits: nothing
Rules: fail closed and late changes recheck everything (safety tests)
"""

import math

import pytest

from kitchen.gate.driver import FakeGate
from kitchen.gate.service import GateService, replay_scenario
from kitchen.gate.voice import FakeSpeaker
from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS, FakeStateClient, load_scenario
from kitchen.shared.models import EntityState, Zone
from kitchen.verdict.service import FakeVerdictFeed

SCENARIOS = {path.stem: path for path in GOLDEN_SCENARIO_PATHS}
PASS_ZONE = Zone(zone_id="pass", kind="pass", polygon=[(0.8, 0.0), (1.0, 0.0), (1.0, 1.0)])
TICK_MS = 250


class World:
    """One golden scenario in FakeStateClient with a pass zone, its verdict feed, and a gate loop on fakes."""

    def __init__(self, name):
        self.state = FakeStateClient()
        self.scenario = load_scenario(self.state, SCENARIOS[name])
        self.state.set_zones([PASS_ZONE])
        self.feed = FakeVerdictFeed(self.state, self.scenario)
        self.clock = FakeClock()
        self.frame_t_ms = 0
        self.gate = FakeGate(quiet=True)
        self.speaker = FakeSpeaker(quiet=True)
        self.service = GateService(self.state, self.gate, self.speaker, self.clock, read_health=self.health)

    def health(self):
        """Vision's last_frame_t_ms: advances whenever the test says a frame arrived."""
        return self.frame_t_ms

    def at(self, t_ms, frame=True):
        """Move time to t_ms (a new camera frame unless frame=False), play due verdicts, and tick the gate."""
        self.clock.set(t_ms)
        if frame:
            self.frame_t_ms = t_ms
        self.feed.play_until(t_ms)
        return self.service.tick()

    def plate_to_pass(self, plate_id, t_ms):
        """Put a plate in the pass zone at t_ms."""
        self.state.move_entity(EntityState(plate_id, 1, t_ms, PASS_ZONE.zone_id, None, {}, False))

    def knife_seen(self, t_ms):
        """Vision sees the knife at t_ms and nothing else, as when a plate has been lifted out of view."""
        self.state.move_entity(EntityState("tool-knife-1", 1, t_ms, None, None, {}, False))

    def unbound_plate_to_pass(self, plate_id, t_ms):
        """Register another plate, bound to no ticket, and put it in the pass zone at t_ms."""
        self.state.register_entity(dict(self.state.get_entity("plate-t2"), entity_id=plate_id, label=plate_id))
        self.plate_to_pass(plate_id, t_ms)


@pytest.mark.safety
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_gate_service_scenarios(name):
    """Every golden scenario drives FakeGate to each expected decision at the moment that verdict lands."""
    gate, _ = replay_scenario(name, tick_ms=TICK_MS, quiet=True)
    world = World(name)

    for verdict in world.feed.verdicts():
        shown = gate.calls[math.ceil(verdict["decided_t_ms"] / TICK_MS)][0]
        assert shown == verdict["decision"], verdict["verdict_id"]


@pytest.mark.safety
def test_gate_stale_revision():
    """Late changes recheck everything: a PASS for revision 1 stops opening the gate once revision 2 exists."""
    world = World("wash-resets-knife")
    world.plate_to_pass("plate-t2", 3000)
    assert world.at(3000) == "pass"

    amended = dict(world.state.get_ticket("t2"), revision=2, created_t_ms=3100, forbidden=["peanut", "milk"])
    world.state.put_ticket(amended)

    assert world.at(3250) == "hold"


@pytest.mark.safety
def test_gate_service_feed_loss_database():
    """Fail closed: the moment the database stops answering, the gate faults, even over a current PASS."""
    world = World("wash-resets-knife")
    world.plate_to_pass("plate-t2", 3000)
    assert world.at(3000) == "pass"

    world.state.set_unavailable(True)

    assert world.at(3250) == "fault"


@pytest.mark.safety
def test_gate_service_feed_loss_vision():
    """Fail closed: when vision's frames stop for longer than the limit, the gate faults."""
    world = World("wash-resets-knife")
    world.plate_to_pass("plate-t2", 3000)
    assert world.at(3000) == "pass"

    limit = world.service.settings.health_limit_ms
    assert world.at(3000 + limit, frame=False) == "pass"
    assert world.at(3000 + limit + 1, frame=False) == "fault"
    assert world.at(3000 + limit + 250) == "pass"  # frames are back


@pytest.mark.safety
def test_gate_service_feed_loss_unreadable_health():
    """Fail closed: if /health can't be read at all, the gate faults; it never assumes vision is fine."""
    world = World("wash-resets-knife")
    world.plate_to_pass("plate-t2", 3000)

    def broken():
        raise ConnectionError("vision unreachable")

    world.service.read_health = broken

    assert world.at(3000) == "fault"


@pytest.mark.safety
def test_two_plates_at_the_pass_hold():
    """Fail closed: with two plates at the pass the arm can't tell them apart, so it holds even if both passed."""
    world = World("wash-resets-knife")
    world.plate_to_pass("plate-t2", 3000)
    assert world.at(3000) == "pass"

    world.unbound_plate_to_pass("plate-x", 3100)

    assert world.at(3250) == "hold"


@pytest.mark.safety
def test_two_plates_seen_within_the_track_lost_limit_hold():
    """Fail closed: a plate last seen plate_lost_ms before the other is still tracked by vision, so both hold."""
    world = World("wash-resets-knife")
    limit = world.service.settings.plate_lost_ms
    world.unbound_plate_to_pass("plate-x", 3000 - limit)
    world.plate_to_pass("plate-t2", 3000)

    assert world.at(3000) == "hold"


def test_a_plate_lifted_out_of_view_leaves_the_pass():
    """A plate's last row says pass forever once it's carried out of view; vision's newer sightings age it out."""
    world = World("knife-carries-peanut-to-plate")
    world.plate_to_pass("plate-t1", 3000)
    assert world.at(3000) == "hold"

    limit = world.service.settings.plate_lost_ms
    world.knife_seen(3000 + limit)
    assert world.at(3000 + limit) == "hold"  # unseen for the limit: vision may still have its track

    world.knife_seen(3000 + limit + 1)
    assert world.at(3000 + limit + 1) == "idle"


def test_the_next_plate_passes_once_the_last_one_has_left():
    """The bug this guards: one plate served by lifting it out of view held every later plate as two at the pass."""
    world = World("wash-resets-knife")
    world.unbound_plate_to_pass("plate-x", 1500)
    assert world.at(1500) == "idle"  # no ticket, no verdict: closed

    world.plate_to_pass("plate-t2", 2400)
    assert world.at(2400) == "hold"  # plate-x seen 900 ms ago: still two at the pass

    world.plate_to_pass("plate-t2", 3000)
    assert world.at(3000) == "pass"  # plate-x unseen for 1500 ms: its track has ended


def test_gate_settings_are_logged_at_startup(caplog):
    with caplog.at_level("INFO", logger="kitchen.gate.service"):
        World("wash-resets-knife")

    assert "plate_lost_ms=1000" in caplog.text


def test_gate_service_voice_says_each_hold_once():
    world = World("knife-carries-peanut-to-plate")
    world.plate_to_pass("plate-t1", 3000)

    world.at(3000)
    world.at(3250)
    world.at(3500)

    assert world.speaker.lines == ["Hold one, peanut"]


def test_a_broken_speaker_never_changes_the_gate():
    world = World("knife-carries-peanut-to-plate")
    world.plate_to_pass("plate-t1", 3000)

    def broken(text):
        raise OSError("no audio device")

    world.speaker.say = broken

    assert world.at(3000) == "hold"
    assert world.gate.state == "hold"


def test_no_plate_at_the_pass_is_idle():
    assert World("wash-resets-knife").at(0) == "idle"
