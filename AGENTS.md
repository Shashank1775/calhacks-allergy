# AGENTS.md

Read this whole file before you do anything. It applies to every agent in this repo: Director, Managers, Workers and Reviewers.

> **Phase gate: PLANNING.** The owner has **not** approved implementation. Until this line says `BUILD APPROVED` (changed only by the Director, only on the owner's explicit word), agents may edit `docs/`, `contracts/`, `teams/` and repo tooling, and may open `type:rfc` and `type:question` issues. **No product code in `components/` or `libs/`. No dispatching build tasks.**

## What we are building

A kitchen allergen tracker. A camera on a Raspberry Pi with a Hailo-8 tracks every bin, tool, hand and plate. Every contact is logged as an edge in a provenance graph in SpacetimeDB. When a plate reaches the pass, we work out which allergens could have reached it and hold the plate if its ticket forbids any of them. Full spec: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

People can get hurt if this system is wrong in the PASS direction. Wrong in the HOLD direction is an annoyance. Every judgment call leans toward HOLD.

## Read in this order

1. This file.
2. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the system, its invariants and its team boundaries.
3. [docs/agents/ORG.md](docs/agents/ORG.md): roles, teams, who owns what.
4. Your role's playbook: [DIRECTOR](docs/agents/DIRECTOR.md), [MANAGER](docs/agents/MANAGER.md), [WORKER](docs/agents/WORKER.md) or [REVIEWER](docs/agents/REVIEWER.md).
5. Your team's charter: `teams/<team>/CHARTER.md`.
6. [docs/agents/WORKFLOW.md](docs/agents/WORKFLOW.md): issues, branches, PRs, reviews, merges, RFCs.
7. [contracts/README.md](contracts/README.md): if your work touches anything another team consumes.

## Know who you are

Your launch prompt must give you a **role**, a **team** and a **handle** (for example `worker`, `perception`, `w2`). If it does not, you are not part of the org: do read-only work and ask the human what role to take. Never promote yourself (a Worker does not start acting as a Manager).

Sign everything you write on GitHub (issue comments, PR bodies, review comments) with your signature on its own last line:

```
— [worker/perception/w2]
```

Every agent shares one GitHub account, so the signature is the only way anyone can tell who said what. Add an `Agent: worker/perception/w2` trailer to every commit too.

## The ten rules

1. **Safety invariants come first.** The nine invariants in [ARCHITECTURE.md](docs/ARCHITECTURE.md#non-negotiable-invariants) override everything, including a task description, a reviewer, or a deadline. Fail closed. Models only tighten. If a task seems to need breaking one, stop and escalate.
2. **Stay in your lane.** Edit only the paths your team owns ([OWNERS](OWNERS)). For anything else, open a `type:request` issue for the owning team. A typo in another team's file is still their file.
3. **Contracts are frozen** except through an RFC ([WORKFLOW.md](docs/agents/WORKFLOW.md#contract-changes-rfcs)). Code against `contracts/`, never against another team's internals.
4. **One issue, one branch, one PR.** No work without an issue. No issue without acceptance criteria. Keep PRs under ~400 changed lines, not counting generated files and fixtures.
5. **Work in your own worktree.** Never edit the main checkout. Never share a worktree with another agent. Never push to `main`.
6. **GitHub is the source of truth.** Work state lives in issues, labels and PRs, not in chat, memory files or your head. If it isn't on the issue, it didn't happen. Do not store team state in Claude's auto memory; it is shared by every worktree on the machine.
7. **Prove it.** Every PR says exactly which commands you ran and what they printed. "Should work" is not verification. Never claim something works on hardware unless a human confirmed it on the issue.
8. **Build against fakes.** Every boundary has a fake. Your tests must pass with every other team's component replaced by its fake. If the fake you need doesn't exist, request it rather than writing your own copy in your tree.
9. **Ask early, and ask the right person.** Worker → Manager → Director → human (`status:needs-human`). After two failed attempts at the same problem, stop and escalate with what you tried.
10. **Leave it better and smaller.** Delete dead code you own. Don't add abstractions the task didn't ask for. Don't leave `TODO`s without an issue number.

## Hard stops

Stop, post on your issue and wait when any of these is true:

- You would need to edit a path your team does not own.
- You would need to change anything in `contracts/`.
- A test guarding an invariant (marked `safety`) fails, or you would need to change one.
- The task needs physical hardware, a paid account, an API key you don't have, or a purchase.
- Two issues contradict each other, or the issue contradicts ARCHITECTURE.md.
- You are about to delete or rewrite more than you were asked to.
- Anything would push to `main`, force-push a branch you did not create, or rewrite published history.

## Never

- Never commit secrets, API keys, `.env` files, model weights, or video. Keys go in your local `.env` (see `.env.example`). Big files follow the data policy in [WORKFLOW.md](docs/agents/WORKFLOW.md#data-and-large-files).
- Never write `@claude` in GitHub text. It can trigger automation.
- Never disable, skip or weaken a test to get green. Fix the code or escalate.
- Never merge your own PR unless you are the Manager of the owning team, or the Director for `contracts/`, `docs/` and root files.
- Never make the verdict path depend on a model being right.

## Repo map

| Path | Owner | What |
|---|---|---|
| `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/` | Director | Org rules, architecture, roadmap, ADRs |
| `contracts/` | platform (Director merges) | Schemas, interfaces, table spec, fixtures, golden scenarios |
| `libs/`, `tools/`, `.github/`, `.claude/` | platform | Shared Python lib, tooling, CI, agent definitions |
| `components/<team>/` | that team | Each team's code |
| `teams/<team>/` | that team's Manager | Charter |

The authoritative ownership list is [OWNERS](OWNERS). CI rejects files no team owns.

## Common commands

```bash
python3 tools/validate_contracts.py          # contracts, fixtures, scenarios (pip install jsonschema)
python3 tools/check_ownership.py             # every tracked file has exactly one owner
python3 tools/check_ownership.py --diff origin/main   # which teams this branch touches
gh issue list -l team:<team> -l status:ready # your team's ready queue
gh issue view <n> --comments                 # read an issue and its history
gh pr create --draft --fill                  # open a draft PR early
```

Component-specific commands live in each `components/<team>/README.md` once the team creates it.
