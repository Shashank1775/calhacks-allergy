"""Contract suite for StateClient: what contracts/apis.md and contracts/state-tables.md promise, and nothing else.

Owner: platform (agents)
Step:  shared
Reads: the make_state_client and make_unavailable_state_client fixtures; contracts/fixtures/valid
Emits: pytest tests marked `contract` (and `safety` where a safety rule is at stake)
Rules: known-clean source (registration never changes first_seen_t_ms or origin; history append-only); late changes
       recheck everything (get_ticket returns the latest revision); fail closed (StateUnavailable from every method)

A real client delivers subscribe() changes from a background poll, so change-feed tests wait for them.
"""

import contextlib

import pytest

from kitchen.shared.contract_tests import contract_fixture, wait_until
from kitchen.shared.interfaces import StateClient, StateUnavailable
from kitchen.shared.models import EntityState, PlateBinding, Zone, validate


def _ticket(revision: int = 1, forbidden: tuple[str, ...] = ("peanut",)) -> dict:
    """Ticket t7 from the contract fixtures, at the given revision."""
    ticket = contract_fixture("ticket", "peanut-anaphylactic")
    ticket["revision"] = revision
    ticket["forbidden"] = list(forbidden)
    return ticket


def _entity(entity_id: str, *, origin: str = "shift_start", first_seen_t_ms: int = 0) -> dict:
    """The knife from the contract fixtures, renamed and re-kinded to `entity_id` (its kind is the ID's prefix)."""
    entity = contract_fixture("entity", "knife")
    entity["entity_id"] = entity_id
    entity["kind"] = entity_id.split("-", 1)[0]
    entity["origin"] = origin
    entity["first_seen_t_ms"] = first_seen_t_ms
    entity["last_seen_t_ms"] = first_seen_t_ms
    return entity


def _event(event_id: str, t_start_ms: int, t_end_ms: int) -> dict:
    """The scoop from the contract fixtures, with a new ID and times."""
    event = contract_fixture("interaction_event", "scoop")
    event.update(event_id=event_id, t_start_ms=t_start_ms, t_end_ms=t_end_ms)
    return event


def _verdict(verdict_id: str, ticket_id: str) -> dict:
    """The peanut hold from the contract fixtures, for another verdict ID and ticket."""
    verdict = contract_fixture("verdict", "hold-peanut")
    verdict.update(verdict_id=verdict_id, ticket_id=ticket_id)
    return verdict


def _state(entity_id: str, t_ms: int, zone: str | None, box: list[float] | None) -> EntityState:
    """An entity_state row for a zone and box change, as the entity store writes it. t_ms doubles as the version."""
    return EntityState(
        entity_id=entity_id, version=t_ms, t_ms=t_ms, zone=zone, box=box, probabilities={}, unknown=False
    )


@pytest.mark.contract
def test_get_ticket_is_none_for_a_ticket_never_sent(make_state_client):
    assert make_state_client().get_ticket("t404") is None


@pytest.mark.contract
def test_put_ticket_then_get_ticket_returns_it(make_state_client):
    client = make_state_client()
    client.put_ticket(_ticket())

    assert client.get_ticket("t7") == _ticket()


@pytest.mark.contract
@pytest.mark.safety
def test_an_amendment_replaces_the_ticket_with_the_next_revision(make_state_client):
    """Late changes recheck everything: whoever reads the ticket after an amendment gets the amended constraints."""
    client = make_state_client()
    client.put_ticket(_ticket(revision=1, forbidden=("peanut",)))
    client.put_ticket(_ticket(revision=2, forbidden=("milk", "peanut")))

    ticket = client.get_ticket("t7")
    assert ticket["revision"] == 2
    assert ticket["forbidden"] == ["milk", "peanut"]


@pytest.mark.contract
def test_put_ticket_twice_is_idempotent(make_state_client):
    client = make_state_client()
    client.put_ticket(_ticket())
    client.put_ticket(_ticket())

    assert client.get_ticket("t7") == _ticket()


@pytest.mark.contract
def test_a_bins_allergens_come_from_its_bin_profile(make_state_client):
    client = make_state_client()
    client.register_entity(_entity("bin-4"))
    satay = contract_fixture("allergen_profile", "bin-satay")
    client.put_bin_profile("bin-4", satay)

    assert client.get_entity("bin-4")["allergen_profile"] == satay


@pytest.mark.contract
def test_put_bin_profile_replaces_the_previous_profile(make_state_client):
    client = make_state_client()
    client.register_entity(_entity("bin-4"))
    client.put_bin_profile("bin-4", contract_fixture("allergen_profile", "bin-satay"))
    butter = {"probabilities": {"milk": 0.99}, "unknown": False, "source": "manual"}
    client.put_bin_profile("bin-4", butter)

    assert client.get_entity("bin-4")["allergen_profile"] == butter


@pytest.mark.contract
def test_set_zones_replaces_the_whole_calibration(make_state_client):
    client = make_state_client()
    square = [(0.0, 0.0), (0.2, 0.0), (0.2, 0.2), (0.0, 0.2)]
    client.set_zones([Zone("slot-1", "bin_slot", square), Zone("wash", "wash", square)])
    client.set_zones([Zone("pass", "pass", square)])

    assert [(zone.zone_id, zone.kind) for zone in client.list_zones()] == [("pass", "pass")]


@pytest.mark.contract
def test_get_plate_binding_is_none_for_a_plate_never_bound(make_state_client):
    assert make_state_client().get_plate_binding("plate-t404") is None


@pytest.mark.contract
def test_bind_plate_then_get_plate_binding_returns_it(make_state_client):
    client = make_state_client()
    client.bind_plate("plate-t7", "t7", 1000)

    assert client.get_plate_binding("plate-t7") == PlateBinding(plate_id="plate-t7", ticket_id="t7", t_ms=1000)


@pytest.mark.contract
@pytest.mark.safety
def test_a_plate_binds_once(make_state_client):
    """Rebinding would judge a plate against another guest's ticket: the first binding stands."""
    client = make_state_client()
    client.bind_plate("plate-t7", "t7", 1000)
    # Rejecting the second binding and ignoring it both keep the promise; changing the binding breaks it.
    with contextlib.suppress(Exception):
        client.bind_plate("plate-t7", "t8", 2000)

    assert client.get_plate_binding("plate-t7") == PlateBinding(plate_id="plate-t7", ticket_id="t7", t_ms=1000)


@pytest.mark.contract
def test_a_bound_plate_carries_its_ticket_id(make_state_client):
    client = make_state_client()
    client.register_entity(_entity("plate-t7"))
    client.bind_plate("plate-t7", "t7", 1000)

    assert client.get_entity("plate-t7")["ticket_id"] == "t7"


@pytest.mark.contract
def test_get_entity_is_none_for_an_entity_never_registered(make_state_client):
    assert make_state_client().get_entity("tool-knife-404") is None


@pytest.mark.contract
def test_register_entity_then_get_entity_returns_a_valid_entity(make_state_client):
    client = make_state_client()
    client.register_entity(_entity("tool-knife-17"))

    entity = client.get_entity("tool-knife-17")
    validate(entity, "entity")
    assert (entity["entity_id"], entity["kind"], entity["origin"]) == ("tool-knife-17", "tool", "shift_start")


@pytest.mark.contract
@pytest.mark.safety
def test_registering_again_never_changes_first_seen_or_origin(make_state_client):
    """Known-clean source: a later write can't turn an unknown-history spoon into a clean one."""
    client = make_state_client()
    client.register_entity(_entity("tool-spoon-9", origin="unknown", first_seen_t_ms=5000))
    client.register_entity(_entity("tool-spoon-9", origin="shift_start", first_seen_t_ms=0))

    entity = client.get_entity("tool-spoon-9")
    assert entity["origin"] == "unknown"
    assert entity["first_seen_t_ms"] == 5000


@pytest.mark.contract
def test_move_entity_sets_the_entitys_zone_and_box(make_state_client):
    client = make_state_client()
    client.register_entity(_entity("plate-t7"))
    client.move_entity(_state("plate-t7", 3000, "pass", [0.1, 0.1, 0.3, 0.3]))

    entity = client.get_entity("plate-t7")
    assert entity["zone"] == "pass"
    assert entity["box"] == [0.1, 0.1, 0.3, 0.3]


@pytest.mark.contract
@pytest.mark.safety
def test_entity_history_is_append_only(make_state_client):
    """Every move is a new entity_state row; no row is ever updated in place."""
    client = make_state_client()
    client.register_entity(_entity("plate-t7"))
    changes = []
    client.subscribe("entity_state", changes.append)
    client.move_entity(_state("plate-t7", 2000, "plating-spot", [0.1, 0.1, 0.3, 0.3]))
    client.move_entity(_state("plate-t7", 3000, "pass", [0.5, 0.1, 0.7, 0.3]))

    assert wait_until(lambda: len(changes) >= 2)
    assert [change.kind for change in changes] == ["insert", "insert"]


@pytest.mark.contract
def test_list_entities_filters_by_kind(make_state_client):
    client = make_state_client()
    for entity_id in ("bin-4", "tool-knife-17", "plate-t7"):
        client.register_entity(_entity(entity_id))

    assert {entity["entity_id"] for entity in client.list_entities()} == {"bin-4", "tool-knife-17", "plate-t7"}
    assert [entity["entity_id"] for entity in client.list_entities("tool")] == ["tool-knife-17"]


@pytest.mark.contract
def test_a_duplicate_event_id_is_a_no_op(make_state_client):
    client = make_state_client()
    client.record_interaction(_event("e1", 1000, 1400))
    client.record_interaction(_event("e1", 1000, 1400))

    assert [event["event_id"] for event in client.list_interactions()] == ["e1"]


@pytest.mark.contract
def test_list_interactions_orders_by_start_then_end_then_event_id(make_state_client):
    client = make_state_client()
    for event in (_event("c", 300, 400), _event("b", 100, 200), _event("a", 100, 200), _event("z", 100, 150)):
        client.record_interaction(event)

    assert [event["event_id"] for event in client.list_interactions()] == ["z", "a", "b", "c"]


@pytest.mark.contract
def test_list_interactions_keeps_to_the_time_range(make_state_client):
    client = make_state_client()
    for event in (_event("early", 100, 200), _event("middle", 5000, 5400), _event("late", 9000, 9400)):
        client.record_interaction(event)

    events = client.list_interactions(since_t_ms=1000, until_t_ms=8000)
    assert [event["event_id"] for event in events] == ["middle"]


@pytest.mark.contract
def test_list_verdicts_filters_by_ticket(make_state_client):
    client = make_state_client()
    client.record_verdict(_verdict("v-t7-1", "t7"))
    client.record_verdict(_verdict("v-t8-1", "t8"))

    assert {verdict["verdict_id"] for verdict in client.list_verdicts()} == {"v-t7-1", "v-t8-1"}
    assert [verdict["verdict_id"] for verdict in client.list_verdicts("t7")] == ["v-t7-1"]
    validate(client.list_verdicts("t7")[0], "verdict")


@pytest.mark.contract
def test_subscribe_delivers_inserts_and_updates(make_state_client):
    """An amendment arrives as an update to the ticket row, which is what triggers a recheck."""
    client = make_state_client()
    changes = []
    client.subscribe("ticket", changes.append)
    client.put_ticket(_ticket(revision=1))
    client.put_ticket(_ticket(revision=2, forbidden=("milk", "peanut")))

    assert wait_until(lambda: len(changes) >= 2)
    assert [(change.table, change.kind) for change in changes] == [("ticket", "insert"), ("ticket", "update")]
    assert [change.row["revision"] for change in changes] == [1, 2]


@pytest.mark.contract
def test_unsubscribe_stops_delivery(make_state_client):
    client = make_state_client()
    kept, dropped = [], []
    client.subscribe("verdict", kept.append)
    unsubscribe = client.subscribe("verdict", dropped.append)
    unsubscribe()
    client.record_verdict(_verdict("v-t7-1", "t7"))

    assert wait_until(lambda: len(kept) == 1)
    assert dropped == []


# One call per StateClient method, for the unreachable-database test below.
_CALLS = {
    "put_ticket": lambda client: client.put_ticket(_ticket()),
    "put_bin_profile": lambda client: client.put_bin_profile(
        "bin-4", contract_fixture("allergen_profile", "bin-satay")
    ),
    "set_zones": lambda client: client.set_zones([]),
    "bind_plate": lambda client: client.bind_plate("plate-t7", "t7", 1000),
    "register_entity": lambda client: client.register_entity(_entity("tool-knife-17")),
    "move_entity": lambda client: client.move_entity(_state("tool-knife-17", 1000, None, None)),
    "record_interaction": lambda client: client.record_interaction(_event("e1", 1000, 1400)),
    "record_verdict": lambda client: client.record_verdict(_verdict("v-t7-1", "t7")),
    "get_ticket": lambda client: client.get_ticket("t7"),
    "get_entity": lambda client: client.get_entity("tool-knife-17"),
    "list_entities": lambda client: client.list_entities(),
    "list_zones": lambda client: client.list_zones(),
    "get_plate_binding": lambda client: client.get_plate_binding("plate-t7"),
    "list_interactions": lambda client: client.list_interactions(),
    "list_verdicts": lambda client: client.list_verdicts(),
    "subscribe": lambda client: client.subscribe("verdict", lambda change: None),
}


@pytest.mark.contract
def test_the_unreachable_database_test_covers_every_method():
    methods = {name for name in vars(StateClient) if not name.startswith("_")}
    assert set(_CALLS) == methods


@pytest.mark.contract
@pytest.mark.safety
@pytest.mark.parametrize("method", sorted(_CALLS))
def test_every_method_raises_state_unavailable_when_the_database_is_unreachable(make_unavailable_state_client, method):
    """Fail closed: callers can only turn an outage into HOLD if it surfaces as StateUnavailable, not a wrong answer."""
    client = make_unavailable_state_client()
    with pytest.raises(StateUnavailable):
        _CALLS[method](client)
