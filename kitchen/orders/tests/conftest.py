"""Shared fixtures for the orders tests: contract schema checks, an in-memory state that can fail on cue, a clock."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from kitchen.shared.interfaces import StateUnavailable

SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"


def schema_errors(name, document):
    """Every way `document` breaks contracts/schemas/<name>.schema.json (an empty list means it's valid)."""
    docs = {p.name: json.loads(p.read_text()) for p in SCHEMAS.glob("*.schema.json")}
    registry = Registry().with_resources((n, Resource.from_contents(d)) for n, d in docs.items())
    validator = Draft202012Validator(docs[f"{name}.schema.json"], registry=registry)
    return [error.message for error in validator.iter_errors(document)]


class DictState:
    """The three StateClient methods orders uses, kept in dicts. Set `down` to make every call raise."""

    def __init__(self):
        self.tickets = {}
        self.bin_profiles = {}
        self.down = False

    def _check(self):
        if self.down:
            raise StateUnavailable("database unreachable (test)")

    def put_ticket(self, ticket):
        self._check()
        self.tickets[ticket["ticket_id"]] = ticket

    def get_ticket(self, ticket_id):
        self._check()
        return self.tickets.get(ticket_id)

    def put_bin_profile(self, entity_id, profile):
        self._check()
        self.bin_profiles[entity_id] = profile


class FixedClock:
    """A clock that reads whatever t_ms the test sets."""

    def __init__(self, t_ms=0):
        self.t_ms = t_ms

    def now_ms(self):
        return self.t_ms


@pytest.fixture
def check_schema():
    return schema_errors


@pytest.fixture
def state():
    return DictState()


@pytest.fixture
def clock():
    return FixedClock(120_000)
