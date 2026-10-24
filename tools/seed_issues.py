#!/usr/bin/env python3
"""Create the initial backlog on GitHub from tools/backlog/*.json. Run once, after BUILD APPROVED.

Creates, in order: task issues (dependencies first, so "Blocked by #N" can be filled in),
one epic per team per milestone, one Team board per team, and the Program board.
Idempotent: every issue carries a hidden key; existing keys are skipped.

  python3 tools/seed_issues.py --dry-run     # validate and print the plan; touches nothing
  python3 tools/seed_issues.py               # create issues (needs bootstrap_github.sh first)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKLOG = ROOT / "tools" / "backlog"
TEAMS = ["platform", "intake", "state", "perception", "interactions", "provenance", "sim", "experience"]
MILESTONES = {
    "M0": "M0 Foundations",
    "M1": "M1 Vertical Slice",
    "M2": "M2 Live on Pi",
    "M3": "M3 Models",
    "M4": "M4 Demo Hardening",
}
FLAGS = {"safety-critical", "hardware", "good-first-task"}
KEY_RE = re.compile(r"<!-- key: ([a-z0-9-]+) -->")


def load_backlog() -> tuple[dict[str, dict], dict[str, list[dict]]]:
    tasks: dict[str, dict] = {}
    epics: dict[str, list[dict]] = {}
    problems = []
    for team in TEAMS:
        path = BACKLOG / f"{team}.json"
        if not path.exists():
            problems.append(f"missing {path.relative_to(ROOT)}")
            continue
        doc = json.loads(path.read_text())
        if doc.get("team") != team:
            problems.append(f"{path.name}: team must be {team!r}")
        epics[team] = doc.get("epics", [])
        for t in doc.get("tasks", []):
            key = t.get("key", "")
            if not key.startswith(f"{team}-"):
                problems.append(f"{path.name}: key {key!r} must start with {team}-")
            if key in tasks:
                problems.append(f"duplicate key {key}")
            if t.get("milestone") not in MILESTONES:
                problems.append(f"{key}: bad milestone {t.get('milestone')!r}")
            if t.get("type") not in ("task", "spike"):
                problems.append(f"{key}: type must be task or spike")
            if t.get("prio") not in ("p0", "p1", "p2"):
                problems.append(f"{key}: prio must be p0/p1/p2")
            if set(t.get("flags", [])) - FLAGS:
                problems.append(f"{key}: unknown flags {set(t['flags']) - FLAGS}")
            if not t.get("acceptance"):
                problems.append(f"{key}: no acceptance criteria")
            if len(t.get("title", "")) > 90:
                problems.append(f"{key}: title too long")
            t["team"] = team
            tasks[key] = t
    for key, t in tasks.items():
        for dep in t.get("blocked_by", []):
            if dep not in tasks:
                problems.append(f"{key}: blocked_by unknown key {dep}")
    if problems:
        sys.exit("backlog invalid:\n  " + "\n  ".join(problems))
    return tasks, epics


def topo_order(tasks: dict[str, dict]) -> list[str]:
    order, state = [], {}

    def visit(k: str, stack: list[str]) -> None:
        if state.get(k) == "done":
            return
        if state.get(k) == "visiting":
            sys.exit("dependency cycle: " + " -> ".join(stack + [k]))
        state[k] = "visiting"
        for dep in tasks[k].get("blocked_by", []):
            visit(dep, stack + [k])
        state[k] = "done"
        order.append(k)

    for k in sorted(tasks, key=lambda k: (tasks[k]["milestone"], TEAMS.index(tasks[k]["team"]), k)):
        visit(k, [])
    return order


def task_body(t: dict, numbers: dict[str, int | str]) -> str:
    deps = t.get("blocked_by", [])
    lines = [f"<!-- key: {t['key']} -->", "## Goal", t["goal"], "", "## Acceptance criteria"]
    lines += [f"- [ ] {a}" for a in t["acceptance"]]
    lines += ["", "## Contracts"] + ([f"- `{c}`" for c in t.get("contracts", [])] or ["- none"])
    lines += ["", "## Out of scope"] + ([f"- {o}" for o in t.get("out_of_scope", [])] or ["- (not specified)"])
    lines += ["", "## Dependencies"] + ([f"- Blocked by #{numbers[d]} ({d})" for d in deps] or ["- none"])
    lines += ["", f"_Seeded from `tools/backlog/{t['team']}.json`. Owner: team {t['team']}._", "", "— [director]"]
    return "\n".join(lines)


def team_board_body(team: str) -> str:
    return "\n".join([
        f"<!-- key: board-{team} -->",
        f"Team board for **{team}**. The Manager rewrites this body every cycle (see docs/agents/MANAGER.md).",
        f"Charter: `teams/{team}/CHARTER.md`", "",
        "## In flight", "_none_", "", "## Blocked", "_none_", "", "## Next up", "_none_", "",
        "## Decisions", "_none_", "", "## Risks", "_none_", "", "— [director]",
    ])


def program_board_body() -> str:
    return "\n".join([
        "<!-- key: board-program -->",
        "Program board. The Director rewrites this body every cycle (see docs/agents/DIRECTOR.md).", "",
        "## Phase gate", "See AGENTS.md.", "",
        "## Milestone status", "| Exit criterion | Status | Evidence |", "|---|---|---|", "", 
        "## Cross-team blockers", "_none_", "", "## Open RFCs", "_none_", "",
        "## Needs the owner", "_none_", "", "— [director]",
    ])


class GitHub:
    def __init__(self, repo: str | None, dry_run: bool):
        self.repo, self.dry_run, self.fake_n = repo, dry_run, 0

    def _gh(self, *args: str, stdin: str | None = None) -> str:
        cmd = ["gh", *args] + (["--repo", self.repo] if self.repo else [])
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=True).stdout

    def existing_keys(self) -> dict[str, int]:
        if self.dry_run:
            return {}
        out = self._gh("issue", "list", "--state", "all", "--limit", "2000", "--json", "number,body")
        keys = {}
        for issue in json.loads(out):
            m = KEY_RE.search(issue.get("body") or "")
            if m:
                keys[m.group(1)] = issue["number"]
        return keys

    def create(self, title: str, body: str, labels: list[str], milestone: str | None) -> int | str:
        if self.dry_run:
            self.fake_n += 1
            ms = f" [{milestone}]" if milestone else ""
            print(f"  would create: {title}{ms}  labels={','.join(labels)}")
            return f"NEW{self.fake_n}"
        args = ["issue", "create", "--title", title, "--body-file", "-"]
        for label in labels:
            args += ["--label", label]
        if milestone:
            args += ["--milestone", milestone]
        url = self._gh(*args, stdin=body).strip()
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        print(f"  created #{number}: {title}")
        return number


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo", help="owner/name (default: current repo)")
    args = ap.parse_args()

    tasks, epics = load_backlog()
    order = topo_order(tasks)
    gh = GitHub(args.repo, args.dry_run)
    numbers: dict[str, int | str] = dict(gh.existing_keys())

    print(f"tasks: {len(tasks)} across {len(TEAMS)} teams")
    for key in order:
        if key in numbers:
            continue
        t = tasks[key]
        labels = [f"team:{t['team']}", f"type:{t['type']}", f"prio:{t['prio']}",
                  "status:blocked" if t.get("blocked_by") else "status:ready", *t.get("flags", [])]
        numbers[key] = gh.create(t["title"], task_body(t, numbers), labels, MILESTONES[t["milestone"]])

    print("epics:")
    for team in TEAMS:
        for epic in epics.get(team, []):
            key = f"epic-{team}-{epic['milestone'].lower()}"
            if key in numbers:
                continue
            mine = [k for k in order if tasks[k]["team"] == team and tasks[k]["milestone"] == epic["milestone"]]
            body = [f"<!-- key: {key} -->", "## Goals"] + [f"- {g}" for g in epic.get("goals", [])]
            body += ["", "## Tasks"] + ([f"- [ ] #{numbers[k]} {tasks[k]['title']}" for k in mine] or
                                        ["_To be broken down by the Manager when this milestone starts._"])
            body += ["", "— [director]"]
            numbers[key] = gh.create(epic["title"], "\n".join(body),
                                     [f"team:{team}", "type:epic", "status:backlog"], MILESTONES[epic["milestone"]])

    print("boards:")
    for team in TEAMS:
        if f"board-{team}" not in numbers:
            gh.create(f"Team board: {team}", team_board_body(team), [f"team:{team}", "type:board"], None)
    if "board-program" not in numbers:
        gh.create("Program board", program_board_body(), ["type:board"], None)
    print("done" + (" (dry run, nothing created)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
