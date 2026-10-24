"""Runs the InteractionClassifier contract suite against FakeClassifier, through the fixtures in conftest.py.

Owner: platform (agents)
Step:  shared
Reads: kitchen/shared/contract_tests/interaction_classifier.py
Emits: nothing
Rules: the fake passes the same suite the partner's zone rules and ConvLSTM must pass
"""

from kitchen.shared.contract_tests.interaction_classifier import *  # noqa: F403
