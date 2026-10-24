"""Contract suite for EntityStore: what contracts/apis.md promises, and nothing else.

Owner: platform (agents)
Step:  shared
Reads: the make_entity_store fixture; contracts/fixtures/valid/observation_frame
Emits: pytest tests marked `contract` (and `safety` where a safety rule is at stake)
Rules: known-clean source: an entity is never deleted and its first_seen_t_ms and origin never change; start_sync()
       writes to whatever StateClient it is given (here the platform's FakeStateClient)
"""

import time

import pytest

from kitchen.shared.contract_tests import contract_fixture, wait_until
from kitchen.shared.fakes.state_client import FakeStateClient
from kitchen.shared.models import validate


def _frame(t_ms: int, *detections: dict) -> dict:
    """An ObservationFrame at `t_ms` holding exactly `detections`."""
    frame = contract_fixture("observation_frame", "two-detections")
    frame.update(frame_id=t_ms // 33, t_ms=t_ms, detections=list(detections))
    return frame


def _knife(*, origin: str = "shift_start", box: list[float] | None = None, zone: str | None = "slot-4") -> dict:
    """The knife detection from the contract fixture, with its origin, box and zone set."""
    knife = contract_fixture("observation_frame", "two-detections")["detections"][0]
    knife.update(origin=origin, zone=zone, box=box or [0.41, 0.52, 0.47, 0.71])
    return knife


def _hand() -> dict:
    """The hand detection from the contract fixture."""
    return contract_fixture("observation_frame", "two-detections")["detections"][1]


@pytest.mark.contract
def test_get_is_none_for_an_entity_never_seen(make_entity_store):
    assert make_entity_store().get("tool-knife-17") is None


@pytest.mark.contract
def test_ingest_then_get_returns_a_valid_entity_from_the_frame(make_entity_store):
    store = make_entity_store()
    store.ingest(_frame(1000, _knife()))

    knife = store.get("tool-knife-17")
    validate(knife, "entity")
    assert (knife["kind"], knife["origin"], knife["zone"]) == ("tool", "shift_start", "slot-4")
    assert knife["box"] == [0.41, 0.52, 0.47, 0.71]
    assert knife["first_seen_t_ms"] == knife["last_seen_t_ms"] == 1000


@pytest.mark.contract
def test_snapshot_lists_every_entity_seen(make_entity_store):
    store = make_entity_store()
    store.ingest(_frame(1000, _knife(), _hand()))

    assert {entity["entity_id"] for entity in store.snapshot()} == {"tool-knife-17", "hand-p1-right"}


@pytest.mark.contract
def test_a_later_frame_moves_the_entity(make_entity_store):
    store = make_entity_store()
    store.ingest(_frame(1000, _knife()))
    store.ingest(_frame(2000, _knife(box=[0.6, 0.5, 0.66, 0.7], zone="plating-spot")))

    knife = store.get("tool-knife-17")
    assert knife["zone"] == "plating-spot"
    assert knife["box"] == [0.6, 0.5, 0.66, 0.7]
    assert knife["last_seen_t_ms"] == 2000


@pytest.mark.contract
@pytest.mark.safety
def test_an_entity_missing_from_later_frames_is_never_deleted(make_entity_store):
    """Known-clean source: an entity's history must outlive the frames that saw it."""
    store = make_entity_store()
    store.ingest(_frame(1000, _knife(), _hand()))
    store.ingest(_frame(2000, _hand()))

    assert store.get("tool-knife-17") is not None
    assert {entity["entity_id"] for entity in store.snapshot()} == {"tool-knife-17", "hand-p1-right"}


@pytest.mark.contract
@pytest.mark.safety
def test_first_seen_and_origin_never_change(make_entity_store):
    """Known-clean source: a later frame claiming shift_start can't make an unknown-history knife clean."""
    store = make_entity_store()
    store.ingest(_frame(5000, _knife(origin="unknown")))
    store.ingest(_frame(6000, _knife(origin="shift_start")))

    knife = store.get("tool-knife-17")
    assert knife["origin"] == "unknown"
    assert knife["first_seen_t_ms"] == 5000


@pytest.mark.contract
def test_last_sync_is_none_before_any_sync(make_entity_store):
    assert make_entity_store().last_sync_t_ms() is None


@pytest.mark.contract
def test_start_sync_writes_entities_to_the_given_state_client(make_entity_store):
    store = make_entity_store()
    state = FakeStateClient()
    store.ingest(_frame(1000, _knife()))
    store.start_sync(state, hz=50.0)
    try:
        assert wait_until(lambda: state.get_entity("tool-knife-17") is not None)
        assert wait_until(lambda: store.last_sync_t_ms() is not None)
    finally:
        store.stop_sync()

    knife = state.get_entity("tool-knife-17")
    assert (knife["origin"], knife["first_seen_t_ms"]) == ("shift_start", 1000)
    assert knife["zone"] == "slot-4"


@pytest.mark.contract
def test_stop_sync_stops_writing(make_entity_store):
    store = make_entity_store()
    state = FakeStateClient()
    store.start_sync(state, hz=50.0)
    store.stop_sync()
    store.ingest(_frame(1000, _knife()))
    time.sleep(0.2)  # ten sync periods at 50 Hz: a sync still running would have written by now

    assert state.get_entity("tool-knife-17") is None
