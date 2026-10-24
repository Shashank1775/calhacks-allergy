"""Pushes entity snapshots to SpacetimeDB about 5 times a second without blocking the frame loop.

Owner: partner (written by an agent at the owner's request)
Step:  Entity database
Reads: the store's snapshot(); every entity's latest allergens from StateClient.list_entities; a Clock
Emits: StateClient.register_entity -> entity table (once per entity); move_entity -> entity_state (zone, box)
Rules: 1 fail closed: a failed sync never advances last_sync_t_ms, so health checks see the outage as staleness, and it
       is retried next tick; 8 never deletes, and registers each entity once with the origin it was first seen with;
       a zone and box row copies the entity's latest allergens forward and never overwrites the graph's
       (contracts/state-tables.md, Who fills which entity_state fields)
"""

import logging
import threading
from collections.abc import Callable

from kitchen.shared.interfaces import Clock, StateClient, StateUnavailable
from kitchen.shared.models import AllergenProfile, Entity, EntityState

logger = logging.getLogger(__name__)


class EntitySync:
    """Sends an entity store's snapshot to a StateClient: each entity registered once, then a state row per move.

    `snapshot` is the store's snapshot() method. sync_once() does one sync on the caller's thread; start() calls it
    from a background thread `hz` times a second.
    """

    def __init__(self, snapshot: Callable[[], list[Entity]], clock: Clock) -> None:
        self._snapshot = snapshot
        self._clock = clock
        self._registered: set[str] = set()  # entity IDs already sent with register_entity
        self._synced_t_ms: dict[str, int] = {}  # last_seen_t_ms of the last state row sent per entity
        self._versions: dict[str, int] = {}  # state rows sent per entity
        self._last_sync_t_ms: int | None = None
        self._failing = False  # whether the last tick failed, so an outage logs once, not 5 times a second
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sync_once(self, state: StateClient) -> None:
        """Register new entities and send a state row for each one that moved since the last sync.

        Any error stops the sync part-way and propagates, leaving last_sync_t_ms where it was. What was sent stays
        sent; the rest goes on the next sync.
        """
        moved = []
        for entity in self._snapshot():
            entity_id = entity["entity_id"]
            if entity_id not in self._registered:
                state.register_entity(entity)
                self._registered.add(entity_id)
            if self._synced_t_ms.get(entity_id) != entity["last_seen_t_ms"]:
                moved.append(entity)
        if moved:
            # One read gives every entity's latest allergens. A real StateClient answers each read with several
            # queries, so one per moved entity, 5 times a second, would load the database for nothing.
            joined = {row["entity_id"]: row for row in state.list_entities()}
            for entity in moved:
                self._send_state_row(state, entity, joined.get(entity["entity_id"]))
        self._last_sync_t_ms = self._clock.now_ms()

    def start(self, state: StateClient, hz: float) -> None:
        """Sync to `state` from a background thread, `hz` times a second, until stop()."""
        if hz <= 0:
            raise ValueError(f"sync rate must be positive, got {hz} Hz")
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._sync_loop, args=(state, 1.0 / hz), name="entity-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background sync, if one is running, and wait for its current tick to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def last_sync_t_ms(self) -> int | None:
        """t_ms of the last sync that finished, or None before the first one."""
        return self._last_sync_t_ms

    def _sync_loop(self, state: StateClient, period_s: float) -> None:
        """The background thread: sync, wait one period, repeat until stopped. A failed tick is retried next tick."""
        while not self._stop.is_set():
            try:
                self.sync_once(state)
                if self._failing:
                    logger.info("entity sync recovered at t_ms %s", self._last_sync_t_ms)
                self._failing = False
            except StateUnavailable:
                # The gate's health watch sees the outage as a stale last_sync_t_ms (fail closed); log it once.
                if not self._failing:
                    logger.warning("database unreachable, entity sync retrying (last sync %s)", self._last_sync_t_ms)
                self._failing = True
            except Exception:
                # Anything else is a bug, so it logs every time. The thread keeps going so a passing fault recovers;
                # until then last_sync_t_ms stays stale, as for an outage.
                logger.exception("entity sync failed, retrying next tick")
                self._failing = True
            self._stop.wait(period_s)

    def _send_state_row(self, state: StateClient, entity: Entity, joined: Entity | None) -> None:
        """Append one entity_state row with the entity's current zone and box.

        The entity store owns only zone and box. The graph owns the allergens, so the row copies them forward from
        the entity's latest state in the database (or, for a bin the graph hasn't touched, its bin profile), and a
        zone update never wipes what the graph found. caused_by stays empty: no interaction caused this row.
        `joined` is the database's view of the entity, or None if it isn't there yet.
        """
        entity_id = entity["entity_id"]
        allergens = latest_allergens(entity, joined)
        version = self._versions.get(entity_id, 0) + 1
        state.move_entity(
            EntityState(
                entity_id=entity_id,
                version=version,
                t_ms=entity["last_seen_t_ms"],
                zone=entity["zone"],
                box=entity["box"],
                probabilities=dict(allergens["probabilities"]),
                unknown=allergens["unknown"],
                caused_by=None,
            )
        )
        # Counted only once the row landed, so a failed write is resent with the same version.
        self._versions[entity_id] = version
        self._synced_t_ms[entity_id] = entity["last_seen_t_ms"]


def latest_allergens(entity: Entity, joined: Entity | None) -> AllergenProfile:
    """What the database says the entity may carry now: its joined allergen profile (contracts/state-tables.md).

    An entity the database doesn't have yet falls back to the starting profile the store gave it, which is clean
    only for a tool, hand or plate first seen with a known-clean origin (safety rule: known-clean source).
    """
    if joined is None:
        return entity["allergen_profile"]
    return joined["allergen_profile"]
