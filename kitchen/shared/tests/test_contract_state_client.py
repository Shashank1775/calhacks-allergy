"""Runs the StateClient contract suite against FakeStateClient, through the fixtures in conftest.py.

Owner: platform (agents)
Step:  shared
Reads: kitchen/shared/contract_tests/state_client.py
Emits: nothing
Rules: the fake passes the same suite the partner's real client must pass
"""

from kitchen.shared.contract_tests.state_client import *  # noqa: F403
