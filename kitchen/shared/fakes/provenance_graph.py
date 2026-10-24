"""FakeProvenanceGraph: stands in for the partner's kitchen/graph by answering from a scenario's ground truth.

Owner: platform (agents)
Step:  shared
Reads: one golden scenario (expected[].allergens_reached and chain_event_ids, events, late_entities)
Emits: AllergenReach lists, unknown-ancestor IDs and a version string, as ProvenanceGraph promises
Rules: no allergen propagation math (the partner owns the graph); every ground-truth allergen is reported with
       probability 1, the fail-closed value; a plate the scenario doesn't cover raises GraphUnavailable rather than
       look clean (fail closed); chains run forward in time (time runs forward); set_unavailable() makes every
       query raise GraphUnavailable
"""

from pathlib import Path

from kitchen.shared.fakes.scenarios import read_scenario
from kitchen.shared.interfaces import GraphUnavailable
from kitchen.shared.models import AllergenReach, ChainStep, Scenario


class FakeProvenanceGraph:
    """A ProvenanceGraph for one scenario: reach() and unknown_ancestors() read the scenario's expected answers.

    It never propagates allergens, so a test against it checks the code that consumes the graph, not the graph.
    """

    def __init__(self, scenario: str | Path | Scenario) -> None:
        self._scenario = read_scenario(scenario)
        self._events = {event["event_id"]: event for event in self._scenario["events"]}
        self._unavailable = False

    def set_unavailable(self, unavailable: bool = True) -> None:
        """Make reach() and unknown_ancestors() raise GraphUnavailable until called again with False."""
        self._unavailable = unavailable

    def reach(self, plate_id: str, as_of_t_ms: int) -> list[AllergenReach]:
        """Every allergen the scenario says reached the plate by `as_of_t_ms`, each with its chain.

        An allergen whose chain isn't complete by `as_of_t_ms` hasn't reached the plate yet, so it is left out.
        """
        expected = self._expected_for(plate_id)
        chain_event_ids = next((entry["chain_event_ids"] for entry in expected if entry.get("chain_event_ids")), [])
        allergens = sorted({allergen for entry in expected for allergen in entry["allergens_reached"]})
        reached = []
        for allergen in allergens:
            chain = self._chain(plate_id, chain_event_ids, allergen)
            if all(step["t_ms"] <= as_of_t_ms for step in chain):
                reached.append({"allergen_unit_id": allergen, "probability": 1.0, "chain": chain})
        return reached

    def unknown_ancestors(self, plate_id: str, as_of_t_ms: int) -> list[str]:
        """The scenario's unknown-history late entities seen by `as_of_t_ms`, if it expects an unknown_history hold."""
        expected = self._expected_for(plate_id)
        if not any("unknown_history" in entry.get("reason_codes", []) for entry in expected):
            return []
        return [
            late["entity_id"]
            for late in self._scenario.get("late_entities", [])
            if late.get("origin", "unknown") == "unknown" and late["first_seen_t_ms"] <= as_of_t_ms
        ]

    def version(self) -> str:
        """Names the fake and the scenario it answers from, so a verdict's versions show it wasn't the real graph."""
        return f"fake-ground-truth:{self._scenario['scenario_id']}"

    def _expected_for(self, plate_id: str) -> list[dict]:
        """The scenario's expected entries for one plate. No entry means no ground truth: fail closed."""
        if self._unavailable:
            raise GraphUnavailable("graph unavailable (FakeProvenanceGraph.set_unavailable)")
        expected = [entry for entry in self._scenario["expected"] if entry["plate_id"] == plate_id]
        if not expected:
            raise GraphUnavailable(f"scenario {self._scenario['scenario_id']} has no ground truth for {plate_id}")
        return expected

    def _chain(self, plate_id: str, event_ids: list[str], allergen: str) -> list[ChainStep]:
        """Turn the scenario's chain_event_ids into ChainSteps, oriented from the allergen's source to the plate.

        It walks back from the plate: each edge's far end is where the allergen came from on that hop.
        """
        steps = []
        to_id = plate_id
        for event_id in reversed(event_ids):
            event = self._events[event_id]
            from_id = event["a_id"] if event["b_id"] == to_id else event["b_id"]
            steps.append(
                {
                    "event_id": event_id,
                    "from_id": from_id,
                    "to_id": to_id,
                    "t_ms": event["t_start_ms"],
                    "action": event["action"],
                    "allergen_unit_id": allergen,
                    "step_confidence": event["probability"],
                }
            )
            to_id = from_id
        steps.reverse()
        return [{"step": number, **step} for number, step in enumerate(steps, start=1)]
