"""Exact reachability: allergens reaching a plate with their chains, its subgraph for Laya, and its unknown ancestors.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: start profiles and time-ordered edges (graph.py); Carried (propagate.py)
Emits: allergen_reach records (ProvenanceGraph.reach), plate_subgraph records (subgraph), unknown ancestor IDs
Rules: time runs forward: chains are in time order and the subgraph holds only time-forward paths into the plate;
       known-clean source: every unwashed ancestor of unknown origin and unknown contents is reported
"""

from kitchen.graph.propagate import UNITS, Carried, flows, is_wash
from kitchen.shared.models import AllergenReach, Entity, InteractionEvent, PlateSubgraph


def reach_records(carried: Carried, plate_id: str) -> list[AllergenReach]:
    """Every allergen the plate may carry (probability above 0), with its strongest chain from a bin.

    An allergen that came only from unknown contents or wash residue has an empty chain: there is no source to name.
    unknown_ancestors() names the entities of unknown contents instead.
    """
    records = []
    for unit in UNITS:
        probability = carried.probabilities[plate_id][unit]
        if probability <= 0.0:
            continue
        _, steps = carried.chains[plate_id].get(unit, (0.0, []))
        records.append({"allergen_unit_id": unit, "probability": probability, "chain": chain_steps(steps, unit)})
    return records


def chain_steps(steps: list, unit: str) -> list[dict]:
    """A chain as ChainStep records, numbered from 1 at the bin's end."""
    return [
        {
            "step": number,
            "event_id": edge["event_id"],
            "from_id": from_id,
            "to_id": to_id,
            "t_ms": edge["t_start_ms"],
            "action": edge["action"],
            "allergen_unit_id": unit,
            "step_confidence": edge["probability"],
        }
        for number, (edge, from_id, to_id) in enumerate(steps, start=1)
    ]


def unknown_ancestors(carried: Carried, plate_id: str) -> list[str]:
    """The entities of unknown origin and unknown contents whose unwashed state reached the plate, sorted."""
    return sorted(carried.unknown_from[plate_id])


def on_path(edges: list[InteractionEvent], plate_id: str) -> list[InteractionEvent]:
    """The edges on some time-forward path into the plate, washes of entities on those paths included, in order.

    Walks the time-ordered edges backwards. `deadline[e]` is the position of e's last edge on a path to the plate, so
    an edge counts only if it came before that.
    """
    deadline = {plate_id: len(edges)}
    kept = []
    for position in range(len(edges) - 1, -1, -1):
        edge = edges[position]
        if edge["action"] == "clean.wash":
            if is_wash(edge) and position < deadline.get(edge["a_id"], -1):
                kept.append(edge)
            continue
        counts = False
        for src, dst in flows(edge):
            if position < deadline.get(dst, -1):
                deadline[src] = max(deadline.get(src, -1), position)
                counts = True
        if counts:
            kept.append(edge)
    return list(reversed(kept))


def subgraph_record(
    entities: dict[str, Entity], edges: list[InteractionEvent], plate_id: str, as_of_t_ms: int
) -> PlateSubgraph:
    """The plate's history for Laya: the edges on its time-forward paths and a node for each entity they touch."""
    kept = on_path(edges, plate_id)
    entity_ids = [plate_id] + [entity_id for edge in kept for entity_id in (edge["a_id"], edge["b_id"])]
    nodes = [
        {
            "entity_id": entity_id,
            "kind": entities[entity_id]["kind"],
            "label": entities[entity_id]["label"],
            "origin": entities[entity_id]["origin"],
            "start": entities[entity_id]["allergen_profile"],
        }
        for entity_id in dict.fromkeys(entity_ids)
    ]
    return {"plate_id": plate_id, "as_of_t_ms": as_of_t_ms, "nodes": nodes, "edges": kept}
