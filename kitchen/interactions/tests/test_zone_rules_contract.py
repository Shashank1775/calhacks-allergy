"""The shared InteractionClassifier contract suite, run against zone rules (fixture in conftest.py).

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.shared.contract_tests.interaction_classifier
Emits: pytest tests marked contract
Rules: zone rules pass the same suite the fake passes
"""

from kitchen.shared.contract_tests.interaction_classifier import *  # noqa: F403
