"""Reusable pytest suites, one per interface, that every implementation (fake or real) must pass.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.interfaces, contracts/fixtures, contracts/scenarios
Emits: suites for StateClient, EntityStore, ProvenanceGraph and InteractionClassifier; contract_fixture(), wait_until()
Rules: a suite tests behavior promised in contracts/apis.md and contracts/state-tables.md and nothing else; the fake and
       the real implementation run the same suite, so a disagreement is a bug in one of them

Each suite is a module of test functions marked `contract`. They take factory fixtures, which the folder running the
suite provides in its conftest.py:

  state_client.py            make_state_client() -> a new, empty StateClient
                             make_unavailable_state_client() -> a StateClient whose database can't be reached
  entity_store.py            make_entity_store() -> a new, empty EntityStore
  provenance_graph.py        make_graph(scenario) -> a ProvenanceGraph over that scenario's world
                             make_unavailable_graph(scenario) -> the same, but failing
  interaction_classifier.py  make_classifier() -> an InteractionClassifier

What each suite checks:
  StateClient            every method; writes idempotent by primary key; latest ticket revision; a plate binds once;
                         first_seen_t_ms and origin never change; entity history append-only; list_interactions order;
                         subscribe() delivers inserts and updates; StateUnavailable from every method when unreachable
  EntityStore            ingest() then get() and snapshot() reflect every frame; entities never deleted;
                         first_seen_t_ms and origin never change (known-clean source); start_sync() writes to the
                         given StateClient and stop_sync() stops it
  ProvenanceGraph        reach() returns valid AllergenReach records with time-forward chains (time runs forward);
                         every golden scenario's expected allergens and chains are found, and its expected passes stay
                         passable; unknown_ancestors() reports exactly the expected unknown-history entities
                         (known-clean source); GraphUnavailable on failure
  InteractionClassifier  name and version are set; classify() returns valid actions and probabilities

How a folder runs a suite against its own code: add the fixtures to that folder's tests/conftest.py, then a test file
that imports the suite, for example kitchen/graph/tests/test_graph_contract.py:

    from kitchen.shared.contract_tests.provenance_graph import *  # noqa: F403

and run `uv run pytest kitchen/graph -m contract`. Platform never edits partner folders; the partner never edits these
suites (ask platform in an issue).
"""

import json
import time

from kitchen.shared.models import CONTRACTS_DIR


def contract_fixture(schema: str, name: str) -> dict:
    """Read contracts/fixtures/valid/<schema>/<name>.json, a record the contracts promise is valid."""
    return json.loads((CONTRACTS_DIR / "fixtures" / "valid" / schema / f"{name}.json").read_text())


def wait_until(condition, timeout_s: float = 5.0) -> bool:
    """Poll `condition` until it is true or `timeout_s` passes, and return whether it came true.

    Real implementations deliver changes from background threads (a sync loop, a change-feed poll), so a suite waits
    for them instead of expecting them at once.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()
