"""Factory fixtures that point the shared InteractionClassifier contract suite at zone rules.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.zone_rules
Emits: make_classifier
Rules: zone rules run the same suite as FakeClassifier (kitchen/shared/contract_tests/interaction_classifier.py)
"""

import pytest

from kitchen.interactions.zone_rules import ZoneRules


@pytest.fixture
def make_classifier():
    """A new ZoneRules with default settings per call."""
    return ZoneRules
