"""Golden scenarios: read a contracts/scenarios file, load it into a StateClient, and step through its timeline.

Owner: platform (agents)
Step:  shared
Reads: contracts/scenarios/*.json (schema: scenario), or a scenario dict
Emits: GOLDEN_SCENARIO_PATHS, read_scenario, load_scenario, TimelineItem, scenario_timeline
Rules: known-clean source: entities a scenario only mentions are shift-start entities (clean tools, hands, plates),
       late entities keep their origin, and bins carry their profiles; late changes recheck everything: amendments
       are timeline items, not loaded up front, so a test can judge revision 1 before the amendment lands
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kitchen.shared.fakes.entity_store import starting_profile
from kitchen.shared.interfaces import StateClient
from kitchen.shared.models import CONTRACTS_DIR, Entity, Scenario, validate

GOLDEN_SCENARIO_PATHS = sorted((CONTRACTS_DIR / "scenarios").glob("*.json"))

# At equal t_ms an edge comes before an amendment, and both before a plate reaches the pass, so the plate is judged
# with everything that happened up to that moment (every judgment call leans toward HOLD).
KIND_ORDER = {"event": 0, "amendment": 1, "plate_at_pass": 2}


@dataclass(frozen=True)
class TimelineItem:
    """One thing that happens in a scenario: an interaction edge, a ticket amendment, or a plate reaching the pass."""

    t_ms: int
    kind: Literal["event", "amendment", "plate_at_pass"]
    record: dict  # the event, the amended ticket, or the plates_at_pass entry


def read_scenario(scenario: str | Path | Scenario) -> Scenario:
    """Return a scenario dict, read from a path or given directly, after checking it against the scenario schema."""
    if isinstance(scenario, str | Path):
        scenario = json.loads(Path(scenario).read_text())
    return validate(scenario, "scenario")


def load_scenario(state: StateClient, scenario: str | Path | Scenario) -> Scenario:
    """Write a scenario's starting world into `state` and return the scenario.

    That is every entity it mentions (bins with their profiles), its tickets, its plate bindings and all its
    interaction edges. Amendments and plates reaching the pass are left for the caller to play from
    scenario_timeline().
    """
    scenario = read_scenario(scenario)
    for entity in scenario_entities(scenario):
        state.register_entity(entity)
    for bin_entry in scenario["bins"]:
        state.put_bin_profile(bin_entry["entity_id"], bin_entry["allergen_profile"])
    for ticket in scenario["tickets"]:
        state.put_ticket(ticket)
    for binding in scenario.get("plate_bindings", []):
        state.bind_plate(binding["plate_id"], binding["ticket_id"], binding["t_ms"])
    for event in scenario["events"]:
        state.record_interaction(event)
    return scenario


def scenario_timeline(scenario: str | Path | Scenario) -> list[TimelineItem]:
    """Every event, amendment and plate-at-pass in the scenario, in time order (ties: event, amendment, plate)."""
    scenario = read_scenario(scenario)
    items = [TimelineItem(event["t_start_ms"], "event", event) for event in scenario["events"]]
    items += [TimelineItem(ticket["created_t_ms"], "amendment", ticket) for ticket in scenario.get("amendments", [])]
    items += [TimelineItem(plate["t_ms"], "plate_at_pass", plate) for plate in scenario["plates_at_pass"]]
    return sorted(items, key=lambda item: (item.t_ms, KIND_ORDER[item.kind]))


def scenario_entities(scenario: Scenario) -> list[Entity]:
    """An Entity for every entity the scenario mentions: bins, late entities, bound plates and both ends of each edge.

    Anything that isn't a bin or a late entity was there at shift start, so it starts clean (safety rule: known-clean
    source). Bins carry their scenario profile.
    """
    entities: dict[str, Entity] = {}
    for bin_entry in scenario["bins"]:
        entity = scenario_entity(bin_entry["entity_id"], "shift_start", 0)
        entity["label"] = bin_entry["label"]
        entity["allergen_profile"] = dict(bin_entry["allergen_profile"])
        entities[bin_entry["entity_id"]] = entity
    for late in scenario.get("late_entities", []):
        origin = late.get("origin", "unknown")
        entities[late["entity_id"]] = scenario_entity(late["entity_id"], origin, late["first_seen_t_ms"])

    mentioned = [binding["plate_id"] for binding in scenario.get("plate_bindings", [])]
    mentioned += [plate["plate_id"] for plate in scenario["plates_at_pass"]]
    for event in scenario["events"]:
        mentioned += [event["a_id"], event["b_id"]]
    for entity_id in mentioned:
        if entity_id not in entities:
            entities[entity_id] = scenario_entity(entity_id, "shift_start", 0)
    return list(entities.values())


def scenario_entity(entity_id: str, origin: str, first_seen_t_ms: int) -> Entity:
    """One scenario entity as an Entity dict. Its kind is the ID's prefix (bin-, tool-, hand-, plate-)."""
    kind = entity_id.split("-", 1)[0]
    return {
        "entity_id": entity_id,
        "kind": kind,
        "label": entity_id,
        "zone": None,
        "box": None,
        "track_id": None,
        "origin": origin,
        "first_seen_t_ms": first_seen_t_ms,
        "last_seen_t_ms": first_seen_t_ms,
        "ticket_id": None,
        "allergen_profile": starting_profile(kind, origin),
    }
