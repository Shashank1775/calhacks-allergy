"""Tests for FakeStateClient beyond the shared contract suite: failure on cue, loud misuse, and the Entity join.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.fakes.state_client, contracts/fixtures/valid
Emits: nothing
Rules: fail closed (StateUnavailable on cue); late changes recheck everything (no stale revisions); known-clean
       source (store updates never overwrite the graph's allergens)
"""

import copy

import jsonschema
import pytest

from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.fakes import FakeStateClient
from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import EntityState


def knife():
    """The knife from the contract fixtures."""
    return contract_fixture("entity", "knife")


def store_move(t_ms, zone):
    """A zone and box update as the entity store writes it: no allergens of its own, no causing event."""
    return EntityState("tool-knife-17", t_ms, t_ms, zone, [0.4, 0.5, 0.5, 0.7], {}, False)


@pytest.mark.safety
def test_fail_next_fails_exactly_that_many_calls():
    client = FakeStateClient()
    client.put_ticket(contract_fixture("ticket", "peanut-anaphylactic"))
    client.fail_next(2)

    for _ in range(2):
        with pytest.raises(StateUnavailable):
            client.get_ticket("t7")
    assert client.get_ticket("t7")["ticket_id"] == "t7"


@pytest.mark.safety
def test_set_unavailable_lasts_until_turned_off():
    client = FakeStateClient()
    client.set_unavailable(True)
    for _ in range(3):
        with pytest.raises(StateUnavailable):
            client.list_verdicts()

    client.set_unavailable(False)
    assert client.list_verdicts() == []


@pytest.mark.safety
@pytest.mark.parametrize("revision", [1, 2, 4])
def test_put_ticket_refuses_anything_but_the_next_revision(revision):
    """Late changes recheck everything: a stale, conflicting or skipped revision would lose or misorder an amendment."""
    client = FakeStateClient()
    amended = contract_fixture("ticket", "peanut-anaphylactic")
    amended.update(revision=2, forbidden=["milk", "peanut"])
    client.put_ticket(contract_fixture("ticket", "peanut-anaphylactic"))
    client.put_ticket(amended)

    stale = contract_fixture("ticket", "peanut-anaphylactic")
    stale["revision"] = revision
    with pytest.raises(ValueError, match="revision"):
        client.put_ticket(stale)
    assert client.get_ticket("t7") == amended


def test_rebinding_a_plate_to_another_ticket_raises():
    client = FakeStateClient()
    client.bind_plate("plate-t7", "t7", 1000)
    with pytest.raises(ValueError, match="already bound"):
        client.bind_plate("plate-t7", "t8", 2000)


def test_moving_an_unregistered_entity_raises():
    with pytest.raises(ValueError, match="unregistered"):
        FakeStateClient().move_entity(store_move(1000, "slot-4"))


def test_writes_are_checked_against_the_schemas():
    ticket = contract_fixture("ticket", "peanut-anaphylactic")
    ticket["forbidden_entities"] = ["bin-4"]
    with pytest.raises(jsonschema.ValidationError):
        FakeStateClient().put_ticket(ticket)


def test_subscribe_refuses_an_unknown_table():
    with pytest.raises(ValueError, match="no table"):
        FakeStateClient().subscribe("tickets", lambda change: None)


def test_reads_are_copies():
    client = FakeStateClient()
    client.put_ticket(contract_fixture("ticket", "peanut-anaphylactic"))
    client.get_ticket("t7")["forbidden"].clear()

    assert client.get_ticket("t7")["forbidden"] == ["peanut"]


@pytest.mark.safety
def test_store_updates_never_overwrite_the_graphs_allergens():
    """Known-clean source: a later zone update carrying a stale 'clean' must not wash a knife the graph found peanut on."""
    client = FakeStateClient()
    client.register_entity(knife())
    client.move_entity(EntityState("tool-knife-17", 1, 1000, "slot-1", None, {"peanut": 0.9}, False, caused_by="e1"))
    client.move_entity(store_move(2000, "plating-spot"))

    entity = client.get_entity("tool-knife-17")
    assert entity["zone"] == "plating-spot"
    assert entity["last_seen_t_ms"] == 2000
    assert entity["allergen_profile"]["probabilities"] == {"peanut": 0.9}


def test_an_entity_with_no_graph_state_keeps_the_profile_it_registered_with():
    client = FakeStateClient()
    client.register_entity(knife())
    client.move_entity(store_move(2000, "plating-spot"))

    assert client.get_entity("tool-knife-17")["allergen_profile"] == knife()["allergen_profile"]


def test_a_row_with_the_same_t_ms_and_version_replaces_the_one_before_it():
    """The real database breaks the tie the same way: the row written later is the newer one."""
    client = FakeStateClient()
    client.register_entity(knife())
    client.move_entity(store_move(2000, "slot-1"))
    client.move_entity(store_move(2000, "plating-spot"))

    assert client.get_entity("tool-knife-17")["zone"] == "plating-spot"


def test_list_interactions_copies_the_edges_outside_the_lock(monkeypatch):
    """A long shift's edges take tens of milliseconds to copy. Under the lock that would stall every writer, the
    camera's frame loop included, so only picking the edges happens inside it."""
    client = FakeStateClient()
    client.record_interaction(contract_fixture("interaction_event", "scoop"))
    lock_held_while_copying = []
    deepcopy = copy.deepcopy

    def deepcopy_that_notes_the_lock(value, *args):
        lock_held_while_copying.append(client._lock._is_owned())
        return deepcopy(value, *args)

    monkeypatch.setattr(copy, "deepcopy", deepcopy_that_notes_the_lock)

    assert len(client.list_interactions()) == 1
    assert lock_held_while_copying and not any(lock_held_while_copying)
