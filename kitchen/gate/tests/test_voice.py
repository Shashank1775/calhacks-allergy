"""Tests for the spoken-line formatter and FakeSpeaker.

Owner: outputs (agents)
Step:  gate and screens
Reads: kitchen.gate.voice, contracts/fixtures/valid/verdict/
Emits: nothing
Rules: none of the nine directly; voice is never on the path to HOLD
"""

import json
from pathlib import Path

import pytest

from kitchen.gate.voice import FakeSpeaker, allergen_words, number_words, spoken_line

VERDICTS = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "valid" / "verdict"


@pytest.mark.parametrize(
    ("n", "words"),
    [(0, "zero"), (7, "seven"), (13, "thirteen"), (20, "twenty"), (42, "forty two"), (105, "one oh five")],
)
def test_voice_format_numbers(n, words):
    assert number_words(n) == words


def test_voice_format_allergens():
    assert allergen_words(["peanut"]) == "peanut"
    assert allergen_words(["milk", "tree_nut"]) == "milk and tree nut"
    assert allergen_words(["egg", "soy", "sesame"]) == "egg, soy and sesame"


def test_voice_format_on_verdict_fixtures():
    hold = json.loads((VERDICTS / "hold-peanut.json").read_text())
    clean = json.loads((VERDICTS / "pass-clean.json").read_text())

    assert spoken_line(hold, 7) == "Hold seven, peanut"
    assert spoken_line(dict(hold, flagged=[]), 7) == "Hold seven"
    assert spoken_line(clean, 7) is None


def test_fake_speaker_records_and_prints(capsys):
    speaker = FakeSpeaker()

    speaker.say("Hold seven")

    assert speaker.lines == ["Hold seven"]
    assert capsys.readouterr().out == "[voice] Hold seven\n"
