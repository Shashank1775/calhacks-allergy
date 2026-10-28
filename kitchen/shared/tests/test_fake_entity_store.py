"""Tests for FakeEntityStore beyond the shared contract suite: starting profiles and the synchronous sync.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.fakes.entity_store, contracts/fixtures/valid/observation_frame
Emits: nothing
Rules: known-clean source (only tools, hands and plates with a clean origin start clean); fail closed (a failed sync
       leaves last_sync_t_ms stale and retries)
"""

import jsonschema
import pytest

from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.fakes import FakeClock, FakeEntityStore, FakeStateClient
from kitchen.shared.fakes.entity_store import starting_profile
from kitchen.shared.interfaces import StateUnavailable


def frame(t_ms, **knife_changes):
    """The two-detection contract frame (a knife and a hand) at `t_ms`, with the knife detection changed."""
    observation = contract_fixture("observation_frame", "two-detections")
    observation["t_ms"] = t_ms
    observation["detections"][0].update(knife_changes)
    return observation


@pytest.mark.safety
@pytest.mark.parametrize(("kind", "unknown"), [("tool", False), ("plate", False), ("hand", False), ("bin", True)])
def test_tools_hands_and_plates_start_clean_and_bins_unknown(kind, unknown):
    """Known-clean source: a tool, hand or plate starts clean whatever its origin, since only an edge gives it risk;
    a bin's contents are its bin profile's business, so the store never calls one clean."""
    assert starting_profile(kind)["unknown"] is unknown


def test_sync_once_registers_then_moves_each_entity():
    clock = FakeClock(start_ms=1000)
    store = FakeEntityStore(clock)
    state = FakeStateClient()
    states = []
    state.subscribe("entity_state", lambda change: states.append(change.row))
    store.ingest(frame(1000))
    store.sync_once(state)

    assert {entity["entity_id"] for entity in state.list_entities()} == {"tool-knife-17", "hand-p1-right"}
    assert [(row.entity_id, row.version, row.zone) for row in states] == [
        ("tool-knife-17", 1, "slot-4"),
        ("hand-p1-right", 1, "slot-4"),
    ]
    assert store.last_sync_t_ms() == 1000


def test_sync_once_sends_states_only_for_entities_that_moved():
    store = FakeEntityStore(FakeClock())
    state = FakeStateClient()
    states = []
    state.subscribe("entity_state", lambda change: states.append(change.row))
    store.ingest(frame(1000))
    store.sync_once(state)
    store.sync_once(state)
    store.ingest(frame(1033, zone="plating-spot"))
    store.sync_once(state)

    knife_states = [(row.version, row.t_ms, row.zone) for row in states if row.entity_id == "tool-knife-17"]
    assert knife_states == [(1, 1000, "slot-4"), (2, 1033, "plating-spot")]
    assert state.get_entity("tool-knife-17")["zone"] == "plating-spot"


@pytest.mark.safety
def test_a_failed_sync_leaves_last_sync_stale_and_retries_next_time():
    """Fail closed: health checks see an outage as a stale last_sync_t_ms, and nothing is lost when it ends."""
    clock = FakeClock(start_ms=1000)
    store = FakeEntityStore(clock)
    state = FakeStateClient()
    store.ingest(frame(1000))
    store.sync_once(state)

    clock.set(2000)
    store.ingest(frame(2000, zone="plating-spot"))
    state.set_unavailable(True)
    with pytest.raises(StateUnavailable):
        store.sync_once(state)
    assert store.last_sync_t_ms() == 1000

    clock.set(3000)
    state.set_unavailable(False)
    store.sync_once(state)
    assert store.last_sync_t_ms() == 3000
    assert state.get_entity("tool-knife-17")["zone"] == "plating-spot"


def test_ingest_checks_frames_against_the_schema():
    bad = frame(1000)
    bad["detections"][0]["allergen"] = "peanut"
    with pytest.raises(jsonschema.ValidationError, match="allergen"):
        FakeEntityStore(FakeClock()).ingest(bad)
