"""SpacetimeStateClient on a real local SpacetimeDB: what the shared contract suite doesn't pin (skips without one).

Owner: partner (written by an agent at the owner's request)
Step:  database
Reads: database/state_client.py; the module in database/allergen-yhz-81; fixtures in database/tests/conftest.py
Emits: nothing
Rules: late changes recheck everything: only the next revision replaces a ticket; known-clean source: an unknown
       origin reads as unknown, and a zone update never hides the graph's allergens; fail closed: a missing database
       raises StateUnavailable from every method
"""

import copy

import pytest

from database.state_client import SpacetimeStateClient
from kitchen.shared.contract_tests import contract_fixture, wait_until
from kitchen.shared.contract_tests.state_client import _CALLS
from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import EntityState, Zone, validate


def ticket(revision: int, forbidden: list[str]) -> dict:
    """Ticket t7 from the contract fixtures at a revision."""
    record = contract_fixture("ticket", "peanut-anaphylactic")
    return {**record, "revision": revision, "forbidden": forbidden}


def entity(entity_id: str, origin: str = "shift_start") -> dict:
    """The fixture knife as `entity_id`, its kind from the ID's prefix."""
    record = contract_fixture("entity", "knife")
    return {**record, "entity_id": entity_id, "kind": entity_id.split("-", 1)[0], "origin": origin}


def event(event_id: str, a_id: str, b_id: str, action: str = "transfer.contact", **extra) -> dict:
    """The fixture scoop with new ends and action."""
    record = contract_fixture("interaction_event", "scoop")
    record.update(event_id=event_id, a_id=a_id, b_id=b_id, action=action, **extra)
    record.pop("evidence", None)
    if "evidence" in extra:
        record["evidence"] = extra["evidence"]
    return record


@pytest.mark.safety
@pytest.mark.parametrize("stale", [1, 2, 4], ids=["older", "same-with-other-content", "skipped-ahead"])
def test_only_the_next_revision_replaces_a_ticket(make_state_client, stale):
    """Late changes recheck everything: a delayed or retried write of another revision can't undo an amendment."""
    client = make_state_client()
    client.put_ticket(ticket(1, ["peanut"]))
    client.put_ticket(ticket(2, ["milk", "peanut"]))

    with pytest.raises(ValueError):
        client.put_ticket(ticket(stale, ["sesame"]))
    assert client.get_ticket("t7") == ticket(2, ["milk", "peanut"])


def test_moving_an_unregistered_entity_is_refused(make_state_client):
    with pytest.raises(ValueError):
        make_state_client().move_entity(EntityState("tool-knife-404", 1, 1000, None, None, {}, False))


def test_an_interaction_reads_back_as_written(make_state_client):
    """Ends, direction and evidence round-trip: a swapped edge would point contamination the wrong way."""
    client = make_state_client()
    knife_to_plate = event(
        "e1", "tool-knife-17", "plate-t7", direction="a_to_b", evidence={"frame_start": 10, "clip_uri": "c.npz"}
    )
    bare = event("e2", "hand-p1-left", "plate-t7", t_start_ms=200000, t_end_ms=200400, evidence={})
    client.record_interaction(knife_to_plate)
    client.record_interaction(bare)

    assert client.list_interactions() == [knife_to_plate, bare]


def test_zones_read_back_as_written_and_a_repeated_id_keeps_its_last(make_state_client):
    client = make_state_client()
    first = Zone("pass", "pass", [(0.8, 0.0), (1.0, 0.0), (1.0, 1.0), (0.8, 1.0)])
    wash = Zone("wash", "wash", [(0.0, 0.7), (0.3, 0.7), (0.3, 1.0), (0.0, 1.0)])
    last = Zone("pass", "pass", [(0.7, 0.0), (1.0, 0.0), (1.0, 1.0), (0.7, 1.0)])
    client.set_zones([first, wash, last])

    assert client.list_zones() == [last, wash]


def test_zone_box_and_last_seen_come_from_the_newest_state(make_state_client):
    """Rows written out of time order: the newest by t_ms is where the plate is, in get_entity and list_entities."""
    client = make_state_client()
    client.register_entity(entity("plate-t7"))
    client.move_entity(EntityState("plate-t7", 2, 3000, "pass", [0.8, 0.1, 0.9, 0.2], {}, False))
    client.move_entity(EntityState("plate-t7", 1, 2000, "plating-spot", [0.4, 0.4, 0.5, 0.5], {}, False))

    plate = client.get_entity("plate-t7")
    assert (plate["zone"], plate["box"], plate["last_seen_t_ms"]) == ("pass", [0.8, 0.1, 0.9, 0.2], 3000)
    assert client.list_entities("plate") == [plate]


@pytest.mark.safety
def test_a_later_zone_update_never_hides_the_graphs_allergens(make_state_client):
    """Known-clean source: the entity store's newer zone row (no caused_by) can't make a contaminated knife clean."""
    client = make_state_client()
    client.register_entity(entity("tool-knife-17"))
    client.record_interaction(event("e1", "tool-knife-17", "bin-4", action="transfer.scoop"))
    client.move_entity(EntityState("tool-knife-17", 1, 1400, "slot-4", None, {"peanut": 0.9}, False, "e1"))
    client.move_entity(EntityState("tool-knife-17", 2, 2000, "prep", None, {}, False))

    profile = client.get_entity("tool-knife-17")["allergen_profile"]
    assert profile == {"probabilities": {"peanut": 0.9}, "unknown": False, "source": "propagated"}
    assert client.get_entity("tool-knife-17")["zone"] == "prep"


@pytest.mark.safety
def test_an_unknown_origin_with_no_state_reads_as_unknown(make_state_client):
    """Known-clean source: a re-acquired spoon nobody has written a state for carries every allergen."""
    client = make_state_client()
    spoon = entity("tool-spoon-9", origin="unknown")
    spoon["allergen_profile"] = {"probabilities": {}, "unknown": True, "source": "unknown_history"}
    client.register_entity(spoon)

    assert client.get_entity("tool-spoon-9")["allergen_profile"]["unknown"] is True
    assert client.list_entities("tool")[0]["allergen_profile"]["unknown"] is True


def test_the_allergen_source_follows_state_tables(make_state_client):
    """contracts/state-tables.md: a wash gives wash_reset, unknown gives unknown_history, any other edge propagated."""
    client = make_state_client()
    client.register_entity(entity("tool-knife-17"))
    client.record_interaction(event("wash-1", "tool-knife-17", "tool-knife-17", action="clean.wash"))
    client.move_entity(EntityState("tool-knife-17", 1, 1000, "wash", None, {}, False, "wash-1"))
    assert client.get_entity("tool-knife-17")["allergen_profile"]["source"] == "wash_reset"

    client.move_entity(EntityState("tool-knife-17", 2, 2000, "prep", None, {}, True, "wash-1"))
    assert client.get_entity("tool-knife-17")["allergen_profile"]["source"] == "unknown_history"


def test_a_late_subscriber_sees_only_what_changes_after_it_subscribed(make_state_client):
    """Each subscription takes its own snapshot, for keyed tables and for append-only entity_state alike."""
    client = make_state_client()
    early, late, early_states, late_states = [], [], [], []
    client.register_entity(entity("plate-t7"))
    client.subscribe("ticket", early.append)
    client.subscribe("entity_state", early_states.append)
    client.put_ticket(ticket(1, ["peanut"]))
    client.move_entity(EntityState("plate-t7", 1, 1000, "plating-spot", None, {}, False))

    client.subscribe("ticket", late.append)
    client.subscribe("entity_state", late_states.append)
    eight = copy.deepcopy(ticket(1, ["milk"]))
    eight.update(ticket_id="t8", number=8)
    client.put_ticket(eight)
    client.move_entity(EntityState("plate-t7", 2, 2000, "pass", None, {}, False))

    assert wait_until(lambda: len(late) == 1 and len(late_states) == 1)
    assert [change.row["ticket_id"] for change in early] == ["t7", "t8"]
    assert [change.row["ticket_id"] for change in late] == ["t8"]
    assert [change.row.zone for change in early_states] == ["plating-spot", "pass"]
    assert [change.row.zone for change in late_states] == ["pass"]
    validate(late[0].row, "ticket")


@pytest.mark.safety
@pytest.mark.parametrize("method", sorted(_CALLS))
def test_a_missing_database_raises_state_unavailable(test_database, method):
    """Fail closed: a server that answers but has no such database (unpublished, mistyped) is an outage, never an
    empty kitchen."""
    uri, _ = test_database
    client = SpacetimeStateClient(uri, "no-such-database-for-tests")
    with pytest.raises(StateUnavailable):
        _CALLS[method](client)
