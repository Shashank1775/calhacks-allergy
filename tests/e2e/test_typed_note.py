"""The vertical slice from a typed note: Orders API, questions, ticket, plate at the pass, verdict, gate and voice,
including a late amendment made through the Orders API.

Owner: platform (agents)
Step:  end-to-end harness
Reads: the `kitchen` fixture (tests/e2e/conftest.py); golden scenario late-amendment-adds-dairy for the kitchen's
       world (its tickets are left out and made here through orders)
Emits: nothing
Rules: no unanswered questions (a draft with open questions can't be sent); late changes recheck everything (an
       amendment through orders rejudges the plate and closes the gate); fail closed
"""

import pytest

from kitchen.orders.laya_parser import FakeLayaParser
from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS
from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import validate

DAIRY_SCENARIO = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == "late-amendment-adds-dairy")
ANSWERS = {"severity": "allergy", "trace_sensitive": True}


@pytest.mark.safety
def test_a_note_with_open_questions_cant_be_sent(kitchen):
    """No unanswered questions: /send refuses a draft whose questions aren't all answered, and nothing is stored."""
    shift = kitchen(DAIRY_SCENARIO, with_tickets=False)

    draft = shift.orders.post("/parse", json={"note": "no peanut", "source": "typed"}).json()
    response = shift.orders.post(f"/drafts/{draft['draft_id']}/send", json={"number": 5})

    assert draft["questions"], "the fake parser should ask about severity and traces for 'no peanut'"
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unanswered_questions"
    assert shift.state.get_ticket("t5") is None


@pytest.mark.safety
def test_typed_note_to_gate_with_a_late_amendment(kitchen):
    """The whole slice. 'no peanut' becomes ticket t5; the plate, which carries milk, passes and the gate opens.
    The guest then adds 'also no dairy' through orders: revision 2 rechecks the plate, it holds, the gate closes and
    the expediter hears it."""
    shift = kitchen(DAIRY_SCENARIO, with_tickets=False)

    shift.at(100)
    ticket = shift.send_note("no peanut", number=5, answers=ANSWERS)
    assert validate(shift.state.get_ticket("t5"), "ticket") == ticket
    assert (ticket["ticket_id"], ticket["revision"], ticket["forbidden"]) == ("t5", 1, ["peanut"])
    assert ticket["pass_threshold"] == 0.95

    shift.at(3000)
    shift.plate_reaches_pass("plate-t5")
    assert shift.tick() == "pass"

    shift.at(4000)
    amended = shift.amend("t5", "also no dairy", answers=ANSWERS)
    assert (amended["revision"], amended["forbidden"]) == (2, ["milk", "peanut"])
    assert shift.tick() == "hold"

    first, second = shift.state.list_verdicts()
    assert (first["ticket_revision"], first["decision"]) == (1, "pass")
    assert (second["ticket_revision"], second["trigger"], second["decision"]) == (2, "ticket_amended", "hold")
    assert "forbidden_allergen" in [reason["code"] for reason in second["reasons"]]
    assert shift.gate.calls[-1] == ("hold", 5)
    assert shift.speaker.lines == ["Hold five, milk"]


class MissesPeanutOnReparse:
    """A LayaParser that reads the first note right and then misses peanut in every later note, as a model can
    misread an amended note. Orders re-parses the whole note on an amendment, so this is the case where only its
    keep-what-was-forbidden rule stands between the guest and a dropped allergen."""

    def __init__(self):
        self.fake = FakeLayaParser()
        self.calls = 0

    def parse_note(self, note, *, source="typed"):
        self.calls += 1
        return self.fake.parse_note(note if self.calls == 1 else note.replace("peanut", ""), source=source)


@pytest.mark.safety
def test_an_amendment_cant_drop_an_allergen_without_an_answer(kitchen):
    """Late changes recheck everything: even when Laya misses peanut on the re-parse, revision 2 still forbids it,
    because only an answered question removes an allergen."""
    parser = MissesPeanutOnReparse()
    shift = kitchen(DAIRY_SCENARIO, with_tickets=False, parser=parser)
    shift.at(100)
    shift.send_note("no peanut", number=5, answers=ANSWERS)

    shift.at(200)
    amended = shift.amend("t5", "extra napkins please", answers=ANSWERS)

    assert parser.calls == 2
    assert amended["revision"] == 2
    assert "peanut" in amended["forbidden"]


@pytest.mark.safety
def test_the_gate_holds_on_an_amendment_before_its_recheck_lands(kitchen):
    """Late changes recheck everything: once the guest adds dairy, revision 1's PASS stops opening the gate, even
    while the recheck's verdict is still waiting to be written."""
    shift = kitchen(DAIRY_SCENARIO, with_tickets=False)
    shift.at(100)
    shift.send_note("no peanut", number=5, answers=ANSWERS)
    shift.at(3000)
    shift.plate_reaches_pass("plate-t5")
    assert shift.tick() == "pass"

    record_verdict = shift.state.record_verdict

    def lost_write(verdict):
        raise StateUnavailable("verdict write lost (test)")

    shift.state.record_verdict = lost_write
    shift.at(4000)
    shift.amend("t5", "also no dairy", answers=ANSWERS)

    assert [v["ticket_revision"] for v in shift.state.list_verdicts()] == [1]  # the recheck hasn't landed
    assert shift.tick() == "hold"

    shift.state.record_verdict = record_verdict
    shift.verdict_service.retry()
    assert [(v["ticket_revision"], v["decision"]) for v in shift.state.list_verdicts()] == [(1, "pass"), (2, "hold")]
    assert shift.tick() == "hold"
