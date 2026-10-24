"""Factory fixtures that point the shared EntityStore contract suite at the real entity store.

Owner: partner (written by an agent at the owner's request)
Step:  Entity database
Reads: kitchen.entities.store, kitchen.shared.clock
Emits: make_entity_store
Rules: the real store runs the same suite as FakeEntityStore (kitchen/shared/contract_tests/entity_store.py)
"""

import pytest

from kitchen.entities.store import EntityStore
from kitchen.shared.clock import FakeClock


@pytest.fixture
def make_entity_store():
    """A new, empty EntityStore on a FakeClock per call."""
    return lambda: EntityStore(FakeClock())
