"""Live per-entity allergen risk, written as entity_state rows for the camera overlays.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24); the partner reviews it.
Step:  provenance graph
Reads: the shift (entities and interaction edges) from a StateClient through graph.read_shift, propagated by
       propagate.py; a Clock; the interaction table's change feed (only to learn that something landed)
Emits: entity_state rows (probabilities, unknown, caused_by) via StateClient.move_entity
Rules: known-clean source: an unknown-history entity shows unknown: true until a wash, and a wash is the only thing
       that lowers a shown number; fail closed: an error writes nothing, so a failed update leaves the screen as it
       was and never shows a clean entity, and a row is never rounded down to zero; the verdict is untouched: it asks
       ProvenanceGraph directly, and no row here can change what the graph computes (see below)

The camera overlays color each box from its latest entity_state row. Nothing else writes allergens into those rows
for a tool, hand or plate, so without this a knife that just scooped satay would show clean. LiveRisk runs
propagate() over the whole shift whenever an edge has landed and appends one row for each entity whose shown risk
changed. The entity store's next row for that entity copies the allergens forward (contracts/state-tables.md), so the
screens need no change.

A risk row copies zone, box and t_ms from the entity's newest row, so it moves nothing: last_seen_t_ms stays when the
camera last saw the entity, and a plate at the pass stays where it is and is not judged again. Its version counts the
risk rows written for that entity. It differs from the entity store's counter on purpose: the two writers never need
to agree, because SpacetimeDB keeps zone and box from the newest (t_ms, version) row and the allergens from the newest
row with caused_by set, and every risk row has the same zone and box as the row before it.

Bins get no rows. The graph starts a bin from the allergen profile the database joins to it (graph.start_profile), so a
row here would become the bin's starting profile and the graph would count the contact twice on its next query. Tools,
hands and plates start clean (or unknown) from their origin alone, so their rows are never read back as a start. The
unknown flag follows the same rule: it is true only for an entity whose origin is unknown, never for one that merely
touched such an entity, since the graph would read the flag back as an unknown start.
"""

import logging
import threading

from kitchen.graph.graph import read_shift
from kitchen.graph.propagate import Carried, propagate
from kitchen.shared.interfaces import Clock, StateClient, StateUnavailable
from kitchen.shared.models import Entity, EntityState, InteractionEvent

logger = logging.getLogger(__name__)

# Probabilities are shown to this many decimals: the screens don't need more, and a short decimal reads back from the
# database exactly, so an unchanged entity compares equal and isn't written again.
DECIMALS = 3
SMALLEST_SHOWN = 10**-DECIMALS

# What an overlay shows for an entity: its nonzero probabilities, and whether it is unwashed unknown history.
Shown = tuple[dict[str, float], bool]


def shown_probability(probability: float) -> float:
    """The probability rounded for the screens. A real risk is never rounded down to 0, which would show clean."""
    if probability <= 0.0:
        return 0.0
    return max(round(probability, DECIMALS), SMALLEST_SHOWN)


def shown(entity: Entity, carried: Carried) -> Shown:
    """What the overlay shows for one entity, given what propagate() says every entity carries."""
    entity_id = entity["entity_id"]
    probabilities = {
        unit: shown_probability(probability)
        for unit, probability in carried.probabilities[entity_id].items()
        if probability > 0.0
    }
    unknown = entity["origin"] == "unknown" and entity_id in carried.unknown_from[entity_id]
    return probabilities, unknown


def latest_edge_per_entity(edges: list[InteractionEvent]) -> dict[str, str]:
    """The event_id of the last edge, in time order, that touches each entity. Only entities with an edge are here."""
    latest = {}
    for edge in edges:
        latest[edge["a_id"]] = edge["event_id"]
        latest[edge["b_id"]] = edge["event_id"]
    return latest


class LiveRisk:
    """Keeps each tool's, hand's and plate's latest entity_state allergens equal to what the graph computes.

    update_once() does one update on the caller's thread. start() runs it from a background thread, at most `hz` times
    a second and only after an interaction edge has landed.
    """

    def __init__(self, state: StateClient, clock: Clock) -> None:
        self._state = state
        self._clock = clock
        self._written: dict[str, Shown] = {}  # what each entity last showed, once its row landed
        self._versions: dict[str, int] = {}  # risk rows written per entity
        self._lock = threading.Lock()  # one update at a time
        self._edge_landed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsubscribe = None
        self._failing = False  # whether the last tick failed, so an outage logs once, not 5 times a second

    def start(self, hz: float = 5.0) -> None:
        """Update from a background thread, up to `hz` times a second, whenever an edge has landed, until stop().

        Edges already in the database get their first update right away. The subscription only sets a flag: its
        callback runs on whichever thread wrote the edge, and that is the camera's frame loop.
        """
        if hz <= 0:
            raise ValueError(f"risk update rate must be positive, got {hz} Hz")
        self.stop()
        self._stop.clear()
        self._unsubscribe = self._state.subscribe("interaction", self._on_interaction)
        self._edge_landed.set()
        self._thread = threading.Thread(target=self._run, args=(1.0 / hz,), name="live-risk", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background updates, if they are running, and wait for the current one to finish."""
        self._stop.set()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def update_once(self) -> bool:
        """Append a row for each entity whose shown risk differs from what was last written for it.

        Returns True when every entity shows what the graph says, and False when one wasn't registered yet (the entity
        store registers it within a tick), so the next update must try again. Any error propagates with nothing
        guessed: rows already written stay, and the rest are written on the next update.
        """
        with self._lock:
            entities, edges = read_shift(self._state, self._clock.now_ms())
            carried = propagate(entities, edges)
            # What an entity no edge touches shows. A first row is written only if it shows something else.
            at_start = propagate(entities, [])
            up_to_date = True
            for entity_id, caused_by in latest_edge_per_entity(edges).items():
                entity = entities[entity_id]
                if entity["kind"] == "bin":  # see the module docstring
                    continue
                now_shown = shown(entity, carried)
                if now_shown == self._written.get(entity_id, shown(entity, at_start)):
                    continue
                if not self._write_row(entity_id, now_shown, caused_by):
                    up_to_date = False
            return up_to_date

    def _write_row(self, entity_id: str, now_shown: Shown, caused_by: str) -> bool:
        """Append one entity_state row with the new allergens and the entity's zone, box and t_ms as they are now.

        The entity is read again just before the write, so the copy is as fresh as it can be. Returns False when the
        database doesn't hold the entity yet.
        """
        entity = self._state.get_entity(entity_id)
        if entity is None:
            logger.debug("live risk: %s isn't registered yet, trying again next update", entity_id)
            return False
        probabilities, unknown = now_shown
        version = self._versions.get(entity_id, 0) + 1
        self._state.move_entity(
            EntityState(
                entity_id=entity_id,
                version=version,
                t_ms=entity["last_seen_t_ms"],
                zone=entity["zone"],
                box=entity["box"],
                probabilities=dict(probabilities),
                unknown=unknown,
                caused_by=caused_by,
            )
        )
        # Counted only once the row landed, so a failed write is tried again with the same version.
        self._versions[entity_id] = version
        self._written[entity_id] = now_shown
        logger.info(
            "live risk: %s shows %s%s (caused by %s)",
            entity_id,
            probabilities,
            " unknown" if unknown else "",
            caused_by,
        )
        return True

    def _on_interaction(self, change) -> None:
        """Note that an edge landed. An unimportant edge carries nothing, so it changes no risk."""
        if change.row["action"] != "unimportant":
            self._edge_landed.set()

    def _run(self, period_s: float) -> None:
        """The background thread: update when an edge has landed, wait one period, repeat until stopped."""
        while not self._stop.is_set():
            if self._edge_landed.is_set():
                self._edge_landed.clear()  # before the update, so an edge that lands during it brings another one
                self._update_or_retry()
            self._stop.wait(period_s)

    def _update_or_retry(self) -> None:
        """One update from the background thread. Whatever goes wrong is logged and tried again next tick."""
        try:
            if not self.update_once():
                self._edge_landed.set()
            if self._failing:
                logger.info("live risk recovered")
            self._failing = False
        except StateUnavailable:
            if not self._failing:
                logger.warning("database unreachable, live risk retrying")
            self._failing = True
            self._edge_landed.set()
        except Exception:
            # A bug, so it logs every time. The thread keeps going so a passing fault recovers; until then the screens
            # keep the last numbers they showed (the update writes nothing it isn't sure of).
            logger.exception("live risk update failed, retrying next tick")
            self._failing = True
            self._edge_landed.set()
