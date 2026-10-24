"""Tests for the LayaParser adapters. They run without torch: the real adapter gets a scripted agent."""

import hashlib
import json
import math

import pytest

from kitchen.orders.bins import allergen_unit_ids
from kitchen.orders.eval import run_demo
from kitchen.orders.laya_parser import (
    QUESTION_THRESHOLD,
    SEVERITIES,
    UNITS,
    FakeLayaParser,
    LayaParserUnavailable,
    LocalLayaParser,
    UnitAnswer,
    build_parse_result,
    note_questions,
    note_state,
    unmentioned,
)

# sha256 of every question and the note state, exactly as the ticket-note model was fine-tuned on them.
# If this test fails, the wording changed: retrain Laya, then update this hash.
TRAINED_WORDING_SHA256 = "4ec3063c08fe2c11028b4bc4b4ea6be9476f60578ca7d10e1aea1c00805638bc"


class ScriptedAgent:
    """Stands in for a loaded Laya agent: answers every question with fixed probabilities."""

    def __init__(self, forbid, fail=False, broken=None):
        self.forbid = forbid  # unit -> P(forbidden)
        self.fail = fail
        self.broken = broken  # replaces every forbid answer when set, to fake a malformed reply
        self.calls = []

    def predict(self, state, questions):
        if self.fail:
            raise RuntimeError("model crashed")
        self.calls.append(sorted(questions))
        answers = {}
        for qid in questions:
            kind, unit = qid.split("_", 1)
            if kind == "forbid":
                answers[qid] = self.broken if self.broken is not None else {"noul": self.forbid.get(unit, 0.01)}
            elif kind == "severity":
                answers[qid] = {"probabilities": {"0": 0.0, "1": 0.0, "2": 0.1, "3": 0.9}}
            else:
                answers[qid] = {"noul": 0.95 if kind == "traces" else 0.02}
        return {"answers": answers}


def local_parser(agent):
    """A LocalLayaParser wired to a scripted agent instead of a real checkpoint."""
    parser = object.__new__(LocalLayaParser)
    parser._agent, parser._version, parser._threshold = agent, "test", QUESTION_THRESHOLD
    return parser


def test_fake_passes_the_demo_notes():
    assert run_demo.run(FakeLayaParser()) == 0


def test_the_parser_knows_every_allergen_unit():
    assert UNITS == allergen_unit_ids()


def test_question_wording_matches_training():
    wording = {"questions": [note_questions(u) for u in UNITS], "state": note_state("x", "voice")}
    digest = hashlib.sha256(json.dumps(wording, sort_keys=True).encode()).hexdigest()
    assert digest == TRAINED_WORDING_SHA256


@pytest.mark.parametrize("note", ["peanut allergy with an EpiPen", "dairy free", "no nuts", "no allergies"])
def test_fake_parse_result_matches_the_contract(note, check_schema):
    assert check_schema("parse_result", FakeLayaParser().parse_note(note, source="typed")) == []


@pytest.mark.safety
def test_every_low_confidence_field_gets_a_question():
    answers = {u: unmentioned(0.01) for u in UNITS}
    answers["peanut"] = UnitAnswer(0.6, [0.2, 0.1, 0.6, 0.1], 0.7, 0.0)  # every field unsure
    result = build_parse_result("maybe peanuts?", "typed", answers, "test")
    asked = {q["target"]["field"] for q in result["questions"]}
    assert asked == {"allergen_unit_id", "severity", "trace_sensitive"}
    assert result["sendable"] is False


def test_unlikely_allergens_are_left_out():
    answers = {u: unmentioned(0.01) for u in UNITS}
    answers["egg"] = UnitAnswer(0.97, [0, 0, 0.95, 0.05], 0.95, 0.0)
    result = build_parse_result("egg allergy", "typed", answers, "test")
    assert [c["allergen_unit_id"] for c in result["constraints"]] == ["egg"]
    assert result["sendable"] is True


def test_the_same_note_twice_gets_two_drafts():
    parser = FakeLayaParser()
    assert (
        parser.parse_note("nut allergy", source="typed")["draft_id"]
        != parser.parse_note("nut allergy", source="typed")["draft_id"]
    )


@pytest.mark.safety
def test_bare_nuts_asks_about_tree_nuts_and_peanuts():
    result = FakeLayaParser().parse_note("allergic to nuts, carries an epipen", source="typed")
    asked = {
        result["constraints"][q["target"]["index"]]["allergen_unit_id"]
        for q in result["questions"]
        if q["target"]["field"] == "allergen_unit_id"
    }
    assert asked == {"tree_nut", "peanut"}


@pytest.mark.safety
def test_no_allergies_does_not_hide_a_named_allergen():
    result = FakeLayaParser().parse_note("no allergies except peanuts", source="typed")
    assert [c["allergen_unit_id"] for c in result["constraints"]] == ["peanut"]


def test_local_parser_asks_follow_ups_only_for_flagged_allergens(check_schema):
    agent = ScriptedAgent({"peanut": 0.97})
    result = local_parser(agent).parse_note("peanut allergy", source="typed")
    assert agent.calls[1] == ["exception_peanut", "severity_peanut", "traces_peanut"]
    assert result["constraints"][0]["severity"] == SEVERITIES[3]
    assert check_schema("parse_result", result) == []


@pytest.mark.safety
def test_a_model_failure_never_becomes_an_answer():
    with pytest.raises(LayaParserUnavailable):
        local_parser(ScriptedAgent({}, fail=True)).parse_note("peanut allergy", source="typed")


@pytest.mark.safety
@pytest.mark.parametrize("broken", [{"noul": math.nan}, {"noul": 1.5}, {"probabilities": {"true": 0.9}}, {}])
def test_a_malformed_answer_never_becomes_an_answer(broken):
    with pytest.raises(LayaParserUnavailable):
        local_parser(ScriptedAgent({}, broken=broken)).parse_note("peanut allergy", source="typed")


@pytest.mark.safety
@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_note_is_refused(text):
    with pytest.raises(LayaParserUnavailable):
        FakeLayaParser().parse_note(text, source="typed")


def test_a_missing_checkpoint_raises():
    with pytest.raises(LayaParserUnavailable):
        LocalLayaParser("/no/such/checkpoint")
