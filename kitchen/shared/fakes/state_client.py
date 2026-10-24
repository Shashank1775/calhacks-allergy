"""FakeStateClient: the database as dicts in memory, with the same semantics as contracts/state-tables.md.

Owner: platform (agents)
Step:  shared
Reads: records written through the StateClient methods (validated against contracts/schemas on write)
Emits: the StateClient reads and ChangeEvents to subscribers
Rules: known-clean source: entities are never deleted and first_seen_t_ms and origin never change; entity history is
       append-only; a ticket row holds the latest revision and only revision + 1 replaces it (late changes recheck
       everything); set_unavailable() and fail_next() make every method raise StateUnavailable (fail closed)
"""

import copy
import threading
from collections.abc import Callable

from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import (
    AllergenProfile,
    ChangeEvent,
    Entity,
    EntityState,
    InteractionEvent,
    PlateBinding,
    Ticket,
    Verdict,
    Zone,
    validate,
)

# Every table in contracts/state-tables.md. subscribe() refuses any other name, so a typo fails instead of going quiet.
TABLES = (
    "allergen",
    "zone",
    "ticket",
    "bin_profile",
    "entity",
    "entity_state",
    "plate_binding",
    "interaction",
    "verdict",
)

# The columns of the entity table; the rest of an Entity comes from its latest state (contracts/state-tables.md).
IDENTITY_FIELDS = ("entity_id", "kind", "label", "origin", "first_seen_t_ms")


def interaction_order(event: InteractionEvent) -> tuple[int, int, str]:
    """Sort key for interactions: t_start_ms, then t_end_ms, then event_id (AGENTS.md, Open questions)."""
    return (event["t_start_ms"], event["t_end_ms"], event["event_id"])


class FakeStateClient:
    """A dict-backed StateClient for tests: every method, subscribe() included, and failure on cue.

    Writes are checked against the contract schemas, so a malformed record fails the test that wrote it. Reads and
    change events hand out copies, so a caller can't change the stored rows by editing what it got back.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickets: dict[str, Ticket] = {}
        self._bin_profiles: dict[str, AllergenProfile] = {}
        self._zones: dict[str, Zone] = {}
        self._plate_bindings: dict[str, PlateBinding] = {}
        self._entities: dict[str, Entity] = {}  # exactly as first registered
        self._entity_states: dict[str, list[EntityState]] = {}
        self._interactions: dict[str, InteractionEvent] = {}
        self._verdicts: dict[str, Verdict] = {}
        self._subscribers: dict[str, list[Callable[[ChangeEvent], None]]] = {table: [] for table in TABLES}
        self._unavailable = False
        self._failures_left = 0

    # failure injection

    def set_unavailable(self, unavailable: bool = True) -> None:
        """Make every StateClient method raise StateUnavailable until called again with False."""
        self._unavailable = unavailable

    def fail_next(self, n: int) -> None:
        """Make the next `n` StateClient calls raise StateUnavailable, then work normally again."""
        self._failures_left = n

    def _check_reachable(self) -> None:
        """Raise StateUnavailable if a failure was injected; every StateClient method calls this first."""
        if self._unavailable:
            raise StateUnavailable("database unreachable (FakeStateClient.set_unavailable)")
        if self._failures_left > 0:
            self._failures_left -= 1
            raise StateUnavailable("database unreachable (FakeStateClient.fail_next)")

    def _notify(self, table: str, kind: str, row: object) -> None:
        """Deliver one ChangeEvent to every subscriber of `table`, synchronously and in subscription order."""
        for on_change in list(self._subscribers[table]):
            on_change(ChangeEvent(table=table, kind=kind, row=copy.deepcopy(row)))

    # writes

    def put_ticket(self, ticket: Ticket) -> None:
        """Insert a ticket, or replace it with the next revision (an amendment). Repeating the same write is a no-op.

        Any other revision raises ValueError: writing an older revision would undo an amendment, and the plate would
        be judged against constraints the guest already changed (safety rule: late changes recheck everything).
        """
        validate(ticket, "ticket")
        with self._lock:
            self._check_reachable()
            current = self._tickets.get(ticket["ticket_id"])
            if current == ticket:
                return
            if current is not None and ticket["revision"] != current["revision"] + 1:
                raise ValueError(
                    f"ticket {ticket['ticket_id']} is at revision {current['revision']}; "
                    f"an amendment must be revision {current['revision'] + 1}, got {ticket['revision']}"
                )
            self._tickets[ticket["ticket_id"]] = copy.deepcopy(ticket)
            self._notify("ticket", "insert" if current is None else "update", ticket)

    def put_bin_profile(self, entity_id: str, profile: AllergenProfile) -> None:
        """Insert or replace a bin's allergen profile."""
        validate(profile, "allergen_profile")
        with self._lock:
            self._check_reachable()
            kind = "update" if entity_id in self._bin_profiles else "insert"
            self._bin_profiles[entity_id] = copy.deepcopy(profile)
            self._notify("bin_profile", kind, {"entity_id": entity_id, **profile})

    def set_zones(self, zones: list[Zone]) -> None:
        """Replace the whole calibration with `zones`."""
        with self._lock:
            self._check_reachable()
            previous = self._zones
            self._zones = {zone.zone_id: zone for zone in zones}
            for zone in zones:
                self._notify("zone", "update" if zone.zone_id in previous else "insert", zone)

    def bind_plate(self, plate_id: str, ticket_id: str, t_ms: int) -> None:
        """Bind a plate to a ticket. A plate binds once: repeating the same binding is a no-op, anything else raises.

        Rebinding would silently judge the plate against another guest's ticket, so it fails loudly instead.
        """
        binding = PlateBinding(plate_id=plate_id, ticket_id=ticket_id, t_ms=t_ms)
        with self._lock:
            self._check_reachable()
            current = self._plate_bindings.get(plate_id)
            if current == binding:
                return
            if current is not None:
                raise ValueError(f"plate {plate_id} is already bound to {current.ticket_id}")
            self._plate_bindings[plate_id] = binding
            self._notify("plate_binding", "insert", binding)

    def register_entity(self, entity: Entity) -> None:
        """Insert an entity once. Registering it again is a no-op, whatever the second record says.

        first_seen_t_ms and origin are fixed at the first registration: letting a later write change them could turn
        an unknown-history tool into a clean one (safety rule: known-clean source).
        """
        validate(entity, "entity")
        with self._lock:
            self._check_reachable()
            if entity["entity_id"] in self._entities:
                return
            self._entities[entity["entity_id"]] = copy.deepcopy(entity)
            self._notify("entity", "insert", {field: entity[field] for field in IDENTITY_FIELDS})

    def move_entity(self, state: EntityState) -> None:
        """Append a state row for a registered entity. Rows are never edited or removed (history is append-only)."""
        with self._lock:
            self._check_reachable()
            if state.entity_id not in self._entities:
                raise ValueError(f"move_entity for unregistered entity {state.entity_id}")
            self._entity_states.setdefault(state.entity_id, []).append(state)
            self._notify("entity_state", "insert", state)

    def record_interaction(self, event: InteractionEvent) -> None:
        """Insert an interaction edge. A duplicate event_id is a no-op."""
        validate(event, "interaction_event")
        with self._lock:
            self._check_reachable()
            if event["event_id"] in self._interactions:
                return
            self._interactions[event["event_id"]] = copy.deepcopy(event)
            self._notify("interaction", "insert", event)

    def record_verdict(self, verdict: Verdict) -> None:
        """Insert a verdict, chain included. A duplicate verdict_id is a no-op."""
        validate(verdict, "verdict")
        with self._lock:
            self._check_reachable()
            if verdict["verdict_id"] in self._verdicts:
                return
            self._verdicts[verdict["verdict_id"]] = copy.deepcopy(verdict)
            self._notify("verdict", "insert", verdict)

    # reads

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        """Return the latest revision of a ticket, or None."""
        with self._lock:
            self._check_reachable()
            return copy.deepcopy(self._tickets.get(ticket_id))

    def get_entity(self, entity_id: str) -> Entity | None:
        """Return the entity joined with its latest state, or None if it was never registered."""
        with self._lock:
            self._check_reachable()
            if entity_id not in self._entities:
                return None
            return self._joined_entity(entity_id)

    def list_entities(self, kind: str | None = None) -> list[Entity]:
        """Return every registered entity (joined with its latest state), optionally only one kind."""
        with self._lock:
            self._check_reachable()
            return [
                self._joined_entity(entity_id)
                for entity_id, entity in self._entities.items()
                if kind is None or entity["kind"] == kind
            ]

    def list_zones(self) -> list[Zone]:
        """Return the current calibration."""
        with self._lock:
            self._check_reachable()
            return list(self._zones.values())

    def get_plate_binding(self, plate_id: str) -> PlateBinding | None:
        """Return the plate's binding, or None if it was never bound."""
        with self._lock:
            self._check_reachable()
            return self._plate_bindings.get(plate_id)

    def list_interactions(self, since_t_ms: int = 0, until_t_ms: int | None = None) -> list[InteractionEvent]:
        """Return interactions with since_t_ms <= t_start_ms <= until_t_ms, ordered by t_start_ms, t_end_ms, event_id.

        Copying a shift's thousands of edges holds the lock for tens of milliseconds, and the camera's frame loop writes
        edges through this client, so only picking them happens under the lock. Stored edges are never edited, so the
        copy is safe outside it.
        """
        with self._lock:
            self._check_reachable()
            events = [
                event
                for event in self._interactions.values()
                if event["t_start_ms"] >= since_t_ms and (until_t_ms is None or event["t_start_ms"] <= until_t_ms)
            ]
        return sorted(copy.deepcopy(events), key=interaction_order)

    def list_verdicts(self, ticket_id: str | None = None) -> list[Verdict]:
        """Return every verdict in the order recorded, optionally only one ticket's."""
        with self._lock:
            self._check_reachable()
            return [
                copy.deepcopy(verdict)
                for verdict in self._verdicts.values()
                if ticket_id is None or verdict["ticket_id"] == ticket_id
            ]

    # change feed

    def subscribe(self, table: str, on_change: Callable[[ChangeEvent], None]) -> Callable[[], None]:
        """Call `on_change` for every insert or update to `table` from now on. Returns a function that unsubscribes."""
        with self._lock:
            self._check_reachable()
            if table not in self._subscribers:
                raise ValueError(f"no table named {table!r}; tables are {TABLES}")
            self._subscribers[table].append(on_change)

        def unsubscribe() -> None:
            with self._lock:
                if on_change in self._subscribers[table]:
                    self._subscribers[table].remove(on_change)

        return unsubscribe

    # the logical Entity (contracts/state-tables.md)

    def _joined_entity(self, entity_id: str) -> Entity:
        """The entity row joined with its latest state, as contracts/state-tables.md describes the logical Entity.

        Zone, box and last seen come from the newest state row, allergens from _allergen_profile_for(), and a plate's
        ticket_id from its binding.
        """
        entity = copy.deepcopy(self._entities[entity_id])
        states = self._entity_states.get(entity_id, [])
        if states:
            latest = states[-1]
            entity["zone"] = latest.zone
            entity["box"] = list(latest.box) if latest.box is not None else None
            entity["last_seen_t_ms"] = latest.t_ms
        entity["allergen_profile"] = self._allergen_profile_for(entity, states)
        binding = self._plate_bindings.get(entity_id)
        if binding is not None:
            entity["ticket_id"] = binding.ticket_id
        return entity

    def _allergen_profile_for(self, entity: Entity, states: list[EntityState]) -> AllergenProfile:
        """What the entity may carry: the graph's latest state, else a bin's profile, else what it registered with.

        A state with caused_by set is one the graph wrote because of an interaction. The entity store's zone and box
        updates leave caused_by empty, so they can never overwrite allergens the graph found with a stale "clean".
        """
        graph_states = [state for state in states if state.caused_by is not None]
        if graph_states:
            latest = graph_states[-1]
            return {"probabilities": dict(latest.probabilities), "unknown": latest.unknown, "source": "propagated"}
        if entity["entity_id"] in self._bin_profiles:
            return copy.deepcopy(self._bin_profiles[entity["entity_id"]])
        return entity["allergen_profile"]
