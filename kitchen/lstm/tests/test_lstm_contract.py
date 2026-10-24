"""Runs the shared InteractionClassifier contract suite against LstmClassifier, through make_classifier in conftest.py.

Owner: partner (written by an agent at the owner's request)
Step:  LSTM classifier
Reads: kitchen/shared/contract_tests/interaction_classifier.py; the exported model (skips without it)
Emits: nothing
Rules: the LSTM passes the same suite as zone rules and FakeClassifier
"""

from kitchen.shared.contract_tests.interaction_classifier import *  # noqa: F403
