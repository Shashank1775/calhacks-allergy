"""Tests for FakeClassifier: scripted labels per pair, a default for unscripted pairs, and failure on cue.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.fakes.classifier
Emits: nothing
Rules: rules stay as the fallback: the fake can fail, so the interactions wrapper's fallback can be tested
"""

import pytest

from kitchen.shared.fakes import FakeClassifier
from kitchen.shared.interfaces import PairWindow


def window(a_id, b_id):
    """A pair window with nothing in it: the fake only reads the pair's IDs. The tensor stays empty here."""
    return PairWindow(a_id=a_id, b_id=b_id, features=[], clip={}, tensor=None)


def test_a_scripted_pair_gets_its_labels():
    classifier = FakeClassifier({("tool-knife-1", "bin-1"): [("transfer.scoop", 0.95)]})

    assert classifier.classify(window("tool-knife-1", "bin-1")) == [("transfer.scoop", 0.95)]


def test_an_unscripted_pair_is_unimportant():
    classifier = FakeClassifier({("tool-knife-1", "bin-1"): [("transfer.scoop", 0.95)]})

    assert classifier.classify(window("bin-1", "tool-knife-1")) == [("unimportant", 1.0)]


def test_name_and_version_default_to_a_zone_rules_stand_in():
    classifier = FakeClassifier()
    assert (classifier.name, classifier.version) == ("zone_rules", "fake")
    assert FakeClassifier(name="lstm", version="0.2.0").name == "lstm"


@pytest.mark.safety
def test_set_unavailable_makes_classify_raise_until_turned_off():
    classifier = FakeClassifier()
    classifier.set_unavailable(True)
    with pytest.raises(RuntimeError):
        classifier.classify(window("tool-knife-1", "bin-1"))

    classifier.set_unavailable(False)
    assert classifier.classify(window("tool-knife-1", "bin-1")) == [("unimportant", 1.0)]
