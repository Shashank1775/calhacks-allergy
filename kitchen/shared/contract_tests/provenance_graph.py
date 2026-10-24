"""Contract suite for ProvenanceGraph: every golden scenario in contracts/scenarios, plus what contracts/apis.md promises.

Owner: platform (agents)
Step:  shared
Reads: the make_graph and make_unavailable_graph fixtures; contracts/scenarios/*.json
Emits: pytest tests marked `contract` (and `safety` where a safety rule is at stake), one per golden scenario
Rules: time runs forward (chains in time order); known-clean source (unknown-history ancestors reported); fail closed
       (GraphUnavailable on failure); a scenario's expected allergens must be found, and its expected passes must
       stay passable for the plain-code verdict (clean-confidence = 1 - sum of forbidden allergens' probabilities)

Each plate is queried as of the moment it reaches the pass. A real graph's factory typically loads the scenario into a
FakeStateClient with load_scenario() and builds the graph on it.
"""

import pytest

from kitchen.shared.fakes.scenarios import GOLDEN_SCENARIO_PATHS, read_scenario
from kitchen.shared.interfaces import GraphUnavailable
from kitchen.shared.models import validate

SCENARIOS = [read_scenario(path) for path in GOLDEN_SCENARIO_PATHS]
SCENARIO_IDS = [scenario["scenario_id"] for scenario in SCENARIOS]


def _pass_time(scenario: dict, plate_id: str) -> int:
    """When the plate reaches the pass: the moment its verdict is taken."""
    return next(plate["t_ms"] for plate in scenario["plates_at_pass"] if plate["plate_id"] == plate_id)


def _ticket(scenario: dict, plate_id: str, revision: int) -> dict:
    """The ticket revision an expected entry judges the plate against (the original or an amendment)."""
    ticket_id = next(plate["ticket_id"] for plate in scenario["plates_at_pass"] if plate["plate_id"] == plate_id)
    tickets = scenario["tickets"] + scenario.get("amendments", [])
    return next(t for t in tickets if t["ticket_id"] == ticket_id and t["revision"] == revision)


def _plates(scenario: dict) -> list[str]:
    """Every plate that reaches the pass in the scenario."""
    return [plate["plate_id"] for plate in scenario["plates_at_pass"]]


def _late_unknown_ids(scenario: dict) -> set[str]:
    """The scenario's late entities with unknown history (not from a clean stack)."""
    return {
        late["entity_id"] for late in scenario.get("late_entities", []) if late.get("origin", "unknown") == "unknown"
    }


@pytest.mark.contract
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_reach_returns_valid_allergen_reach_records(make_graph, scenario):
    graph = make_graph(scenario)
    for plate_id in _plates(scenario):
        for reach in graph.reach(plate_id, _pass_time(scenario, plate_id)):
            validate(reach, "allergen_reach")


@pytest.mark.contract
@pytest.mark.safety
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_reach_finds_every_expected_allergen(make_graph, scenario):
    """A missed allergen is a PASS that should have been a HOLD: the direction that hurts people."""
    graph = make_graph(scenario)
    for expected in scenario["expected"]:
        plate_id = expected["plate_id"]
        found = {reach["allergen_unit_id"] for reach in graph.reach(plate_id, _pass_time(scenario, plate_id))}
        assert set(expected["allergens_reached"]) <= found, f"{plate_id} revision {expected['ticket_revision']}"


@pytest.mark.contract
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_expected_passes_stay_passable(make_graph, scenario):
    """A plate the scenario passes must keep clean-confidence at or above its ticket's pass threshold."""
    graph = make_graph(scenario)
    for expected in scenario["expected"]:
        if expected["decision"] != "pass":
            continue
        plate_id = expected["plate_id"]
        ticket = _ticket(scenario, plate_id, expected["ticket_revision"])
        reached = graph.reach(plate_id, _pass_time(scenario, plate_id))
        forbidden_total = sum(r["probability"] for r in reached if r["allergen_unit_id"] in ticket["forbidden"])
        assert 1 - forbidden_total >= ticket["pass_threshold"], f"{plate_id} revision {expected['ticket_revision']}"


@pytest.mark.contract
@pytest.mark.safety
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_chains_run_forward_in_time_to_the_plate(make_graph, scenario):
    """Time runs forward: each hop happens no earlier than the one before, and the last hop lands on the plate."""
    graph = make_graph(scenario)
    for plate_id in _plates(scenario):
        as_of_t_ms = _pass_time(scenario, plate_id)
        for reach in graph.reach(plate_id, as_of_t_ms):
            chain = reach["chain"]
            if not chain:
                continue
            times = [step["t_ms"] for step in chain]
            assert [step["step"] for step in chain] == list(range(1, len(chain) + 1))
            assert times == sorted(times) and times[-1] <= as_of_t_ms
            assert all(step["allergen_unit_id"] == reach["allergen_unit_id"] for step in chain)
            assert all(hop["to_id"] == after["from_id"] for hop, after in zip(chain, chain[1:], strict=False))
            assert chain[-1]["to_id"] == plate_id


@pytest.mark.contract
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_expected_chains_are_found(make_graph, scenario):
    graph = make_graph(scenario)
    for expected in scenario["expected"]:
        if not expected.get("chain_event_ids"):
            continue
        plate_id = expected["plate_id"]
        chains = [
            [step["event_id"] for step in reach["chain"]]
            for reach in graph.reach(plate_id, _pass_time(scenario, plate_id))
        ]
        assert expected["chain_event_ids"] in chains, f"{plate_id} revision {expected['ticket_revision']}"


@pytest.mark.contract
@pytest.mark.safety
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_unknown_ancestors_are_the_expected_unknown_history_entities(make_graph, scenario):
    """Known-clean source: an unwashed unknown-history entity in a plate's past is reported, and nothing else is."""
    graph = make_graph(scenario)
    for plate_id in _plates(scenario):
        ancestors = set(graph.unknown_ancestors(plate_id, _pass_time(scenario, plate_id)))
        expects_unknown = any(
            "unknown_history" in expected.get("reason_codes", [])
            for expected in scenario["expected"]
            if expected["plate_id"] == plate_id
        )
        if expects_unknown:
            assert ancestors and ancestors <= _late_unknown_ids(scenario), plate_id
        else:
            assert ancestors == set(), plate_id


@pytest.mark.contract
def test_graph_version_is_a_nonempty_string(make_graph):
    version = make_graph(SCENARIOS[0]).version()
    assert isinstance(version, str) and version


@pytest.mark.contract
@pytest.mark.safety
@pytest.mark.parametrize("query", ["reach", "unknown_ancestors"])
def test_a_failing_graph_raises_graph_unavailable(make_unavailable_graph, query):
    """Fail closed: the verdict policy can only turn a broken graph into HOLD if it raises GraphUnavailable."""
    scenario = SCENARIOS[0]
    plate_id = _plates(scenario)[0]
    graph = make_unavailable_graph(scenario)
    with pytest.raises(GraphUnavailable):
        getattr(graph, query)(plate_id, _pass_time(scenario, plate_id))
