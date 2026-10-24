"""Fixtures that point the StateClient contract suite at SpacetimeStateClient on a local SpacetimeDB.

Owner: partner (written by an agent at the owner's request)
Step:  database
Reads: SPACETIMEDB_URI (default http://localhost:3000) and SPACETIMEDB_TEST_DB (default allergen-yhz81-test)
Emits: make_state_client, make_unavailable_state_client
Rules: every client starts from an empty database (start_shift), as each shift does; with no server or no test
       database the suite skips, which is what CI sees

    spacetime start                                                    # once
    spacetime publish allergen-yhz81-test --server local --yes        # from database/allergen-yhz-81
"""

import os

import httpx
import pytest

from database.state_client import SpacetimeStateClient

URI = os.environ.get("SPACETIMEDB_URI", "http://localhost:3000")
TEST_DB = os.environ.get("SPACETIMEDB_TEST_DB", "allergen-yhz81-test")


@pytest.fixture(scope="session")
def test_database():
    """The URI and name of a published test database, or a skip saying how to get one."""
    try:
        httpx.get(f"{URI}/v1/ping", timeout=1.0).raise_for_status()
        found = httpx.get(f"{URI}/v1/database/{TEST_DB}", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        pytest.skip(f"no SpacetimeDB at {URI}: run `spacetime start` (database/tests/conftest.py)")
    if not found:
        pytest.skip(f"no database {TEST_DB}: publish the module as {TEST_DB} (database/tests/conftest.py)")
    return URI, TEST_DB


@pytest.fixture
def make_state_client(test_database):
    """A factory for clients on the test database, emptied first. Closes them after the test."""
    uri, name = test_database
    clients = []

    def make():
        client = SpacetimeStateClient(uri, name, poll_s=0.05)
        client.start_shift()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.fixture
def make_unavailable_state_client():
    """A client whose database can't be reached: nothing listens on port 9 (discard)."""
    return lambda: SpacetimeStateClient("http://127.0.0.1:9", "allergen-yhz81", timeout_s=0.5)
