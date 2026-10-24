"""SpacetimeStateClient: the real StateClient, over SpacetimeDB's HTTP API (reducer calls plus SQL polling).

Owner: partner (written by an agent at the owner's request)
Step:  database
Reads: a SpacetimeDB server (SPACETIMEDB_URI) running the module in database/allergen-yhz-81 (SPACETIMEDB_DB_NAME)
Emits: the StateClient methods of contracts/apis.md; ChangeEvents to subscribers, shaped as FakeStateClient's
Rules: fail closed: a network error, a timeout or an unreachable database raises StateUnavailable from every method;
       a reducer the module refuses (an older ticket revision, rebinding a plate) raises ValueError, as the fake does;
       known-clean source: an entity with no state takes its allergens from its origin, so a registration can never
       claim a clean history; writes are checked against the contract schemas before they leave

Reads and the change feed are SQL queries. An entity is joined through the module's entity_latest table, so a read
never scans the entity_state history. Each subscribe() takes its own snapshot of the table; after that, changes arrive
two ways: each write through this client delivers its own change events before it returns (as FakeStateClient does
inside the write), plus whatever its callbacks write in turn, for a few rounds; and a background thread polls every
`poll_s` for writes from other processes. Callbacks run one at a time, on whichever thread polled. Several changes to
one row between two polls arrive as one change, with the latest row.
"""

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from kitchen.shared.fakes.state_client import TABLES, interaction_order
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

logger = logging.getLogger(__name__)

DEFAULT_URI = "http://localhost:3000"
DEFAULT_DATABASE = "allergen-yhz81"

# HTTP 530 is how SpacetimeDB reports a reducer that returned an error: the module refused the write.
REDUCER_REFUSED = 530

# A write delivers its own change, and what its callbacks write in turn (a verdict recorded from inside an
# entity_state callback), in at most this many rounds; anything later comes with the background poll. Bounded so a
# write never waits on another process that keeps writing.
WRITE_ROUNDS = 3

# The primary key of every table but entity_state, which is append-only and keyed by its auto-increment state_id.
KEYS = {
    "allergen": "id",
    "zone": "zone_id",
    "ticket": "ticket_id",
    "bin_profile": "entity_id",
    "entity": "entity_id",
    "plate_binding": "plate_id",
    "interaction": "event_id",
    "verdict": "verdict_id",
}


def some(value):
    """A reducer argument for an Option column: SpacetimeDB's JSON for Some(value) or None."""
    return {"none": []} if value is None else {"some": value}


def quoted(text: str) -> str:
    """A SQL string literal. IDs come from our own services, but a quote in one must never change the query."""
    return "'" + str(text).replace("'", "''") + "'"


def decode(value, algebraic_type: dict):
    """One SQL result value as plain Python, guided by its SpacetimeDB type.

    Products become dicts by field name, arrays become lists, and an Option (a sum of `some` and `none`) becomes
    the value or None. SpacetimeDB sends sum values as [variant index, payload].
    """
    kind, inner = next(iter(algebraic_type.items()))
    if kind == "Product":
        return {
            field["name"]["some"]: decode(item, field["algebraic_type"])
            for field, item in zip(inner["elements"], value, strict=True)
        }
    if kind == "Array":
        return [decode(item, inner) for item in value]
    if kind == "Sum":
        index, payload = value
        variant = inner["variants"][index]
        if variant["name"]["some"] == "none":
            return None
        return decode(payload, variant["algebraic_type"])
    return value


def starting_profile(kind: str, origin: str) -> AllergenProfile:
    """What an entity carries before the graph or a bin profile says more (safety rule: known-clean source).

    Tools, hands and plates are clean only with a known-clean origin; anything else, bins included, is unknown and
    counts as carrying every allergen. Matches kitchen.shared.fakes.entity_store.starting_profile.
    """
    if kind != "bin" and origin in ("shift_start", "clean_stack"):
        return {"probabilities": {}, "unknown": False, "source": "shift_start_clean"}
    return {"probabilities": {}, "unknown": True, "source": "unknown_history"}


def probabilities_of(rows: list[dict]) -> dict[str, float]:
    """An allergen profile's probabilities column (a list of {allergen_unit_id, probability}) as a dict."""
    return {row["allergen_unit_id"]: row["probability"] for row in rows}


def without_none(record: dict) -> dict:
    """The record with its None fields left out: optional contract fields are absent, never null."""
    return {key: value for key, value in record.items() if value is not None}


class SpacetimeStateClient:
    """A StateClient backed by a SpacetimeDB database, reached over HTTP. Safe to share between threads."""

    def __init__(
        self,
        uri: str | None = None,
        database: str | None = None,
        timeout_s: float = 2.0,
        poll_s: float = 0.2,
    ) -> None:
        self.uri = (uri or os.environ.get("SPACETIMEDB_URI") or DEFAULT_URI).rstrip("/")
        self.database = database or os.environ.get("SPACETIMEDB_DB_NAME") or DEFAULT_DATABASE
        self.poll_s = poll_s
        self._http = httpx.Client(timeout=timeout_s)
        self._token: str | None = None
        self._lock = threading.RLock()  # one poll at a time; reentrant so a callback may write
        self._polling = False
        self._subscriptions: list[Subscription] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------- HTTP ----------

    def _headers(self) -> dict:
        """An Authorization header, asking the server for an identity the first time. Any identity may call."""
        if self._token is None:
            response = self._request("POST", f"{self.uri}/v1/identity")
            try:
                self._token = response.json()["token"]
            except (ValueError, KeyError, TypeError) as error:  # fail closed: no identity, no answers
                raise StateUnavailable(f"SpacetimeDB gave no identity ({response.status_code}): {error}") from error
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One HTTP request. Anything but an answer from the server raises StateUnavailable (fail closed)."""
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise StateUnavailable(f"SpacetimeDB at {self.uri} unreachable: {error}") from error
        if response.status_code >= 500 and response.status_code != REDUCER_REFUSED:
            raise StateUnavailable(f"SpacetimeDB answered {response.status_code}: {response.text}")
        return response

    def _call(self, reducer: str, args: dict) -> None:
        """Call a reducer, then deliver this write's change events before returning.

        A reducer the module refused raises ValueError with its message; a database that can't be reached or found
        raises StateUnavailable.
        """
        url = f"{self.uri}/v1/database/{self.database}/call/{reducer}"
        response = self._request("POST", url, json=args, headers=self._headers())
        if response.status_code == REDUCER_REFUSED:
            raise ValueError(response.text)
        if response.status_code == 404:
            raise StateUnavailable(f"no database {self.database!r} at {self.uri}")
        if response.status_code >= 400:
            raise ValueError(f"{reducer} rejected its arguments ({response.status_code}): {response.text}")
        try:
            self._deliver_changes()
        except StateUnavailable:
            # The write landed; only the follow-up poll failed. Raising would tell the caller it wasn't written. Its
            # changes come with the next poll, since no subscription's snapshot moved on.
            logger.warning("delivering %s's changes failed; the background poll will", reducer, exc_info=True)

    def _sql(self, query: str) -> list[dict]:
        """Rows of one SQL query, each as a dict by column name."""
        url = f"{self.uri}/v1/database/{self.database}/sql"
        response = self._request("POST", url, content=query, headers=self._headers())
        if response.status_code >= 400:
            raise StateUnavailable(f"SQL failed ({response.status_code}): {response.text}")
        try:
            result = response.json()[0]
            row_type = {"Product": result["schema"]}
            return [decode(row, row_type) for row in result["rows"]]
        except (ValueError, KeyError, IndexError, TypeError) as error:  # fail closed: an answer we can't read
            raise StateUnavailable(f"unreadable SQL answer for {query!r}: {error}") from error

    # ---------- shift start (not part of StateClient) ----------

    def start_shift(self) -> None:
        """Empty every table but the allergen list: each shift starts with an empty database (state-tables.md)."""
        self._call("start_shift", {})

    # ---------- writes ----------

    def put_ticket(self, ticket: Ticket) -> None:
        """Insert a ticket, or replace it with its next revision; the module refuses any other revision."""
        validate(ticket, "ticket")
        self._call("put_ticket", {field: ticket[field] for field in TICKET_FIELDS})

    def put_bin_profile(self, entity_id: str, profile: AllergenProfile) -> None:
        """Insert or replace a bin's allergen profile."""
        validate(profile, "allergen_profile")
        probabilities = [{"allergen_unit_id": unit, "probability": p} for unit, p in profile["probabilities"].items()]
        args = {"entity_id": entity_id, "probabilities": probabilities, "unknown": profile["unknown"]}
        self._call("put_bin_profile", {**args, "source": profile["source"]})

    def set_zones(self, zones: list[Zone]) -> None:
        """Replace the whole calibration with `zones`."""
        rows = [
            {"zone_id": zone.zone_id, "kind": zone.kind, "polygon": [{"x": x, "y": y} for x, y in zone.polygon]}
            for zone in zones
        ]
        self._call("set_zones", {"zones": rows})

    def bind_plate(self, plate_id: str, ticket_id: str, t_ms: int) -> None:
        """Bind a plate to a ticket. The same binding again is a no-op; a different one raises ValueError."""
        self._call("bind_plate", {"plate_id": plate_id, "ticket_id": ticket_id, "t_ms": t_ms})

    def register_entity(self, entity: Entity) -> None:
        """Register an entity once. Registering it again changes nothing, first_seen_t_ms and origin included."""
        validate(entity, "entity")
        self._call("register_entity", {field: entity[field] for field in IDENTITY_FIELDS})

    def move_entity(self, state: EntityState) -> None:
        """Append a state row for a registered entity (history is append-only)."""
        probabilities = [{"allergen_unit_id": unit, "probability": p} for unit, p in state.probabilities.items()]
        args = {
            "entity_id": state.entity_id,
            "version": state.version,
            "t_ms": state.t_ms,
            "zone": some(state.zone),
            "bbox": some(None if state.box is None else list(state.box)),
            "probabilities": probabilities,
            "unknown": state.unknown,
            "caused_by": some(state.caused_by),
        }
        self._call("move_entity", args)

    def record_interaction(self, event: InteractionEvent) -> None:
        """Insert an interaction edge. A duplicate event_id is a no-op."""
        validate(event, "interaction_event")
        args = {field: event[field] for field in EVENT_FIELDS}
        evidence = event.get("evidence")
        if evidence is not None:
            evidence = {field: some(evidence.get(field)) for field in ("frame_start", "frame_end", "clip_uri")}
        self._call("record_interaction", {**args, "evidence": some(evidence)})

    def record_verdict(self, verdict: Verdict) -> None:
        """Insert a verdict, chain included. A duplicate verdict_id is a no-op."""
        validate(verdict, "verdict")
        args = {field: verdict[field] for field in VERDICT_FIELDS}
        args["reasons"] = [
            {"code": r["code"], "allergen_unit_id": some(r.get("allergen_unit_id")), "detail": some(r.get("detail"))}
            for r in verdict["reasons"]
        ]
        versions = verdict["versions"]
        args["versions"] = {
            "policy": versions["policy"],
            "graph": versions["graph"],
            "classifier": some(versions.get("classifier")),
            "laya": some(versions.get("laya")),
        }
        self._call("record_verdict", args)

    # ---------- reads ----------

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        """The latest revision of a ticket, or None."""
        rows = self._sql(f"SELECT * FROM ticket WHERE ticket_id = {quoted(ticket_id)}")
        return rows[0] if rows else None

    def get_entity(self, entity_id: str) -> Entity | None:
        """The entity joined with its latest state, or None if it was never registered."""
        rows = self._sql(f"SELECT * FROM entity WHERE entity_id = {quoted(entity_id)}")
        if not rows:
            return None
        latest = self._sql(f"SELECT * FROM entity_latest WHERE entity_id = {quoted(entity_id)}")
        profiles = self._sql(f"SELECT * FROM bin_profile WHERE entity_id = {quoted(entity_id)}")
        bindings = self._sql(f"SELECT * FROM plate_binding WHERE plate_id = {quoted(entity_id)}")
        return self._joined(rows[0], first(latest), first(profiles), first(bindings))

    def list_entities(self, kind: str | None = None) -> list[Entity]:
        """Every registered entity joined with its latest state, optionally only one kind."""
        where = "" if kind is None else f" WHERE kind = {quoted(kind)}"
        rows = self._sql(f"SELECT * FROM entity{where}")
        if not rows:
            return []
        latest = {row["entity_id"]: row for row in self._sql("SELECT * FROM entity_latest")}
        profiles = {row["entity_id"]: row for row in self._sql("SELECT * FROM bin_profile")}
        bindings = {row["plate_id"]: row for row in self._sql("SELECT * FROM plate_binding")}
        return [
            self._joined(
                row, latest.get(row["entity_id"]), profiles.get(row["entity_id"]), bindings.get(row["entity_id"])
            )
            for row in sorted(rows, key=lambda row: (row["first_seen_t_ms"], row["entity_id"]))
        ]

    def list_zones(self) -> list[Zone]:
        """The current calibration, by zone_id."""
        rows = self._sql("SELECT * FROM zone")
        return [as_zone(row) for row in sorted(rows, key=lambda row: row["zone_id"])]

    def get_plate_binding(self, plate_id: str) -> PlateBinding | None:
        """The plate's binding, or None if it was never bound."""
        rows = self._sql(f"SELECT * FROM plate_binding WHERE plate_id = {quoted(plate_id)}")
        return PlateBinding(**rows[0]) if rows else None

    def list_interactions(self, since_t_ms: int = 0, until_t_ms: int | None = None) -> list[InteractionEvent]:
        """Interactions with since_t_ms <= t_start_ms <= until_t_ms, ordered by t_start_ms, t_end_ms, event_id."""
        query = f"SELECT * FROM interaction WHERE t_start_ms >= {int(since_t_ms)}"
        if until_t_ms is not None:
            query += f" AND t_start_ms <= {int(until_t_ms)}"
        return sorted((as_event(row) for row in self._sql(query)), key=interaction_order)

    def list_verdicts(self, ticket_id: str | None = None) -> list[Verdict]:
        """Every verdict, optionally only one ticket's, in the order decided (decided_t_ms, then verdict_id)."""
        where = "" if ticket_id is None else f" WHERE ticket_id = {quoted(ticket_id)}"
        rows = self._sql(f"SELECT * FROM verdict{where}")
        verdicts = [as_verdict(row) for row in rows]
        return sorted(verdicts, key=lambda verdict: (verdict["decided_t_ms"], verdict["verdict_id"]))

    # ---------- the logical Entity (contracts/state-tables.md) ----------

    def _joined(self, row: dict, latest: dict | None, bin_profile: dict | None, binding: dict | None) -> Entity:
        """The entity row joined with its entity_latest row, its allergens, and a plate's ticket.

        Zone, box and last seen come from the newest state. Allergens come from the newest state the graph wrote
        (one with caused_by set), else a bin's profile, else the entity's origin, so the entity store's zone updates
        can never overwrite what the graph found and a registration can never claim a clean history.
        """
        entity = {
            "entity_id": row["entity_id"],
            "kind": row["kind"],
            "label": row["label"],
            "zone": latest["zone"] if latest else None,
            "box": latest["bbox"] if latest else None,
            "track_id": None,
            "origin": row["origin"],
            "first_seen_t_ms": row["first_seen_t_ms"],
            "last_seen_t_ms": latest["t_ms"] if latest else row["first_seen_t_ms"],
            "ticket_id": binding["ticket_id"] if binding else None,
        }
        if latest is not None and latest["graph_t_ms"] is not None:
            entity["allergen_profile"] = self._graph_profile(latest)
        elif bin_profile is not None:
            entity["allergen_profile"] = as_profile(bin_profile)
        else:
            entity["allergen_profile"] = starting_profile(row["kind"], row["origin"])
        return entity

    def _graph_profile(self, latest: dict) -> AllergenProfile:
        """The graph's newest state as an allergen profile, its source named as contracts/state-tables.md says."""
        if latest["unknown"]:
            source = "unknown_history"
        else:
            events = self._sql(f"SELECT * FROM interaction WHERE event_id = {quoted(latest['caused_by'])}")
            source = "wash_reset" if events and events[0]["action"] == "clean.wash" else "propagated"
        probabilities = probabilities_of(latest["probabilities"])
        return {"probabilities": probabilities, "unknown": latest["unknown"], "source": source}

    # ---------- change feed ----------

    def subscribe(self, table: str, on_change: Callable[[ChangeEvent], None]) -> Callable[[], None]:
        """Call `on_change` for every insert or update to `table` from now on. Returns a function that unsubscribes.

        Each subscription takes its own snapshot of the table now; only what changes after it is delivered.
        """
        if table not in TABLES:
            raise ValueError(f"no table named {table!r}; tables are {TABLES}")
        subscription = Subscription(table, on_change)
        with self._lock:
            if table == "entity_state":
                rows = self._sql("SELECT * FROM entity_state")
                subscription.max_state_id = max((row["state_id"] for row in rows), default=-1)
            else:
                subscription.seen = self._rows_by_key(table)
            self._subscriptions.append(subscription)
        self._start_polling()

        def unsubscribe() -> None:
            with self._lock:
                if subscription in self._subscriptions:
                    self._subscriptions.remove(subscription)

        return unsubscribe

    def close(self) -> None:
        """Stop the background poll and close the HTTP connection pool."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2 * self.poll_s + 1)
        self._http.close()

    def _start_polling(self) -> None:
        """Start the background poll once, for changes written by other processes."""
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._poll_forever, daemon=True, name="spacetime-poll")
                self._thread.start()

    def _poll_forever(self) -> None:
        """One delivery round every poll_s until close(), releasing the lock in between so writes never wait long.

        An unreachable database is logged and tried again next time; nothing is lost, since a subscription's
        snapshot only moves on after a round it was delivered.
        """
        while not self._stop.wait(self.poll_s):
            try:
                self._deliver_changes(max_rounds=1)
            except StateUnavailable:
                logger.warning("change feed poll failed; retrying", exc_info=True)

    def _deliver_changes(self, max_rounds: int = WRITE_ROUNDS) -> None:
        """Deliver changes to subscribed tables, for at most `max_rounds` rounds or until a round finds none.

        One poll at a time. A callback that writes through this client doesn't poll again inside the callback; the
        next round here, or the background poll, picks its change up.
        """
        with self._lock:
            if self._polling or not self._subscriptions:
                return
            self._polling = True
            try:
                for _ in range(max_rounds):
                    if not self._deliver_one_round():
                        break
            finally:
                self._polling = False

    def _deliver_one_round(self) -> bool:
        """One pass over every subscribed table. Returns whether any subscription got a change."""
        changed = False
        for table in sorted({subscription.table for subscription in self._subscriptions}):
            subscriptions = [subscription for subscription in self._subscriptions if subscription.table == table]
            if table == "entity_state":
                changed |= self._deliver_new_states(subscriptions)
                continue
            current = self._rows_by_key(table)
            for subscription in subscriptions:
                changes = [
                    ChangeEvent(table, "update" if key in subscription.seen else "insert", as_change_row(table, row))
                    for key, row in current.items()
                    if subscription.seen.get(key) != row
                ]
                subscription.seen = current
                changed |= self._deliver(subscription, changes)
        return changed

    def _deliver_new_states(self, subscriptions: list["Subscription"]) -> bool:
        """entity_state is append-only, so only rows past each subscription's highest state_id are read.

        state_ids have gaps but only grow, since the module's reducers run one at a time; they are delivered in the
        order they were written, as FakeStateClient appends them.
        """
        lowest = min(subscription.max_state_id for subscription in subscriptions)
        where = "" if lowest < 0 else f" WHERE state_id > {lowest}"  # state_id is unsigned: no "> -1"
        rows = sorted(self._sql(f"SELECT * FROM entity_state{where}"), key=lambda row: row["state_id"])
        changed = False
        for subscription in subscriptions:
            new = [row for row in rows if row["state_id"] > subscription.max_state_id]
            if new:
                subscription.max_state_id = new[-1]["state_id"]
            changes = [ChangeEvent("entity_state", "insert", as_change_row("entity_state", row)) for row in new]
            changed |= self._deliver(subscription, changes)
        return changed

    def _deliver(self, subscription: "Subscription", changes: list[ChangeEvent]) -> bool:
        """Hand each change to the subscription's callback. Returns whether there were any."""
        for change in changes:
            if subscription not in self._subscriptions:
                break  # unsubscribed by an earlier callback
            try:
                subscription.on_change(change)
            except Exception:
                logger.exception("subscriber to %s failed on %s", change.table, change.kind)
        return bool(changes)

    def _rows_by_key(self, table: str) -> dict:
        """Every row of a keyed table by primary key."""
        return {row[KEYS[table]]: row for row in self._sql(f"SELECT * FROM {table}")}


@dataclass(eq=False)
class Subscription:
    """One subscribe() call: its table, its callback, and what it has seen so far (rows by primary key, or for
    entity_state the highest state_id delivered). Compared by identity, so unsubscribing removes exactly this one."""

    table: str
    on_change: Callable[[ChangeEvent], None]
    seen: dict = field(default_factory=dict)
    max_state_id: int = -1


def first(rows: list[dict]) -> dict | None:
    """The first row of a query by primary key, or None."""
    return rows[0] if rows else None


# ---------- rows as contract records ----------

TICKET_FIELDS = ("ticket_id", "revision", "number", "note", "source", "created_t_ms", "pass_threshold", "forbidden")
IDENTITY_FIELDS = ("entity_id", "kind", "label", "origin", "first_seen_t_ms")
EVENT_FIELDS = (
    "event_id",
    "t_start_ms",
    "t_end_ms",
    "a_id",
    "b_id",
    "action",
    "probability",
    "direction",
    "classifier",
)
VERDICT_FIELDS = (
    "verdict_id",
    "ticket_id",
    "ticket_revision",
    "plate_id",
    "decided_t_ms",
    "trigger",
    "decision",
    "confidence",
    "pass_threshold",
    "flagged",
    "chain",
)


def as_profile(row: dict) -> AllergenProfile:
    """A bin_profile row as an AllergenProfile."""
    return {"probabilities": probabilities_of(row["probabilities"]), "unknown": row["unknown"], "source": row["source"]}


def as_zone(row: dict) -> Zone:
    """A zone row as a Zone."""
    return Zone(
        zone_id=row["zone_id"], kind=row["kind"], polygon=[(point["x"], point["y"]) for point in row["polygon"]]
    )


def as_event(row: dict) -> InteractionEvent:
    """An interaction row as an InteractionEvent; evidence and its fields appear only when set."""
    event = {field: row[field] for field in EVENT_FIELDS}
    if row["evidence"] is not None:
        event["evidence"] = without_none(row["evidence"])
    return event


def as_verdict(row: dict) -> Verdict:
    """A verdict row as a Verdict; optional reason fields and versions appear only when set."""
    verdict = {field: row[field] for field in VERDICT_FIELDS}
    verdict["reasons"] = [without_none(reason) for reason in row["reasons"]]
    verdict["versions"] = without_none(row["versions"])
    return verdict


def as_change_row(table: str, row: dict):
    """A row as FakeStateClient puts it in a ChangeEvent: dataclasses for zone, plate_binding and entity_state, and
    the contract record (bin_profile with its entity_id) for the rest."""
    if table == "zone":
        return as_zone(row)
    if table == "plate_binding":
        return PlateBinding(**row)
    if table == "entity_state":
        return EntityState(
            entity_id=row["entity_id"],
            version=row["version"],
            t_ms=row["t_ms"],
            zone=row["zone"],
            box=row["bbox"],
            probabilities=probabilities_of(row["probabilities"]),
            unknown=row["unknown"],
            caused_by=row["caused_by"],
        )
    if table == "bin_profile":
        return {"entity_id": row["entity_id"], **as_profile(row)}
    if table == "interaction":
        return as_event(row)
    if table == "verdict":
        return as_verdict(row)
    return row
