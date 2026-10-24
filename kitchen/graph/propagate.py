"""Noisy-OR propagation along transfer edges in time order; washes reset.

Owner: partner (human). First version written by an agent at the owner's request (2026-10-24).
Step:  provenance graph
Reads: start profiles and time-ordered edges (graph.py); contracts/allergen_units.json
Emits: Carried: per entity, each allergen's probability, the strongest chain from a bin, and the unknown-history
       entities whose unwashed state flowed into it
Rules: time runs forward: edges are applied one at a time in time order, washes first on a tie (OQ-17);
       known-clean source: an unknown-history entity carries every allergen at probability 1 (OQ-13) until a wash;
       a wash is a self-loop edge and only ever lowers a probability

Math (AGENTS.md, Graph math), per transfer edge src -> dst with edge probability p and action efficiency k:
    P_dst(a) <- 1 - (1 - P_dst(a)) * (1 - k * p * P_src(a))
and per wash of x with probability p:
    P_x(a) <- (1 - p) * P_x(a)
"""

import json
from dataclasses import dataclass, field

from kitchen.shared.models import CONTRACTS_DIR, Entity, InteractionEvent

UNITS = [unit["id"] for unit in json.loads((CONTRACTS_DIR / "allergen_units.json").read_text())["units"]]

# k per transfer action. 1 everywhere: the most conservative choice, and the one Laya's exact plate check uses
# (kitchen/verdict/laya_check.py), so the graph and the plate check agree. The golden scenarios hold for any k in (0, 1].
EFFICIENCY = {"transfer.contact": 1.0, "transfer.scoop": 1.0, "transfer.wipe": 1.0}


@dataclass
class Carried:
    """What each entity carries after the edges so far.

    probabilities: entity -> allergen -> P(carries it).
    chains: entity -> allergen -> (strength, steps), the most likely single path from a bin that holds the allergen.
        A step is (edge, from_id, to_id). A wash ends the washed entity's chains: what survives a wash is residue,
        and residue, like unknown history, has no source to point at. Its risk still counts in `probabilities`.
    unknown_from: entity -> the unknown-history entities whose unwashed state reached it.
    """

    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    chains: dict[str, dict[str, tuple[float, list]]] = field(default_factory=dict)
    unknown_from: dict[str, set[str]] = field(default_factory=dict)


def flows(edge: InteractionEvent) -> list[tuple[str, str]]:
    """The (source, destination) pairs a transfer edge moves allergens along."""
    a, b = edge["a_id"], edge["b_id"]
    return {"a_to_b": [(a, b)], "b_to_a": [(b, a)], "both": [(a, b), (b, a)]}[edge["direction"]]


def is_wash(edge: InteractionEvent) -> bool:
    """A wash is a clean.wash self-loop. Anything else with that action is malformed and must not lower risk."""
    return edge["action"] == "clean.wash" and edge["a_id"] == edge["b_id"]


def start(entities: dict[str, Entity]) -> Carried:
    """Each entity as the shift starts it: unknown history carries everything, a bin its profile, the rest nothing."""
    carried = Carried()
    for entity_id, entity in entities.items():
        profile = entity["allergen_profile"]
        if profile["unknown"]:
            carried.probabilities[entity_id] = {unit: 1.0 for unit in UNITS}
            carried.chains[entity_id] = {}
            carried.unknown_from[entity_id] = {entity_id} if entity["origin"] == "unknown" else set()
            continue
        known = {unit: profile["probabilities"].get(unit, 0.0) for unit in UNITS}
        carried.probabilities[entity_id] = known
        carried.chains[entity_id] = {unit: (p, []) for unit, p in known.items() if p > 0.0}
        carried.unknown_from[entity_id] = set()
    return carried


def wash(carried: Carried, edge: InteractionEvent) -> None:
    """A wash keeps (1 - p) of everything the entity carried, and resets its chains and unknown history."""
    entity_id, p = edge["a_id"], edge["probability"]
    carried.probabilities[entity_id] = {unit: (1.0 - p) * q for unit, q in carried.probabilities[entity_id].items()}
    carried.chains[entity_id] = {}
    carried.unknown_from[entity_id] = set()


def transfer(carried: Carried, edge: InteractionEvent) -> None:
    """Noisy-OR along each direction of the edge. A "both" edge moves both ways at once, from the state before it."""
    k = EFFICIENCY[edge["action"]]  # a KeyError on an unknown action fails the query closed
    p = edge["probability"]
    pairs = flows(edge)
    before = {entity_id: dict(carried.probabilities[entity_id]) for pair in pairs for entity_id in pair}
    chains_before = {entity_id: dict(carried.chains[entity_id]) for pair in pairs for entity_id in pair}
    unknown_before = {entity_id: set(carried.unknown_from[entity_id]) for pair in pairs for entity_id in pair}

    for src, dst in pairs:
        if src == dst:
            continue
        for unit in UNITS:
            moved = k * p * before[src][unit]
            carried.probabilities[dst][unit] = 1.0 - (1.0 - carried.probabilities[dst][unit]) * (1.0 - moved)
        for unit, (strength, steps) in chains_before[src].items():
            candidate = k * p * strength
            if candidate > carried.chains[dst].get(unit, (0.0, []))[0]:
                carried.chains[dst][unit] = (candidate, steps + [(edge, src, dst)])
        carried.unknown_from[dst] |= unknown_before[src]


def propagate(entities: dict[str, Entity], edges: list[InteractionEvent]) -> Carried:
    """Apply the time-ordered edges to the shift's start state and return what every entity carries afterwards."""
    carried = start(entities)
    for edge in edges:
        if edge["action"] == "clean.wash":
            if is_wash(edge):
                wash(carried, edge)
        else:
            transfer(carried, edge)
    return carried
