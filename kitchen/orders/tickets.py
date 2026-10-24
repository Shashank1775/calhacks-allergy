"""Drafts and tickets: parse a note into a draft with one-tap questions, take answers, send, and amend.

Owner: orders (agents)
Step:  orders
Reads: note text and source (typed | voice); a LayaParser; answers (question_id, value); the current Ticket
       (StateClient.get_ticket) for amendments
Emits: ParseResult drafts; Ticket via StateClient.put_ticket, with pass_threshold set from the strictest severity
Rules: no unanswered questions: only a draft with every question answered can be sent;
       never drop a mentioned allergen: on an amendment an allergen leaves the ticket only through an answered question;
       late changes recheck everything: an amendment writes revision + 1 with the full forbidden list;
       StateUnavailable leaves the draft unsent

Drafts live in memory here; only sent tickets go to the database.
"""

import copy
from dataclasses import dataclass

# Clean-confidence a plate needs, by the strictest severity on the ticket (AGENTS.md, OQ-4: settled).
PASS_THRESHOLDS = {"preference": 0.80, "intolerance": 0.90, "allergy": 0.95, "anaphylactic": 0.99}

# Severity for an allergen added by a one-tap answer, where the note gave none (OQ-3 default).
DEFAULT_SEVERITY = "allergy"


class OrdersError(Exception):
    """A request the tablet got wrong. The API turns it into the standard error body with this status."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class Draft:
    """A parsed note waiting for answers. `amends` is the Ticket being changed, or None for a new ticket."""

    result: dict
    amends: dict | None = None
    sent: bool = False


def resolved_constraints(result):
    """The draft's constraints with every answer applied.

    "No" on "Keep X off?" removes X. A severity or trace answer replaces that field. A "new constraint"
    answer adds the allergen(s) it names. Exception answers change nothing here: the ticket forbids the
    whole allergen either way.
    """
    constraints = copy.deepcopy(result["constraints"])
    removed = set()
    for question in result["questions"]:
        target, value = question["target"], question["answer"]
        if target["kind"] == "constraint":
            if target["field"] == "allergen_unit_id" and value is None:
                removed.add(target["index"])
            else:
                constraints[target["index"]][target["field"]] = value
        elif target["kind"] == "new_constraint" and value is not None:
            for unit in value if isinstance(value, list) else [value]:
                constraints.append({"allergen_unit_id": unit, "severity": DEFAULT_SEVERITY, "trace_sensitive": True})
    return [c for i, c in enumerate(constraints) if i not in removed]


def removed_by_answer(result):
    """Allergens a person explicitly took off with a "No" answer."""
    removed = set()
    for question in result["questions"]:
        target = question["target"]
        if target["kind"] == "constraint" and target["field"] == "allergen_unit_id" and question["answer"] is None:
            removed.add(result["constraints"][target["index"]]["allergen_unit_id"])
    return removed


def pass_threshold(severities):
    """The strictest severity's threshold. With nothing forbidden the loosest one: no allergen can fail it."""
    return max((PASS_THRESHOLDS[s] for s in severities), default=PASS_THRESHOLDS["preference"])


class TicketDesk:
    """Holds open drafts and turns them into tickets."""

    def __init__(self, parser, state, clock):
        self._parser = parser
        self._state = state
        self._clock = clock
        self._drafts = {}  # draft_id -> Draft

    def parse(self, note, source):
        """A new draft for a new ticket. LayaParserUnavailable passes through: nothing guesses."""
        result = self._parser.parse_note(note, source=source)
        self._drafts[result["draft_id"]] = Draft(result)
        return result

    def answer(self, draft_id, question_id, value):
        """Record one answer. The draft becomes sendable once every question has one."""
        result = self._open_draft(draft_id).result
        question = next((q for q in result["questions"] if q["question_id"] == question_id), None)
        if question is None:
            raise OrdersError(404, "question_not_found", f"draft {draft_id} has no question {question_id}")
        if value not in [option["value"] for option in question["options"]]:
            raise OrdersError(400, "bad_answer", f"{value!r} is not one of the options for {question_id}")
        question["answer"] = value
        result["sendable"] = all("answer" in q for q in result["questions"])
        return result

    def send(self, draft_id, number=None):
        """Write the ticket. An amendment keeps the ticket's ID and number and writes revision + 1."""
        draft = self._open_draft(draft_id)
        result = draft.result
        if not result["sendable"]:
            raise OrdersError(409, "unanswered_questions", "answer every question before sending")

        constraints = resolved_constraints(result)
        forbidden = {c["allergen_unit_id"] for c in constraints}
        threshold = pass_threshold(c["severity"] for c in constraints)

        if draft.amends is None:
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                raise OrdersError(400, "bad_request", 'send needs the ticket number, e.g. {"number": 7}')
            ticket_id = f"t{number}"
            if self._state.get_ticket(ticket_id) is not None:
                raise OrdersError(409, "number_in_use", f"ticket {number} was already sent this shift")
            revision = 1
            created_t_ms = self._clock.now_ms()
        else:
            old = draft.amends
            current = self._state.get_ticket(old["ticket_id"])
            if current is None or current["revision"] != old["revision"]:
                raise OrdersError(409, "ticket_changed", "the ticket changed since this amendment started; amend again")
            ticket_id, number, revision = old["ticket_id"], old["number"], old["revision"] + 1
            created_t_ms = old["created_t_ms"]
            # Safety: an allergen the old ticket forbade stays unless a person answered "No" to it,
            # and the threshold never gets looser on a late change.
            forbidden |= set(old["forbidden"]) - removed_by_answer(result)
            threshold = max(threshold, old["pass_threshold"])

        ticket = {
            "ticket_id": ticket_id,
            "number": number,
            "revision": revision,
            "created_t_ms": created_t_ms,
            "source": result["source"],
            "note": result["note"],
            "pass_threshold": threshold,
            "forbidden": sorted(forbidden),
        }
        self._state.put_ticket(ticket)  # StateUnavailable passes through and the draft stays unsent
        draft.sent = True
        return ticket

    def amend(self, ticket_id, change):
        """A new draft for revision + 1: the original note plus the change, parsed again from scratch."""
        if not change.strip():
            raise OrdersError(400, "bad_request", "the amendment is empty")
        old = self._state.get_ticket(ticket_id)
        if old is None:
            raise OrdersError(404, "ticket_not_found", f"no ticket {ticket_id}")
        note = f"{old['note'].rstrip(' .')}. {change.strip()}"
        result = self._parser.parse_note(note, source=old["source"])
        self._drafts[result["draft_id"]] = Draft(result, amends=old)
        return result

    def _open_draft(self, draft_id):
        """The draft, if it exists and hasn't been sent yet."""
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise OrdersError(404, "draft_not_found", f"no draft {draft_id}")
        if draft.sent:
            raise OrdersError(409, "already_sent", f"draft {draft_id} was already sent")
        return draft
