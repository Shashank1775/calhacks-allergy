"""Command-line options for the end-to-end harness: which StateClient and ProvenanceGraph it runs on.

Owner: platform (agents)
Step:  end-to-end harness
Reads: pytest's command line
Emits: --state fake|real and --graph fake|real (default fake), read by tests/e2e/conftest.py
Rules: none of the nine; options live here because pytest registers them only from conftests it loads at startup,
       and this one is loaded for `uv run pytest` and for `uv run pytest tests/e2e` alike
"""


def pytest_addoption(parser):
    """Register --state and --graph. `real` selects the partner's implementation once it exists."""
    parser.addoption("--state", choices=["fake", "real"], default="fake", help="StateClient for tests/e2e")
    parser.addoption("--graph", choices=["fake", "real"], default="fake", help="ProvenanceGraph for tests/e2e")
