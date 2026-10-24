#!/usr/bin/env python3
"""Fail a PR that adds product code while AGENTS.md says the phase gate is PLANNING.

While gated, agent-owned code folders may only change README.md and CLAUDE.md files.
Partner-owned paths (OWNERS) are exempt: the partner is a human already building.

  python3 tools/check_phase_gate.py --diff origin/main
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATED_PREFIXES = ("kitchen/", "web/", "database/", "tests/")
ALLOWED_NAMES = {"README.md", "CLAUDE.md", ".gitkeep"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", metavar="BASE", required=True)
    args = ap.parse_args()

    # The gate line reads "Phase gate: PLANNING." or "Phase gate: BUILD APPROVED."; only the word(s) right after
    # the colon count, because the PLANNING banner itself mentions BUILD APPROVED in its instructions.
    m = re.search(r"Phase gate:\*{0,2}\s*([A-Z][A-Z ]*[A-Z])", (ROOT / "AGENTS.md").read_text())
    state = m.group(1) if m else "PLANNING"
    if state == "BUILD APPROVED":
        print("phase gate: BUILD APPROVED")
        return 0

    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{args.diff}...HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    sys.path.insert(0, str(ROOT / "tools"))
    from check_ownership import load_owners, owner_of

    rules = load_owners()
    blocked = [
        f
        for f in changed
        if f.startswith(GATED_PREFIXES) and Path(f).name not in ALLOWED_NAMES and owner_of(f, rules) != "partner"
    ]
    if blocked:
        print("FAIL: phase gate is PLANNING (see AGENTS.md); product code is not approved yet:")
        for f in blocked:
            print(f"    {f}")
        return 1
    print("phase gate: PLANNING, no product code in this diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
