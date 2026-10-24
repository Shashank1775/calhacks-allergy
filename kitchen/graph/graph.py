"""Graph structure and the ProvenanceGraph itself: nodes and time-ordered edges, read from a StateClient per query.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: entity (with bin profiles joined in) and interaction_event, through any StateClient
Emits: ProvenanceGraph (reach, unknown_ancestors, subgraph, version), as contracts/apis.md promises
Rules: fail closed: any failure raises GraphUnavailable; time runs forward: edges sorted by t_start_ms, t_end_ms,
       washes first, event_id (OQ-17), and none after as_of; known-clean source: an entity's start comes from its
       origin, so only shift_start and clean_stack entities start clean, and an edge to an entity the database
       doesn't know treats it as unknown history

Every query reads the shift from the database again, so the answer always matches what's stored, with no cache to
go stale. A shift's few thousand edges take milliseconds.
"""

from kitchen.graph.propagate import EFFICIENCY, propagate
from kitchen.graph.reachability import reach_records, subgraph_record, unknown_ancestors
from kitchen.shared.interfaces import GraphUnavailable, StateClient
from kitchen.shared.models import AllergenReach, Entity, InteractionEvent, PlateSubgraph

VERSION = "1.0.0"

KINDS = ("bin", "tool", "hand", "plate")


def time_order(edge: InteractionEvent) -> tuple:
    """Sort key: t_start_ms, t_end_ms, then a wash before a transfer it ties with, then event_id (OQ-17)."""
    return (edge["t_start_ms"], edge["t_end_ms"], edge["action"] != "clean.wash", edge["event_id"])


def start_profile(entity: Entity) -> dict:
    """What the entity carried when it was first seen.

    Unknown origin is never clean. A bin starts with its profile (a bin with none is unknown, which the StateClient
    already reports). Tools, hands and plates from shift start or the clean stack start clean.
    """
    if entity["origin"] == "unknown" or entity["allergen_profile"]["unknown"]:
        return {"probabilities": {}, "unknown": True, "source": "unknown_history"}
    if entity["kind"] == "bin":
        return entity["allergen_profile"]
    return {"probabilities": {}, "unknown": False, "source": "shift_start_clean"}


def unknown_entity(entity_id: str) -> Entity:
    """An entity an edge names but the database doesn't hold. Nothing is known about it, so it may carry anything."""
    kind = entity_id.split("-", 1)[0]
    return {
        "entity_id": entity_id,
        "kind": kind if kind in KINDS else "tool",
        "label": entity_id,
        "origin": "unknown",
        "allergen_profile": {"probabilities": {}, "unknown": True, "source": "unknown_history"},
    }


def read_shift(state: StateClient, as_of_t_ms: int) -> tuple[dict[str, Entity], list[InteractionEvent]]:
    """The shift up to as_of_t_ms: every entity with its start profile, and the edges that matter, in time order."""
    entities = {}
    for entity in state.list_entities():
        node = {key: entity[key] for key in ("entity_id", "kind", "label", "origin")}
        node["allergen_profile"] = start_profile(entity)
        entities[entity["entity_id"]] = node

    edges = [edge for edge in state.list_interactions(0, as_of_t_ms) if edge["action"] != "unimportant"]
    for edge in edges:
        for entity_id in (edge["a_id"], edge["b_id"]):
            if entity_id not in entities:
                entities[entity_id] = unknown_entity(entity_id)
    return entities, sorted(edges, key=time_order)


class ProvenanceGraph:
    """The provenance graph over one StateClient: which allergens could have reached a plate, and how."""

    def __init__(self, state: StateClient) -> None:
        self._state = state

    def reach(self, plate_id: str, as_of_t_ms: int) -> list[AllergenReach]:
        """Every allergen with a chance of being on the plate at as_of_t_ms, each with its chain from a bin."""
        return self._query(
            plate_id, as_of_t_ms, lambda entities, edges: reach_records(propagate(entities, edges), plate_id)
        )

    def unknown_ancestors(self, plate_id: str, as_of_t_ms: int) -> list[str]:
        """The unknown-history entities whose unwashed state reached the plate by as_of_t_ms."""
        return self._query(
            plate_id, as_of_t_ms, lambda entities, edges: unknown_ancestors(propagate(entities, edges), plate_id)
        )

    def subgraph(self, plate_id: str, as_of_t_ms: int) -> PlateSubgraph:
        """The plate's time-forward history for Laya's plate check."""
        return self._query(
            plate_id, as_of_t_ms, lambda entities, edges: subgraph_record(entities, edges, plate_id, as_of_t_ms)
        )

    def version(self) -> str:
        """The graph's version and its efficiencies, recorded in every verdict's versions."""
        efficiencies = ",".join(f"{action.split('.')[1]}={k:g}" for action, k in EFFICIENCY.items())
        return f"kitchen-graph {VERSION} noisy-or k[{efficiencies}]"

    def _query(self, plate_id: str, as_of_t_ms: int, answer):
        """Read the shift and answer from it. Any failure, a plate the database doesn't know included, raises
        GraphUnavailable, which the verdict turns into HOLD."""
        try:
            entities, edges = read_shift(self._state, as_of_t_ms)
            if plate_id not in entities:
                raise GraphUnavailable(f"no entity {plate_id} in the database")
            return answer(entities, edges)
        except GraphUnavailable:
            raise
        except Exception as error:
            raise GraphUnavailable(f"provenance graph failed for {plate_id} at {as_of_t_ms}: {error}") from error
