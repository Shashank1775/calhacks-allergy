"""FakeEntityStore: stands in for the partner's kitchen/entities, applying ObservationFrames to a dict.

Owner: platform (agents)
Step:  shared
Reads: ObservationFrames (validated against contracts/schemas on ingest); a Clock for last_sync_t_ms
Emits: Entity dicts through get() and snapshot(); register_entity and move_entity calls to the StateClient it syncs to
Rules: known-clean source: never deletes an entity and never changes first_seen_t_ms or origin; a tool, hand or plate
       starts clean whatever its origin, and a bin starts unknown until its bin profile joins in; a failed sync
       leaves last_sync_t_ms stale so health checks see the outage (fail closed)
"""

import contextlib
import copy
import threading

from kitchen.shared.interfaces import Clock, StateClient, StateUnavailable
from kitchen.shared.models import AllergenProfile, Entity, EntityState, ObservationFrame, validate


def starting_profile(kind: str) -> AllergenProfile:
    """The allergen profile an entity has when first seen, before the graph or a bin profile says more.

    Tools, hands and plates start clean whatever their origin, unknown included: only an edge in the graph can give
    one risk, so a plate holds on evidence, never on an unknown origin alone (safety rule: known-clean source). The
    origin is still recorded and never changes. Bins are unknown here: what a bin holds comes from its bin profile,
    and nothing here knows it.
    """
    if kind != "bin":
        return {"probabilities": {}, "unknown": False, "source": "shift_start_clean"}
    return {"probabilities": {}, "unknown": True, "source": "unknown_history"}


class FakeEntityStore:
    """An EntityStore that keeps every entity seen this shift in a dict and syncs it to any StateClient.

    sync_once() does one sync synchronously, so tests can sync without a thread; start_sync() calls it from a
    background thread `hz` times a second.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entities: dict[str, Entity] = {}
        self._registered: set[str] = set()  # entity IDs already sent with register_entity
        self._synced_t_ms: dict[str, int] = {}  # last_seen_t_ms of the last state sent per entity
        self._versions: dict[str, int] = {}  # state rows sent per entity
        self._last_sync_t_ms: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def ingest(self, frame: ObservationFrame) -> None:
        """Apply one frame: add entities seen for the first time, and move the ones already known."""
        validate(frame, "observation_frame")
        with self._lock:
            for detection in frame["detections"]:
                entity = self._entities.get(detection["entity_id"])
                if entity is None:
                    self._entities[detection["entity_id"]] = new_entity(detection, frame["t_ms"])
                    continue
                # Only where it is changes. first_seen_t_ms and origin stay as first seen, whatever later frames
                # say (safety rule: known-clean source).
                entity["zone"] = detection.get("zone")
                entity["box"] = list(detection["box"])
                entity["track_id"] = detection["track_id"]
                entity["last_seen_t_ms"] = frame["t_ms"]

    def get(self, entity_id: str) -> Entity | None:
        """Return a copy of one entity, or None if it was never seen."""
        with self._lock:
            return copy.deepcopy(self._entities.get(entity_id))

    def snapshot(self) -> list[Entity]:
        """Return a copy of every entity seen this shift, in the order first seen. Nothing is ever removed."""
        with self._lock:
            return copy.deepcopy(list(self._entities.values()))

    def sync_once(self, state: StateClient) -> None:
        """Register new entities and send a state row for each one that moved since the last sync.

        A StateUnavailable stops the sync part-way and propagates; whatever wasn't sent goes on the next sync.
        """
        for entity in self.snapshot():
            entity_id = entity["entity_id"]
            if entity_id not in self._registered:
                state.register_entity(entity)
                self._registered.add(entity_id)
            if self._synced_t_ms.get(entity_id) != entity["last_seen_t_ms"]:
                state.move_entity(self._state_row(entity))
                self._synced_t_ms[entity_id] = entity["last_seen_t_ms"]
        self._last_sync_t_ms = self._clock.now_ms()

    def start_sync(self, state: StateClient, hz: float = 5.0) -> None:
        """Sync to `state` from a background thread, `hz` times a second, until stop_sync()."""
        self.stop_sync()
        self._stop.clear()
        self._thread = threading.Thread(target=self._sync_loop, args=(state, 1.0 / hz), daemon=True)
        self._thread.start()

    def stop_sync(self) -> None:
        """Stop the background sync, if one is running, and wait for it to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def last_sync_t_ms(self) -> int | None:
        """t_ms of the last sync that finished, or None before the first one."""
        return self._last_sync_t_ms

    def _sync_loop(self, state: StateClient, period_s: float) -> None:
        """The background thread: sync, wait one period, repeat until stopped."""
        while not self._stop.is_set():
            # An unreachable database just skips this tick: last_sync_t_ms goes stale, which is what health checks
            # watch (fail closed). Any other error ends the thread, which also leaves last_sync_t_ms stale.
            with contextlib.suppress(StateUnavailable):
                self.sync_once(state)
            self._stop.wait(period_s)

    def _state_row(self, entity: Entity) -> EntityState:
        """The entity_state row for an entity's current zone and box. caused_by stays empty: no interaction caused it."""
        version = self._versions.get(entity["entity_id"], 0) + 1
        self._versions[entity["entity_id"]] = version
        return EntityState(
            entity_id=entity["entity_id"],
            version=version,
            t_ms=entity["last_seen_t_ms"],
            zone=entity["zone"],
            box=entity["box"],
            probabilities=dict(entity["allergen_profile"]["probabilities"]),
            unknown=entity["allergen_profile"]["unknown"],
        )


def new_entity(detection: dict, t_ms: int) -> Entity:
    """The Entity for a detection seen for the first time: its origin, and first_seen_t_ms, are fixed from here on."""
    return {
        "entity_id": detection["entity_id"],
        "kind": detection["kind"],
        "label": detection["class_name"],
        "zone": detection.get("zone"),
        "box": list(detection["box"]),
        "track_id": detection["track_id"],
        "origin": detection["origin"],
        "first_seen_t_ms": t_ms,
        "last_seen_t_ms": t_ms,
        "ticket_id": None,
        "allergen_profile": starting_profile(detection["kind"]),
    }
