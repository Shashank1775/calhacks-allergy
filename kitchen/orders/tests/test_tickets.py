"""Tests for drafts and tickets: answers, send, amendments, and the safety rules around them."""

from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kitchen.orders.api import InMemoryState
from kitchen.orders.laya_parser import UNITS, FakeLayaParser, UnitAnswer, build_parse_result, unmentioned
from kitchen.orders.tickets import OrdersError, TicketDesk, pass_threshold
from kitchen.shared.interfaces import StateUnavailable


class FixedParser:
    """A LayaParser that always returns a ParseResult built from the given per-allergen answers."""

    def __init__(self, answers):
        self.answers = answers

    def parse_note(self, note, *, source="typed"):
        full = {u: self.answers.get(u, unmentioned(0.01)) for u in UNITS}
        return build_parse_result(note, source, full, "test")


SURE_PEANUT = UnitAnswer(0.97, [0.0, 0.0, 0.04, 0.96], 0.95, 0.0)  # anaphylactic, no questions
UNSURE_MILK = UnitAnswer(0.6, [0.0, 0.0, 0.9, 0.1], 0.95, 0.0)  # one question: keep dairy off?


def desk_for(state, clock, answers=None):
    parser = FixedParser(answers) if answers is not None else FakeLayaParser()
    return TicketDesk(parser, state, clock)


def answer_all(desk, draft, choose=lambda question: question["options"][0]["value"]):
    """Answer every question on a draft (by default with its first option) and return the final draft."""
    for question in draft["questions"]:
        draft = desk.answer(draft["draft_id"], question["question_id"], choose(question))
    return draft


def test_a_sure_note_sends_straight_away(state, clock, check_schema):
    desk = desk_for(state, clock, {"peanut": SURE_PEANUT})
    draft = desk.parse("peanut allergy, EpiPen", "typed")
    ticket = desk.send(draft["draft_id"], number=7)
    assert ticket == {
        "ticket_id": "t7",
        "number": 7,
        "revision": 1,
        "created_t_ms": 120_000,
        "source": "typed",
        "note": "peanut allergy, EpiPen",
        "pass_threshold": 0.99,
        "forbidden": ["peanut"],
    }
    assert check_schema("ticket", ticket) == []
    assert state.tickets["t7"] == ticket


@pytest.mark.safety
def test_a_draft_with_an_open_question_cannot_be_sent(state, clock):
    desk = desk_for(state, clock, {"milk": UNSURE_MILK})
    draft = desk.parse("maybe dairy?", "typed")
    with pytest.raises(OrdersError) as error:
        desk.send(draft["draft_id"], number=3)
    assert (error.value.status, error.value.code) == (409, "unanswered_questions")
    assert state.tickets == {}


def test_answering_yes_keeps_the_allergen_and_no_removes_it(state, clock):
    desk = desk_for(state, clock, {"milk": UNSURE_MILK, "peanut": SURE_PEANUT})

    yes = answer_all(desk, desk.parse("dairy? peanut allergy", "typed"))
    assert yes["sendable"] is True
    assert desk.send(yes["draft_id"], number=1)["forbidden"] == ["milk", "peanut"]

    no = answer_all(desk, desk.parse("dairy? peanut allergy", "typed"), choose=lambda q: None)
    assert desk.send(no["draft_id"], number=2)["forbidden"] == ["peanut"]


def test_a_severity_answer_sets_the_pass_threshold(state, clock):
    unsure_severity = UnitAnswer(0.97, [0.3, 0.3, 0.4, 0.0], 0.95, 0.0)
    desk = desk_for(state, clock, {"egg": unsure_severity})
    draft = answer_all(desk, desk.parse("no egg", "typed"), choose=lambda q: "intolerance")
    assert desk.send(draft["draft_id"], number=4)["pass_threshold"] == 0.90


def test_an_answer_must_be_one_of_the_options(state, clock):
    desk = desk_for(state, clock, {"milk": UNSURE_MILK})
    draft = desk.parse("maybe dairy?", "typed")
    with pytest.raises(OrdersError) as error:
        desk.answer(draft["draft_id"], "q1", "soy")
    assert error.value.code == "bad_answer"


def test_a_ticket_number_is_used_once_per_shift(state, clock):
    desk = desk_for(state, clock, {"peanut": SURE_PEANUT})
    desk.send(desk.parse("peanut allergy", "typed")["draft_id"], number=5)
    with pytest.raises(OrdersError) as error:
        desk.send(desk.parse("peanut allergy", "typed")["draft_id"], number=5)
    assert error.value.code == "number_in_use"


def test_a_draft_is_sent_once(state, clock):
    desk = desk_for(state, clock, {"peanut": SURE_PEANUT})
    draft = desk.parse("peanut allergy", "typed")
    desk.send(draft["draft_id"], number=6)
    with pytest.raises(OrdersError) as error:
        desk.send(draft["draft_id"], number=8)
    assert error.value.code == "already_sent"


@pytest.mark.safety
def test_a_database_failure_leaves_the_draft_unsent(state, clock):
    desk = desk_for(state, clock, {"peanut": SURE_PEANUT})
    draft = desk.parse("peanut allergy", "typed")
    state.down = True
    with pytest.raises(StateUnavailable):
        desk.send(draft["draft_id"], number=9)
    state.down = False
    assert desk.send(draft["draft_id"], number=9)["ticket_id"] == "t9"


@pytest.mark.safety
def test_an_amendment_adds_to_the_ticket_and_bumps_the_revision(state, clock, check_schema):
    desk = desk_for(state, clock)  # the keyword fake reads the combined note
    desk.send(desk.parse("peanut allergy, EpiPen", "typed")["draft_id"], number=5)

    draft = desk.amend("t5", "also no dairy")
    assert draft["note"] == "peanut allergy, EpiPen. also no dairy"
    ticket = desk.send(answer_all(desk, draft)["draft_id"])
    assert (ticket["ticket_id"], ticket["number"], ticket["revision"]) == ("t5", 5, 2)
    assert ticket["forbidden"] == ["milk", "peanut"]
    assert ticket["pass_threshold"] == 0.99  # never looser than revision 1
    assert check_schema("ticket", ticket) == []


@pytest.mark.safety
def test_an_amendment_keeps_an_allergen_the_new_parse_missed(state, clock):
    desk = desk_for(state, clock, {"peanut": SURE_PEANUT})
    desk.send(desk.parse("peanut allergy", "typed")["draft_id"], number=2)

    desk._parser = FixedParser({"milk": UnitAnswer(0.97, [0, 0, 0.95, 0.05], 0.95, 0.0)})  # forgets peanut
    ticket = desk.send(desk.amend("t2", "also no dairy")["draft_id"])
    assert ticket["forbidden"] == ["milk", "peanut"]


@pytest.mark.safety
def test_an_amendment_drops_an_allergen_only_when_a_person_says_no(state, clock):
    desk = desk_for(state, clock, {"milk": UNSURE_MILK})
    first = answer_all(desk, desk.parse("dairy?", "typed"))
    desk.send(first["draft_id"], number=3)

    draft = answer_all(desk, desk.amend("t3", "actually dairy is fine"), choose=lambda q: None)
    assert desk.send(draft["draft_id"])["forbidden"] == []


def test_an_amendment_to_an_old_revision_is_refused(state, clock):
    desk = desk_for(state, clock, {"peanut": SURE_PEANUT})
    desk.send(desk.parse("peanut allergy", "typed")["draft_id"], number=1)
    stale = desk.amend("t1", "also no soy")
    desk.send(desk.amend("t1", "also no egg")["draft_id"])
    with pytest.raises(OrdersError) as error:
        desk.send(stale["draft_id"])
    assert error.value.code == "ticket_changed"


def test_amending_an_unknown_ticket_is_a_404(state, clock):
    with pytest.raises(OrdersError) as error:
        desk_for(state, clock).amend("t99", "also no dairy")
    assert error.value.status == 404


def test_pass_threshold_uses_the_strictest_severity():
    assert pass_threshold(["intolerance", "anaphylactic", "allergy"]) == 0.99
    assert pass_threshold([]) == 0.80


@pytest.mark.safety
@given(p_forbid=st.floats(0.15, 1.0), p_severity=st.floats(0.0, 1.0), p_traces=st.floats(0.0, 1.0))
def test_a_mentioned_allergen_is_never_sent_without_a_yes_or_a_sure_parse(p_forbid, p_severity, p_traces):
    severity = [0.0, 0.0, p_severity, 1.0 - p_severity]
    parser = FixedParser({"sesame": UnitAnswer(p_forbid, severity, p_traces, 0.0)})
    desk = TicketDesk(parser, InMemoryState(), SimpleNamespace(now_ms=lambda: 0))  # fresh state for every example
    draft = answer_all(desk, desk.parse("sesame?", "typed"))  # first option: keep it off
    assert desk.send(draft["draft_id"], number=1)["forbidden"] == ["sesame"]
