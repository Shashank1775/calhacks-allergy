"""In-memory entity store: builds and updates Entity rows from every ObservationFrame.

Owner: partner (written by an agent at the owner's request)
Step:  Entity database
Reads: observation_frame (validated on ingest); a Clock for last_sync_t_ms
Emits: Entity via get() and snapshot(); register_entity and move_entity calls through kitchen/entities/sync.py
Rules: 8 known-clean source: never deletes an entity, never changes first_seen_t_ms or origin, and an entity's starting
       allergen profile follows the origin vision gave its first sighting; 5 time runs forward: a frame older than the
       newest one ingested is refused; 1 fail closed: last_sync_t_ms moves only when a sync finishes
"""

import copy
import threading

from kitchen.entities.sync import EntitySync
from kitchen.shared.fakes.entity_store import starting_profile
from kitchen.shared.interfaces import Clock, StateClient
from kitchen.shared.models import Entity, ObservationFrame, validate


class EntityStore:
    """The Pi's EntityStore: every entity seen this shift, kept in a dict and synced to SpacetimeDB about 5 Hz.

    Vision calls ingest() from its frame loop while the sync thread reads snapshot(), so both take one lock. The lock
    only guards the dict: the sync does its database calls outside it, so a slow database never stalls a frame.
    """

    def __init__(self, clock: Clock) -> None:
        self._lock = threading.Lock()
        self._entities: dict[str, Entity] = {}
        self._newest_frame_t_ms: int | None = None
        self._sync = EntitySync(self.snapshot, clock)

    def ingest(self, frame: ObservationFrame) -> None:
        """Apply one frame: add entities seen for the first time, and move the ones already known.

        A malformed frame raises jsonschema.ValidationError and a frame older than the newest one raises ValueError;
        either way nothing changes.
        """
        validate(frame, "observation_frame")
        with self._lock:
            # Applying an older frame would move entities back in time, and the rows synced from them would put an
            # entity's history out of order (safety rule: time runs forward). One camera sends frames in order, so
            # an older frame is a bug upstream and fails loudly.
            if self._newest_frame_t_ms is not None and frame["t_ms"] < self._newest_frame_t_ms:
                raise ValueError(
                    f"frame at t_ms {frame['t_ms']} is older than the newest one, {self._newest_frame_t_ms}"
                )
            self._newest_frame_t_ms = frame["t_ms"]

            for detection in frame["detections"]:
                entity = self._entities.get(detection["entity_id"])
                if entity is None:
                    self._entities[detection["entity_id"]] = new_entity(detection, frame["t_ms"])
                else:
                    apply_detection(entity, detection, frame["t_ms"])

    def get(self, entity_id: str) -> Entity | None:
        """Return a copy of one entity, or None if it was never seen."""
        with self._lock:
            return copy.deepcopy(self._entities.get(entity_id))

    def snapshot(self) -> list[Entity]:
        """Return a copy of every entity seen this shift, in the order first seen. Nothing is ever removed."""
        with self._lock:
            return copy.deepcopy(list(self._entities.values()))

    def sync_once(self, state: StateClient) -> None:
        """Do one sync now, on the caller's thread (tests and replays use this instead of the background thread)."""
        self._sync.sync_once(state)

    def start_sync(self, state: StateClient, hz: float = 5.0) -> None:
        """Sync to `state` from a background thread, `hz` times a second, until stop_sync()."""
        self._sync.start(state, hz)

    def stop_sync(self) -> None:
        """Stop the background sync, if one is running, and wait for it to finish."""
        self._sync.stop()

    def last_sync_t_ms(self) -> int | None:
        """t_ms of the last sync that finished, or None before the first one. Health checks watch this go stale."""
        return self._sync.last_sync_t_ms()


def new_entity(detection: dict, t_ms: int) -> Entity:
    """The Entity for a detection seen for the first time.

    Its identity (kind, label, origin, first_seen_t_ms) is fixed from here on. Vision gives a re-acquired track a new
    entity_id with origin unknown, so a tool that leaves and comes back is a new entity here, never the old clean one.
    """
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
        "allergen_profile": starting_profile(detection["kind"], detection["origin"]),
    }


def apply_detection(entity: Entity, detection: dict, t_ms: int) -> None:
    """Update a known entity from a later detection: where it is and when it was last seen, nothing else.

    A later detection's origin is ignored, so a frame that says shift_start can't make an unknown-history tool clean
    (safety rule: known-clean source).
    """
    entity["zone"] = detection.get("zone")
    entity["box"] = list(detection["box"])
    entity["track_id"] = detection["track_id"]
    entity["last_seen_t_ms"] = t_ms
