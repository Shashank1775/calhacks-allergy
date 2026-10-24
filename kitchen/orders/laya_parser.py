"""LayaParser adapters: LocalLayaParser (our fine-tuned Laya, run locally) and FakeLayaParser (keyword rules, for tests).

Owner: orders (agents)
Step:  orders
Reads: ticket note text
Emits: ParseResult (built from Laya's answers to typed questions)
Rules: fail closed: any failure raises LayaParserUnavailable, never a guess;
       no unanswered questions: every field below the question threshold gets a one-tap question

Laya doesn't write text. It reads a note and answers typed questions with probabilities. So for each
allergen we ask: does the note forbid it, how severe, do traces matter, and is some ingredient of it
still allowed. The answers are turned into a ParseResult here.

The question wording below is the wording the model was fine-tuned on. Don't edit it without retraining.
"""

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from kitchen.shared.interfaces import LayaParserUnavailable

__all__ = ["FakeLayaParser", "LayaParserUnavailable", "LocalLayaParser"]

UNITS = ["milk", "egg", "fish", "shellfish", "tree_nut", "peanut", "wheat", "soy", "sesame"]

# Plain names plus common ingredients, so the model can match ingredient words to allergens.
UNIT_TEXT = {
    "milk": "milk or dairy (cheese, butter, cream, yogurt, whey, casein, ghee)",
    "egg": "egg (mayo, aioli, meringue, custard)",
    "fish": "fish (anchovy, fish sauce, Worcestershire, bonito)",
    "shellfish": "crustacean shellfish (shrimp, prawn, crab, lobster, crawfish)",
    "tree_nut": "tree nuts (almond, cashew, walnut, pecan, pistachio, hazelnut, pine nut)",
    "peanut": "peanuts (peanut butter, peanut oil, satay)",
    "wheat": "wheat or gluten (flour, bread, pasta, semolina, panko)",
    "soy": "soy (soy sauce, tofu, edamame, miso)",
    "sesame": "sesame (tahini, sesame oil)",
}

# Short names for one-tap questions on the tablet.
UNIT_NAMES = {
    "milk": "dairy",
    "egg": "egg",
    "fish": "fish",
    "shellfish": "shellfish",
    "tree_nut": "tree nuts",
    "peanut": "peanuts",
    "wheat": "wheat",
    "soy": "soy",
    "sesame": "sesame",
}

SEVERITIES = ["preference", "intolerance", "allergy", "anaphylactic"]

QUESTION_THRESHOLD = 0.85  # a field below this gets a one-tap question
DROP_THRESHOLD = 0.15  # an allergen below this isn't listed at all


# ---------- the questions Laya was trained on ----------


def note_questions(unit):
    """The four typed questions asked about one allergen in a ticket note."""
    text = UNIT_TEXT[unit]
    return {
        f"forbid_{unit}": {
            "type": "noul",
            "instructions": f"Does this ticket note ask the kitchen to keep {text} away from the guest's food?",
            "criteria": {"false": f"no restriction on {unit}", "true": f"{unit} must be kept off the plate"},
        },
        f"severity_{unit}": {
            "type": "score",
            "instructions": f"How serious is the guest's reaction to {text}?",
            "criteria": [
                "preference: avoids it by choice",
                "intolerance: discomfort, not dangerous",
                "allergy: allergic reaction",
                "anaphylactic: severe or life-threatening, EpiPen",
            ],
        },
        f"traces_{unit}": {
            "type": "noul",
            "instructions": f"Do traces or cross-contact of {text} matter for this guest?",
            "criteria": {
                "false": "only keep it out of the recipe; traces are fine",
                "true": "even traces or shared utensils matter",
            },
        },
        f"exception_{unit}": {
            "type": "noul",
            "instructions": f"Does the note say some specific ingredient in the group {text} is still fine for the guest?",
            "criteria": {"false": "no allowed ingredient is named", "true": "an allowed ingredient is named"},
        },
    }


def note_state(note, source):
    """The state Laya reads for a ticket note (same shape as in training)."""
    kind = "voice transcript" if source == "voice" else "typed note"
    return {"ticket_note": note, "input": kind}


def read_answer(answer, question):
    """P(yes) for a yes/no question, or one probability per severity for a score question.

    Anything missing or outside 0..1 (NaN included) raises, so a broken answer can't pass as a real one.
    """
    if question["type"] == "noul":
        values = [answer["noul"]]
    else:
        values = [answer["probabilities"][str(i)] for i in range(len(SEVERITIES))]
    if not all(0.0 <= p <= 1.0 for p in values):  # also False for NaN
        raise ValueError(f"impossible probability in {values}")
    return values[0] if question["type"] == "noul" else values


# ---------- turning answers into a ParseResult ----------


@dataclass
class UnitAnswer:
    """What the model (or the fake) said about one allergen in a note."""

    p_forbid: float
    severity_probs: list  # one probability per entry in SEVERITIES
    p_traces: float
    p_exception: float


def unmentioned(p_forbid):
    """Answer for an allergen below DROP_THRESHOLD. build_parse_result drops it, so the other fields are never read."""
    return UnitAnswer(p_forbid, [0.0, 0.0, 1.0, 0.0], 1.0, 0.0)


def unit_questions(unit, index, confidence):
    """One-tap questions for every field of one constraint whose confidence is below the threshold."""
    name = UNIT_NAMES[unit]
    target = {"kind": "constraint", "index": index}
    questions = []
    if confidence["allergen_unit_id"] < QUESTION_THRESHOLD:
        questions.append(
            {
                "target": {**target, "field": "allergen_unit_id"},
                "prompt": f"Keep {name} off this order?",
                "options": [{"label": "Yes, keep it off", "value": unit}, {"label": "No", "value": None}],
            }
        )
    if confidence["severity"] < QUESTION_THRESHOLD:
        questions.append(
            {
                "target": {**target, "field": "severity"},
                "prompt": f"How serious is the {name} restriction?",
                "options": [
                    {"label": "Severe (EpiPen)", "value": "anaphylactic"},
                    {"label": "Allergy", "value": "allergy"},
                    {"label": "Intolerance", "value": "intolerance"},
                    {"label": "Preference", "value": "preference"},
                ],
            }
        )
    if confidence["trace_sensitive"] < QUESTION_THRESHOLD:
        questions.append(
            {
                "target": {**target, "field": "trace_sensitive"},
                "prompt": f"Do traces of {name} matter?",
                "options": [
                    {"label": "Yes, even traces", "value": True},
                    {"label": "No, just keep it out of the dish", "value": False},
                ],
            }
        )
    return questions


def build_parse_result(note, source, answers, parser_version):
    """Turn per-allergen answers into a ParseResult (contracts/schemas/parse_result.schema.json)."""
    constraints, exceptions, questions = [], [], []

    for unit in UNITS:
        answer = answers[unit]
        if answer.p_forbid < DROP_THRESHOLD:
            continue

        best = max(range(len(SEVERITIES)), key=lambda i: answer.severity_probs[i])
        confidence = {
            "allergen_unit_id": round(answer.p_forbid, 3),
            "severity": round(answer.severity_probs[best], 3),
            "trace_sensitive": round(max(answer.p_traces, 1.0 - answer.p_traces), 3),
        }
        index = len(constraints)
        constraints.append(
            {
                "allergen_unit_id": unit,
                "severity": SEVERITIES[best],
                "trace_sensitive": answer.p_traces >= 0.5,
                "confidence": confidence,
            }
        )
        questions += unit_questions(unit, index, confidence)

        # Laya can say an allowed ingredient was named, but not which one, so the note is the reference.
        # The ticket still forbids the whole allergen; the exception only lives in the draft.
        if answer.p_exception >= 0.5:
            exception_confidence = round(answer.p_exception, 3)
            exceptions.append(
                {"allergen_unit_id": unit, "ingredient": "named in the note", "confidence": exception_confidence}
            )
            if exception_confidence < QUESTION_THRESHOLD:
                questions.append(
                    {
                        "target": {"kind": "exception", "index": len(exceptions) - 1, "field": "ingredient"},
                        "prompt": f"The note may allow some {UNIT_NAMES[unit]}. Is any of it OK for this guest?",
                        # The answer is only recorded: the ticket forbids the whole allergen either way.
                        "options": [
                            {"label": "Yes, what the note says is OK", "value": True},
                            {"label": "No, avoid all of it", "value": False},
                        ],
                    }
                )

    for number, question in enumerate(questions, start=1):
        question["question_id"] = f"q{number}"

    return {
        # Unique per parse, so two guests with the same note never share a draft.
        "draft_id": f"d-{uuid.uuid4().hex[:10]}",
        "note": note,
        "source": source,
        "parser": {"name": "laya", "version": parser_version},
        "constraints": constraints,
        "exceptions": exceptions,
        "questions": questions,
        "question_threshold": QUESTION_THRESHOLD,
        "sendable": not questions,
    }


def require_text(text):
    """Refuse an empty note: an empty answer must never look like 'no allergens'."""
    if not isinstance(text, str) or not text.strip():
        raise LayaParserUnavailable("empty ticket note")


# ---------- the real model ----------


class LocalLayaParser:
    """LayaParser backed by our fine-tuned Laya checkpoint, loaded from a local folder."""

    def __init__(self, model_dir):
        try:
            import laya  # heavy (torch); only needed when the real model is used

            self._agent = laya.load(str(model_dir))
        except Exception as error:
            raise LayaParserUnavailable(f"could not load Laya from {model_dir}: {error}") from error
        self._version = Path(model_dir).name

    def _ask(self, state, questions):
        """One forward pass for all questions. Returns {key: P(yes) or [P per severity]}.

        Reading the answers happens inside the try, so a malformed answer fails closed too.
        """
        try:
            answers = self._agent.predict(state, questions)["answers"]
            return {key: read_answer(answers[key], question) for key, question in questions.items()}
        except Exception as error:
            raise LayaParserUnavailable(f"Laya failed to answer: {error}") from error

    def parse_note(self, note, *, source="typed"):
        """Ask whether the note forbids each allergen, then follow up only on the ones it might."""
        require_text(note)
        state = note_state(note, source)

        forbid = self._ask(state, {f"forbid_{u}": note_questions(u)[f"forbid_{u}"] for u in UNITS})
        flagged = [u for u in UNITS if forbid[f"forbid_{u}"] >= DROP_THRESHOLD]

        follow_ups = {}
        for unit in flagged:
            questions = note_questions(unit)
            for kind in ("severity", "traces", "exception"):
                follow_ups[f"{kind}_{unit}"] = questions[f"{kind}_{unit}"]
        details = self._ask(state, follow_ups) if follow_ups else {}

        answers = {}
        for unit in UNITS:
            if unit in flagged:
                answers[unit] = UnitAnswer(
                    p_forbid=forbid[f"forbid_{unit}"],
                    severity_probs=details[f"severity_{unit}"],
                    p_traces=details[f"traces_{unit}"],
                    p_exception=details[f"exception_{unit}"],
                )
            else:
                answers[unit] = unmentioned(forbid[f"forbid_{unit}"])
        return build_parse_result(note, source, answers, self._version)


# ---------- the fake, for tests and offline demos without the model ----------

KEYWORDS = {
    "milk": ["dairy", "milk", "cheese", "butter", "cream", "lactose", "whey", "ghee", "parmesan", "feta", "yogurt"],
    "egg": ["egg", "mayo", "aioli", "meringue"],
    "fish": ["fish", "anchovy", "anchovies", "salmon", "tuna", "cod"],
    "shellfish": ["shellfish", "shrimp", "prawn", "crab", "lobster", "crawfish"],
    "tree_nut": ["tree nut", "almond", "cashew", "walnut", "pecan", "pistachio", "hazelnut", "macadamia", "pine nut"],
    "peanut": ["peanut", "satay", "groundnut"],
    "wheat": ["wheat", "gluten", "flour", "bread", "pasta", "panko"],
    "soy": ["soy", "tofu", "edamame", "miso"],
    "sesame": ["sesame", "tahini"],
}

# Phrases that contain a keyword but don't mean that allergen (peanut butter isn't dairy).
NOT_DAIRY = ["peanut butter", "almond butter", "cocoa butter", "coconut milk", "oat milk", "almond milk", "soy milk"]


def mentions(text, unit):
    """True if a keyword for this allergen appears as a whole word (plurals allowed)."""
    if unit == "milk":
        for phrase in NOT_DAIRY:
            text = text.replace(phrase, "")
    return any(re.search(rf"\b{re.escape(word)}(s|es)?\b", text) for word in KEYWORDS[unit])


def bare_nuts(text):
    """True when "nut" or "nuts" appears on its own: it could mean tree nuts or peanuts, so both get asked.

    "peanuts" never matches the pattern; "tree nut" and "pine nut" are removed first because they name the kind.
    """
    for phrase in ("tree nut", "pine nut"):
        text = text.replace(phrase, "")
    return bool(re.search(r"\bnuts?\b", text))


def fake_unit_answer(text, unit):
    """Keyword rules standing in for Laya on one allergen. Unsure cases get middle probabilities."""
    if mentions(text, unit):
        p_forbid = 0.95
    elif unit in ("tree_nut", "peanut") and bare_nuts(text):
        p_forbid = 0.7  # below the question threshold, so the server is asked about each
    else:
        p_forbid = 0.02

    if any(cue in text for cue in ("epipen", "anaphyla", "severe", "deathly")):
        severity = [0.0, 0.0, 0.05, 0.95]
    elif any(cue in text for cue in ("intoleran", "sensitiv")):
        severity = [0.05, 0.9, 0.05, 0.0]
    elif "allerg" in text:
        severity = [0.0, 0.05, 0.88, 0.07]
    else:
        severity = [0.2, 0.1, 0.6, 0.1]  # no reason given: ask

    if any(cue in text for cue in ("traces are fine", "traces ok", "a little is fine")):
        p_traces = 0.05
    elif any(cue in text for cue in ("trace", "cross", "separate", "shared")):
        p_traces = 0.95
    else:
        # A real allergy: assume traces matter, sure enough to skip the question. Otherwise ask.
        p_traces = 0.85 if severity[2] + severity[3] >= 0.85 else 0.7

    return UnitAnswer(p_forbid, severity, p_traces, 0.0)


class FakeLayaParser:
    """LayaParser that uses keyword rules instead of the model. Same output shapes, no torch."""

    def parse_note(self, note, *, source="typed"):
        """Parse a note with keyword rules."""
        require_text(note)
        text = note.lower()
        answers = {u: fake_unit_answer(text, u) for u in UNITS}
        return build_parse_result(note, source, answers, "fake")
