"""Runs the StateClient contract suite against SpacetimeStateClient on a local SpacetimeDB (skips without one).

Owner: partner (written by an agent at the owner's request)
Step:  database
Reads: kitchen/shared/contract_tests/state_client.py; the fixtures in database/tests/conftest.py
Emits: nothing
Rules: the real client passes the same suite FakeStateClient passes
"""

from kitchen.shared.contract_tests.state_client import *  # noqa: F403
