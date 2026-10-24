#!/usr/bin/env python3
"""Check that every file has exactly one owner (see OWNERS), and report which owners a branch touches.

  python3 tools/check_ownership.py                    # fail if any tracked file is unowned
  python3 tools/check_ownership.py --diff origin/main [--branch NAME]
      # list owners touched by this branch; fail if an agent branch touches partner paths
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "data", "worktrees"}
# Shared files any team may change as a side effect of its own work (WORKFLOW.md#lockfiles).
SHARED_SIDE_EFFECT_FILES = {"uv.lock"}


def load_owners() -> list[tuple[str, str]]:
    rules = []
    for raw in (ROOT / "OWNERS").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        path, owner = line.split()[:2]
        rules.append((path, owner))
    return rules


def owner_of(path: str, rules: list[tuple[str, str]]) -> str | None:
    best: tuple[int, str] | None = None
    for prefix, owner in rules:
        hit = path.startswith(prefix) if prefix.endswith("/") else path == prefix
        if hit and (best is None or len(prefix) > best[0]):
            best = (len(prefix), owner)
    return best[1] if best else None


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def tracked_files() -> list[str]:
    try:
        return git("ls-files")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # not a git checkout yet: walk the tree
        files = []
        for p in ROOT.rglob("*"):
            if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS):
                files.append(p.relative_to(ROOT).as_posix())
        return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", metavar="BASE", help="report owners touched since merge-base with BASE")
    ap.add_argument("--branch", help="head branch name (default: current branch); partner/* may touch partner paths")
    args = ap.parse_args()
    rules = load_owners()

    if args.diff:
        changed = git("diff", "--name-only", f"{args.diff}...HEAD")
        by_owner: dict[str, list[str]] = defaultdict(list)
        for f in changed:
            by_owner[owner_of(f, rules) or "UNOWNED"].append(f)
        for owner in sorted(by_owner):
            print(f"{owner}: {len(by_owner[owner])} file(s)")
            for f in by_owner[owner]:
                print(f"    {f}")
        teams = {owner_of(f, rules) or "UNOWNED" for f in changed if f not in SHARED_SIDE_EFFECT_FILES} - {"director", "partner"}
        if "contracts/" in {f[:10] for f in changed}:
            print("NOTE: touches contracts/ -> needs an RFC and the Director must merge (label contract-change)")
        if len(teams) > 1:
            print(f"NOTE: touches {len(teams)} owners -> label cross-team and get each other owner's approval")
        branch = args.branch or git("rev-parse", "--abbrev-ref", "HEAD")[0]
        if "partner" in by_owner and not branch.startswith("partner/"):
            print(f"FAIL: branch {branch!r} touches partner-owned paths; agents never edit them (AGENTS.md section 4)")
            return 1
        if "UNOWNED" in by_owner:
            print("FAIL: unowned files; add them to OWNERS (Director merges)")
            return 1
        return 0

    unowned = [f for f in tracked_files() if owner_of(f, rules) is None]
    if unowned:
        print("FAIL: files with no owner in OWNERS:")
        for f in unowned:
            print(f"    {f}")
        return 1
    print(f"ownership OK: {len(tracked_files())} files, {len({o for _, o in rules})} owners")
    return 0


if __name__ == "__main__":
    sys.exit(main())
