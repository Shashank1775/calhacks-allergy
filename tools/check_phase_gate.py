#!/usr/bin/env python3
"""Fail a PR that adds product code while AGENTS.md says the phase gate is PLANNING.

While gated, only README.md and CLAUDE.md may change under components/ and libs/.

  python3 tools/check_phase_gate.py --diff origin/main
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATED_PREFIXES = ("components/", "libs/")
ALLOWED_NAMES = {"README.md", "CLAUDE.md", ".gitkeep"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", metavar="BASE", required=True)
    args = ap.parse_args()

    gate_line = next((l for l in (ROOT / "AGENTS.md").read_text().splitlines() if "Phase gate:" in l), "")
    if "BUILD APPROVED" in gate_line:
        print("phase gate: BUILD APPROVED")
        return 0

    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{args.diff}...HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    blocked = [f for f in changed if f.startswith(GATED_PREFIXES) and Path(f).name not in ALLOWED_NAMES]
    if blocked:
        print("FAIL: phase gate is PLANNING (see AGENTS.md); product code is not approved yet:")
        for f in blocked:
            print(f"    {f}")
        return 1
    print("phase gate: PLANNING, no product code in this diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
