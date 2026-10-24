"""Tests for decide(): table-driven cases, schema checks, and Hypothesis properties for the safety rules.

Owner: verdict (agents)
Step:  verdict
Reads: kitchen.verdict.policy, contracts/schemas, contracts/fixtures, contracts/allergen_units.json
Emits: nothing
Rules: fail closed, plain-code verdict, allergens not contacts and known-clean source (safety tests)
"""

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from kitchen.verdict.policy import clean_confidence, decide, reach_probabilities, ticket_problem

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
FIXTURES = CONTRACTS / "fixtures" / "valid"
TICKET = json.loads((FIXTURES / "ticket" / "peanut-anaphylactic.json").read_text())
PEANUT_REACH = json.loads((FIXTURES / "allergen_reach" / "peanut-via-knife.json").read_text())
HOLD_FIXTURE = json.loads((FIXTURES / "verdict" / "hold-peanut.json").read_text())
UNITS = [unit["id"] for unit in json.loads((CONTRACTS / "allergen_units.json").read_text())["units"]]
THRESHOLDS = [0.80, 0.90, 0.95, 0.99]


def verdict_validator():
    """A validator for verdict.schema.json with the other schemas registered for its $refs."""
    docs = {p.name: json.loads(p.read_text()) for p in (CONTRACTS / "schemas").glob("*.schema.json")}
    registry = Registry().with_resources((name, Resource.from_contents(doc)) for name, doc in docs.items())
    return Draft202012Validator(docs["verdict.schema.json"], registry=registry)


VALIDATOR = verdict_validator()


def ticket(forbidden, threshold=0.99, revision=1):
    """A ticket like the fixture, forbidding `forbidden` at `threshold`."""
    return dict(TICKET, forbidden=list(forbidden), pass_threshold=threshold, revision=revision)


def reach(**probabilities):
    """AllergenReach entries with empty chains: reach(peanut=0.3, milk=0.1)."""
    return [{"allergen_unit_id": unit, "probability": p, "chain": []} for unit, p in probabilities.items()]


def run(ticket=TICKET, reach=(), unknown_ancestors=(), problems=(), graph="0.1.0"):
    """decide() with the fixture's IDs; None for ticket, reach or unknown_ancestors means it couldn't be read."""
    return decide(
        verdict_id="v-t7-1",
        plate_id="plate-t7",
        trigger="plate_at_pass",
        decided_t_ms=190050,
        ticket_id="t7",
        ticket=ticket,
        reach=None if reach is None else list(reach),
        unknown_ancestors=None if unknown_ancestors is None else list(unknown_ancestors),
        problems=problems,
        graph=graph,
    )


def codes(verdict):
    """The verdict's reason codes, in order."""
    return [reason["code"] for reason in verdict["reasons"]]


CASES = {
    "clean-plate-passes": (dict(), "pass", []),
    "fixture-peanut-holds": (dict(reach=[PEANUT_REACH]), "hold", ["forbidden_allergen", "low_confidence"]),
    "unforbidden-allergen-passes": (dict(reach=reach(milk=1.0)), "pass", []),
    "trace-under-threshold-passes": (dict(ticket=ticket(["peanut"], 0.95), reach=reach(peanut=0.04)), "pass", []),
    "trace-at-threshold-flags": (
        dict(ticket=ticket(["peanut"], 0.95), reach=reach(peanut=0.05)),
        "hold",
        ["forbidden_allergen"],
    ),
    "two-traces-add-up": (
        dict(ticket=ticket(["peanut", "soy"], 0.95), reach=reach(peanut=0.03, soy=0.03)),
        "hold",
        ["low_confidence"],
    ),
    "unknown-ancestor-holds": (dict(unknown_ancestors=["tool-spoon-9"]), "hold", ["unknown_history", "low_confidence"]),
    "missing-ticket-holds": (dict(ticket=None), "hold", ["missing_input", "low_confidence"]),
    "graph-reach-down-holds": (dict(reach=None), "hold", ["error", "low_confidence"]),
    "graph-ancestors-down-holds": (dict(unknown_ancestors=None), "hold", ["error", "low_confidence"]),
    "stale-track-holds": (dict(problems=[{"code": "stale_track"}]), "hold", ["stale_track", "low_confidence"]),
    "malformed-reach-holds": (dict(reach=[{"probability": 0.0}]), "hold", ["error"]),
    "unknown-problem-code-holds": (dict(problems=[{"code": "laya_said_ok"}]), "hold", ["error"]),
}


@pytest.mark.parametrize(("inputs", "decision", "reason_codes"), CASES.values(), ids=CASES.keys())
def test_decide_cases(inputs, decision, reason_codes):
    verdict = run(**inputs)

    assert verdict["decision"] == decision
    assert codes(verdict) == reason_codes
    assert not list(VALIDATOR.iter_errors(verdict))


def test_fixture_hold_is_reproduced():
    verdict = run(reach=[PEANUT_REACH])

    for field in ("ticket_id", "ticket_revision", "plate_id", "decision", "pass_threshold", "flagged", "chain"):
        assert verdict[field] == HOLD_FIXTURE[field]
    assert verdict["confidence"] == pytest.approx(HOLD_FIXTURE["confidence"])
    assert verdict["reasons"] == HOLD_FIXTURE["reasons"]


def test_versions_are_recorded():
    assert run()["versions"] == {"policy": "0.1.1", "graph": "0.1.0"}
    assert run(graph=None)["versions"]["graph"] == "unavailable"


def test_same_inputs_give_byte_identical_json():
    first = json.dumps(run(reach=[PEANUT_REACH], unknown_ancestors=["tool-spoon-9"]))
    second = json.dumps(run(reach=[PEANUT_REACH], unknown_ancestors=["tool-spoon-9"]))

    assert first == second


def test_duplicate_reach_entries_count_the_larger():
    assert reach_probabilities(reach(peanut=0.1) + reach(peanut=0.4)) == {"peanut": 0.4}
    assert clean_confidence(["peanut", "soy"], {"peanut": 0.4, "soy": 0.5, "milk": 0.9}) == pytest.approx(0.1)


# Hypothesis inputs: a ticket over the nine units at one of the four thresholds, and reach over any of the nine.
tickets = st.builds(
    ticket,
    forbidden=st.lists(st.sampled_from(UNITS), unique=True, max_size=4),
    threshold=st.sampled_from(THRESHOLDS),
)
probability = st.floats(0.0, 1.0, allow_nan=False)
reaches = st.dictionaries(st.sampled_from(UNITS), probability, max_size=6).map(lambda ps: reach(**ps))
problems = st.lists(st.sampled_from([{"code": "stale_track"}, {"code": "missing_input"}, {"code": "error"}]))


@pytest.mark.safety
@given(
    t=st.none() | tickets,
    r=st.none() | reaches,
    unknown=st.none() | st.lists(st.just("tool-spoon-9"), max_size=1),
    found=problems,
)
def test_fail_closed(t, r, unknown, found):
    """Fail closed: any missing input, error, stale track or unknown ancestor holds with confidence 0."""
    verdict = run(ticket=t, reach=r, unknown_ancestors=unknown, problems=found)

    unhealthy = t is None or r is None or unknown is None or bool(unknown) or bool(found)
    if unhealthy:
        assert verdict["decision"] == "hold"
        assert verdict["confidence"] == 0.0
    assert not list(VALIDATOR.iter_errors(verdict))


@pytest.mark.safety
@given(t=tickets, r=reaches)
def test_plain_code_verdict_is_the_formula(t, r):
    """Plain-code verdict: on healthy inputs, decide() is exactly the README's formula, nothing more or less."""
    verdict = run(ticket=t, reach=r)

    p = {entry["allergen_unit_id"]: entry["probability"] for entry in r}
    confidence = max(0.0, 1.0 - sum(p.get(unit, 0.0) for unit in t["forbidden"]))
    flagged = [unit for unit in t["forbidden"] if unit in p and 1.0 - p[unit] <= t["pass_threshold"]]
    hold = bool(flagged) or confidence < t["pass_threshold"]

    assert verdict["confidence"] == pytest.approx(confidence)
    assert [item["allergen_unit_id"] for item in verdict["flagged"]] == flagged
    assert verdict["decision"] == ("hold" if hold else "pass")


@pytest.mark.safety
@given(t=tickets, r=reaches, unit=st.sampled_from(UNITS), extra=probability)
def test_more_evidence_never_helps(t, r, unit, extra):
    """Plain-code verdict: raising any allergen's probability never raises confidence or turns a HOLD into a PASS."""
    before = run(ticket=t, reach=r)
    p = {entry["allergen_unit_id"]: entry["probability"] for entry in r}
    p[unit] = max(p.get(unit, 0.0), extra)
    after = run(ticket=t, reach=reach(**p))

    assert after["confidence"] <= before["confidence"]
    if before["decision"] == "hold":
        assert after["decision"] == "hold"


@pytest.mark.safety
@given(t=tickets, r=reaches, extra=st.dictionaries(st.sampled_from(UNITS), probability, max_size=4))
def test_allergens_not_contacts(t, r, extra):
    """Allergens, not contacts: allergens the ticket doesn't forbid never change the decision or confidence."""
    p = {entry["allergen_unit_id"]: entry["probability"] for entry in r}
    noisy = dict(p, **{unit: q for unit, q in extra.items() if unit not in t["forbidden"]})

    before, after = run(ticket=t, reach=r), run(ticket=t, reach=reach(**noisy))

    assert after["decision"] == before["decision"]
    assert after["confidence"] == before["confidence"]


@pytest.mark.safety
@given(junk=st.lists(st.dictionaries(st.sampled_from(["allergen_unit_id", "probability", "chain"]), st.none())))
def test_fail_closed_on_malformed_reach(junk):
    """Fail closed: whatever shape the graph's answer has, a broken one holds and still makes a valid verdict."""
    verdict = run(reach=junk)

    if junk:
        assert verdict["decision"] == "hold"
        assert verdict["confidence"] == 0.0
    assert not list(VALIDATOR.iter_errors(verdict))


MALFORMED_TICKETS = {
    "nan-threshold": dict(TICKET, pass_threshold=float("nan")),
    "threshold-below-zero": dict(TICKET, pass_threshold=-1),
    "threshold-above-one": dict(TICKET, pass_threshold=1.5),
    "forbidden-a-string": dict(TICKET, forbidden="peanut"),
    "revision-a-string": dict(TICKET, revision="1"),
    "revision-zero": dict(TICKET, revision=0),
    "revision-a-bool": dict(TICKET, revision=True),
    "no-forbidden": {key: value for key, value in TICKET.items() if key != "forbidden"},
    "not-a-dict": "t7",
}


@pytest.mark.safety
@pytest.mark.parametrize("bad", MALFORMED_TICKETS.values(), ids=MALFORMED_TICKETS.keys())
def test_a_malformed_ticket_holds(bad):
    """Fail closed: a ticket decide() can't trust, a NaN threshold above all (NaN fails every comparison, so it would
    never trip a hold), holds with reason error even when the graph finds the forbidden allergen everywhere."""
    verdict = run(ticket=bad, reach=reach(peanut=1.0))

    assert verdict["decision"] == "hold"
    assert verdict["confidence"] == 0.0
    assert "error" in codes(verdict)
    assert not list(VALIDATOR.iter_errors(verdict))


@pytest.mark.safety
@pytest.mark.parametrize("p", [float("nan"), float("inf"), -0.1, 1.5, "0.5", True])
def test_a_reach_probability_that_isnt_one_holds(p):
    """Fail closed: a graph answer that isn't a real probability holds instead of being compared."""
    verdict = run(reach=[{"allergen_unit_id": "peanut", "probability": p, "chain": []}])

    assert verdict["decision"] == "hold"
    assert codes(verdict) == ["error"]


@pytest.mark.safety
def test_unknown_ancestors_must_be_a_list_of_ids():
    assert run(unknown_ancestors="tool-spoon-9")["decision"] == "hold"


junk = (
    st.none() | st.booleans() | st.integers(-2, 3) | st.floats() | st.text(max_size=3) | st.lists(st.text(max_size=3))
)


@pytest.mark.safety
@given(field=st.sampled_from(sorted(TICKET)), value=junk, r=reaches)
def test_every_verdict_is_schema_valid_whatever_the_ticket_holds(field, value, r):
    """Fail closed: whatever one ticket field holds, decide() returns a valid verdict, and a bad field never passes."""
    bad = dict(TICKET, **{field: value})
    verdict = run(ticket=bad, reach=r)

    assert not list(VALIDATOR.iter_errors(verdict))
    if verdict["decision"] == "pass":
        assert ticket_problem(bad) is None
