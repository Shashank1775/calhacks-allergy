"""Smoke test: `uv sync` installs the kitchen package and pytest finds it.

Owner: platform (agents)
Step:  shared
Reads: the installed kitchen package
Emits: nothing
Rules: none; this only proves the project and CI are wired up
Tasks: platform-01
"""

from pathlib import Path

import kitchen
import kitchen.shared

# This file is kitchen/shared/tests/test_package.py, so the repo root is three folders up.
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_kitchen_imports_from_this_checkout():
    """The editable install points at this repo's kitchen/ folder, so edits show up without reinstalling."""
    kitchen_dir = Path(kitchen.__file__).resolve().parent
    shared_dir = Path(kitchen.shared.__file__).resolve().parent

    assert kitchen_dir == REPO_ROOT / "kitchen"
    assert shared_dir == kitchen_dir / "shared"
