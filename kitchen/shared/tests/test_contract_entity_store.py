"""Runs the EntityStore contract suite against FakeEntityStore, through the fixtures in conftest.py.

Owner: platform (agents)
Step:  shared
Reads: kitchen/shared/contract_tests/entity_store.py
Emits: nothing
Rules: the fake passes the same suite the partner's real entity store must pass
"""

from kitchen.shared.contract_tests.entity_store import *  # noqa: F403
