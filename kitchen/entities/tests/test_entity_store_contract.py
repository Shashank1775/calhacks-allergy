"""Runs the shared EntityStore contract suite against the real entity store, through the fixtures in conftest.py.

Owner: partner (written by an agent at the owner's request)
Step:  Entity database
Reads: kitchen/shared/contract_tests/entity_store.py
Emits: nothing
Rules: the real store passes the same suite as FakeEntityStore, so the agents' tests on the fake still hold
"""

from kitchen.shared.contract_tests.entity_store import *  # noqa: F403
