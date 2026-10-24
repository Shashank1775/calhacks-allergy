"""Tests for the real entity store beyond the shared contract suite: allergens carried forward, origin, retries.

Owner: partner (written by an agent at the owner's request)
Step:  Entity database
Reads: kitchen.entities.store, FakeStateClient, contracts/fixtures/valid (observation_frame, allergen_profile)
Emits: nothing
Rules: known-clean source (origin fixed at first sighting, re-acquired tracks are new unknown entities); a zone row
       never overwrites the graph's allergens; time runs forward (older frames refused); fail closed (a failed sync
       leaves last_sync_t_ms stale and is retried)
"""

import logging
import time

import jsonschema
import pytest

from kitchen.entities.store import EntityStore
from kitchen.shared.clock import FakeClock
from kitchen.shared.contract_tests import contract_fixture, wait_until
from kitchen.shared.fakes import FakeStateClient
from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import EntityState


def frame(t_ms: int, *detections: dict) -> dict:
    """An ObservationFrame at `t_ms` holding exactly `detections`."""
    observation = contract_fixture("observation_frame", "two-detections")
    observation.update(frame_id=t_ms // 33, t_ms=t_ms, detections=list(detections))
    return observation


def knife(**changes) -> dict:
    """The contract fixture's knife detection (tool-knife-17, shift_start, slot-4), with `changes` applied."""
    detection = contract_fixture("observation_frame", "two-detections")["detections"][0]
    detection.update(changes)
    return detection


def hand() -> dict:
    """The contract fixture's hand detection (hand-p1-right)."""
    return contract_fixture("observation_frame", "two-detections")["detections"][1]


def state_rows(state: FakeStateClient) -> list[EntityState]:
    """A list that fills with every entity_state row written to `state` from now on."""
    rows = []
    state.subscribe("entity_state", lambda change: rows.append(change.row))
    return rows


@pytest.mark.safety
def test_a_zone_row_carries_the_graphs_allergens_forward():
    """A zone update must never wipe what the graph found: the knife touched peanut, then moved."""
    store = EntityStore(FakeClock())
    state = FakeStateClient()
    rows = state_rows(state)
    store.ingest(frame(1000, knife()))
    store.sync_once(state)

    graph_row = EntityState("tool-knife-17", 1, 1500, "slot-4", None, {"peanut": 0.9}, False, caused_by="evt-1")
    state.move_entity(graph_row)
    store.ingest(frame(2000, knife(zone="plating-spot")))
    store.sync_once(state)

    zone_row = rows[-1]
    assert (zone_row.t_ms, zone_row.zone) == (2000, "plating-spot")
    assert (zone_row.probabilities, zone_row.unknown, zone_row.caused_by) == ({"peanut": 0.9}, False, None)


@pytest.mark.safety
def test_a_bins_zone_rows_carry_its_bin_profile():
    """A bin the graph hasn't touched yet takes its allergens from its bin profile, not from the store's 'unknown'."""
    store = EntityStore(FakeClock())
    state = FakeStateClient()
    rows = state_rows(state)
    state.put_bin_profile("bin-4", contract_fixture("allergen_profile", "bin-satay"))
    store.ingest(frame(1000, knife(entity_id="bin-4", kind="bin", class_name="bowl", track_id=4)))
    store.sync_once(state)

    assert (rows[-1].probabilities, rows[-1].unknown) == ({"peanut": 0.98, "soy": 0.6, "wheat": 0.3}, False)


@pytest.mark.safety
def test_an_unknown_origin_stays_unknown_in_the_store_and_the_database():
    """Known-clean source: a knife first seen with unknown history stays unknown, whatever later frames say."""
    store = EntityStore(FakeClock())
    state = FakeStateClient()
    rows = state_rows(state)
    store.ingest(frame(5000, knife(origin="unknown")))
    store.ingest(frame(6000, knife(origin="shift_start", zone="plating-spot")))
    store.sync_once(state)

    assert store.get("tool-knife-17")["allergen_profile"]["unknown"] is True
    synced = state.get_entity("tool-knife-17")
    assert (synced["origin"], synced["first_seen_t_ms"]) == ("unknown", 5000)
    assert rows[-1].unknown is True


@pytest.mark.safety
def test_a_reacquired_track_is_a_new_unknown_entity():
    """Known-clean source: vision gives a knife that left and came back a new ID, and the old clean one is untouched."""
    store = EntityStore(FakeClock())
    store.ingest(frame(1000, knife(), hand()))
    store.ingest(frame(2000, hand()))
    store.ingest(frame(3000, knife(entity_id="tool-knife-18", track_id=18, origin="unknown"), hand()))

    old = store.get("tool-knife-17")
    assert (old["origin"], old["last_seen_t_ms"], old["allergen_profile"]["unknown"]) == ("shift_start", 1000, False)
    new = store.get("tool-knife-18")
    assert (new["origin"], new["first_seen_t_ms"], new["allergen_profile"]["unknown"]) == ("unknown", 3000, True)


@pytest.mark.safety
def test_a_frame_older_than_the_newest_is_refused():
    """Time runs forward: a late frame can't move an entity back, and a frame at the same t_ms is fine."""
    store = EntityStore(FakeClock())
    store.ingest(frame(2000, knife()))

    with pytest.raises(ValueError, match="older"):
        store.ingest(frame(1000, knife(zone="plating-spot")))
    store.ingest(frame(2000, hand()))

    moved = store.get("tool-knife-17")
    assert (moved["zone"], moved["last_seen_t_ms"]) == ("slot-4", 2000)


@pytest.mark.safety
def test_last_sync_moves_only_when_a_sync_finishes():
    """Fail closed: health checks see an outage as a stale last_sync_t_ms, never as freshness that isn't there."""
    clock = FakeClock(start_ms=1000)
    store = EntityStore(clock)
    state = FakeStateClient()
    store.ingest(frame(1000, knife()))

    state.set_unavailable(True)
    with pytest.raises(StateUnavailable):
        store.sync_once(state)
    assert store.last_sync_t_ms() is None

    clock.set(2000)
    state.set_unavailable(False)
    store.sync_once(state)
    assert store.last_sync_t_ms() == 2000

    clock.set(3000)
    store.ingest(frame(3000, knife(zone="plating-spot")))
    state.fail_next(1)
    with pytest.raises(StateUnavailable):
        store.sync_once(state)
    assert store.last_sync_t_ms() == 2000


@pytest.mark.safety
def test_the_sync_thread_retries_until_the_database_is_back(caplog):
    """Fail closed: an outage is logged and retried every tick, and nothing seen during it is lost."""
    caplog.set_level(logging.WARNING, logger="kitchen.entities.sync")
    store = EntityStore(FakeClock(start_ms=1000))
    state = FakeStateClient()
    state.set_unavailable(True)
    store.ingest(frame(1000, knife()))
    store.start_sync(state, hz=50.0)
    try:
        time.sleep(0.1)  # five ticks at 50 Hz, all failing
        assert store.last_sync_t_ms() is None
        state.set_unavailable(False)
        assert wait_until(lambda: store.last_sync_t_ms() == 1000)
    finally:
        store.stop_sync()

    assert state.get_entity("tool-knife-17")["zone"] == "slot-4"
    assert "unreachable" in caplog.text


def test_a_state_row_is_sent_only_when_an_entity_moved():
    store = EntityStore(FakeClock())
    state = FakeStateClient()
    rows = state_rows(state)
    store.ingest(frame(1000, knife()))
    store.sync_once(state)
    store.sync_once(state)
    store.ingest(frame(1033, knife(zone="plating-spot")))
    store.sync_once(state)

    assert [(row.version, row.t_ms, row.zone) for row in rows] == [(1, 1000, "slot-4"), (2, 1033, "plating-spot")]


def test_ingest_checks_frames_against_the_schema():
    bad = frame(1000, knife(allergen="peanut"))
    with pytest.raises(jsonschema.ValidationError, match="allergen"):
        EntityStore(FakeClock()).ingest(bad)
