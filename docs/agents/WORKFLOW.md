# Workflow

How work moves from an idea to `main`. Every agent follows this. Your role's playbook says which parts are yours.

## Labels

Created by `tools/bootstrap_github.sh`. Every issue has exactly one `team:`, one `type:` and one `status:` label, plus a `prio:` label once it has been triaged.

| Group | Labels | Meaning |
|---|---|---|
| Team | `team:platform` `team:intake` `team:state` `team:perception` `team:interactions` `team:provenance` `team:sim` `team:experience` | Who owns the work |
| Type | `type:task` | A unit of work for one Worker |
| | `type:epic` | A milestone-sized goal; its checklist links tasks |
| | `type:bug` | Something is wrong on `main` |
| | `type:spike` | Time-boxed research; output is a written answer, not production code |
| | `type:request` | One team asking another for something |
| | `type:rfc` | Proposed contract or architecture change |
| | `type:question` | Needs a decision from the Director or the owner |
| | `type:board` | A pinned status board (one per team, plus the Program board) |
| Status | `status:backlog` | Not ready: missing acceptance criteria, or blocked by design |
| | `status:ready` | Ready to dispatch |
| | `status:in-progress` | A Worker has it (see the claim comment) |
| | `status:review` | PR open and ready for review |
| | `status:blocked` | Waiting on something named in a comment |
| | `status:needs-human` | Only the owner can unblock it |
| Priority | `prio:p0` `prio:p1` `prio:p2` | p0: blocks the current milestone's demo. p1: needed this milestone. p2: nice to have |
| Flags | `safety-critical` | On the verdict, fail-closed or gate path. Needs the safety reviewer |
| | `hardware` | Needs the Pi, camera, Hailo, servo or speaker. Only a human can verify it |
| | `contract-change` | Touches `contracts/` |
| | `cross-team` | PR touches another team's paths, with that team's agreement |
| | `good-first-task` | Small, self-contained, good for warming up a new Worker |
| Review | `review:approved` | The owning Manager approved |
| | `review:safety-approved` | The safety reviewer approved |
| | `review:changes-requested` | Back to the Worker |

## Issue lifecycle

```
backlog ──triage──▶ ready ──dispatch──▶ in-progress ──PR ready──▶ review ──merge──▶ closed
                      ▲                    │    ▲                    │
                      │                    ▼    │                    ▼
                      └────────────────  blocked / needs-human ◀── changes-requested
```

- Only Managers (and the Director) move issues to `ready` and dispatch them.
- A Worker moves its own issue to `review` and `blocked`.
- Merging the PR closes the issue (`Closes #N` in the PR body).

## Writing an issue

Use the `Task` template. An issue is `ready` only when it has all of these:

- **Goal:** one or two sentences on the outcome, not the method.
- **Acceptance criteria:** a checklist a reviewer can verify. Include the command that proves each item.
- **Contracts:** the schemas and interfaces it produces or consumes.
- **Out of scope:** what not to do. Agents over-build; say where to stop.
- **Dependencies:** `Blocked by #N` lines, or "none".
- **Size:** fits in one PR of ~400 lines or less. If not, split it.

## Dispatch and claiming

Every agent uses the same GitHub account, so assignees cannot tell agents apart. Claims are made with a label plus a comment.

1. The Manager picks a `status:ready` issue and checks that it is not already `status:in-progress`.
2. The Manager swaps the label to `status:in-progress` and posts the claim:
   ```
   CLAIM worker=w2 branch=perception/42-bytetrack-wrapper
   — [manager/perception]
   ```
3. The Manager starts the Worker with the issue number and the branch name.
4. The Worker confirms on the issue in its first message: `STARTED in worktree <path>`.

A Worker never claims work for itself. A claim with no commits and no comment for 2 hours is stale; the Manager may reclaim it after commenting `RECLAIM`.

## Branches and worktrees

- Branch name: `<team>/<issue>-<short-slug>`, for example `provenance/57-noisy-or-engine`. Contract branches: `contracts/<rfc-issue>-<slug>`.
- One agent, one worktree, one branch. Claude Code worktrees live in `.claude/worktrees/`. Elsewhere, use `git worktree add ../wt/<branch> -b <branch> origin/main`.
- Rebase on `origin/main` before asking for review, and again if `main` moved under a conflicting change. Use `git push --force-with-lease`, and only on your own branch.
- Remove your worktree after merge: `git worktree remove <path>`.

## Lockfiles

The root `uv.lock` (and any `package-lock.json`) is shared. Adding a dependency to **your own** component's `pyproject.toml` is in your lane, and the resulting lockfile change is allowed in any team's PR. Never hand-merge a lockfile. On conflict, take `main`'s version, re-run `uv lock` (or `npm install`), and commit the result.

## Commits

```
<team>: <imperative summary under 72 chars> (#<issue>)

<why, if not obvious from the summary>

Agent: worker/<team>/w2
```

Keep each commit buildable. Add whatever co-author trailer your harness requires after the `Agent:` line.

## Pull requests

- Open a **draft** PR as soon as you have a first commit, so the Manager can see progress.
- Fill in every section of the PR template: `Closes #N`, what changed, **how you verified it** (exact commands and trimmed output), contract impact, safety impact, hardware status.
- Touch only your team's paths. If you must touch another team's path, stop. Either split that part into a `type:request`, or get the other Manager to agree on the issue first and label the PR `cross-team`.
- CI must be green: contract validation, ownership check, lint and tests.
- Mark ready (`gh pr ready`) and set the issue to `status:review`.

## Review and merge

| PR touches | Needs before merge | Merged by |
|---|---|---|
| One team's paths | `review:approved` from that team's Manager | That Manager |
| Anything `safety-critical` | Plus `review:safety-approved` | The owning Manager |
| Another team's paths (`cross-team`) | Plus a comment approving it from the other team's Manager | The owning Manager |
| `contracts/` | An RFC issue with the affected Managers' comments | The Director |
| `docs/`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `OWNERS` | The Director's review | The Director |

- GitHub won't let an account approve its own PR, so approvals are a signed review comment plus a `review:*` label.
- Merge with **squash** only. Delete the branch after merge.
- A reviewer who finds a problem labels `review:changes-requested` and lists the problems as a numbered checklist. The Worker fixes each one and replies to each number.

## Keeping main green

`main` must always build, pass CI, and run the current demo path with fakes.

- If `main` breaks, whoever notices opens a `type:bug` `prio:p0` issue and pings the Program board. The team that broke it reverts first and fixes second.
- Nothing else merges to `main` until it is green again, except the fix.

## Cross-team requests

When you need something from another team (a field, a fake, a bug fix, a behavior):

1. Open a `type:request` issue with **their** `team:` label. Say what you need, why, and by when (which milestone).
2. Link it from your blocked issue (`Blocked by #N`) and set yours to `status:blocked`.
3. Their Manager triages it like any other issue, and replies with an ETA or a counter-proposal.
4. Two teams that can't agree escalate to the Director on the request issue.

Never work around a missing dependency by copying the other team's code into your tree.

## Contract changes (RFCs)

1. Open a `type:rfc` issue (template provided): the change, the motivation, who produces and consumes it, whether it's breaking, and the migration path.
2. Tag every affected team with its `team:` label. Each affected Manager comments `ACK`, `ACK with changes: ...` or `NACK: reason`.
3. Once there are no open NACKs, the proposer opens a PR on a `contracts/<rfc>-<slug>` branch touching only `contracts/` (and `docs/adr/` if behavior changes). Bump `contracts/VERSION`. Label it `contract-change`.
4. The Director reviews and merges, then comments on each affected Team board with the new version and what consumers must do.
5. Consumers update in their own PRs. Breaking changes keep the old shape working for one milestone when that's feasible.

## Architecture decisions (ADRs)

A decision that's hard to reverse or affects more than one team gets an ADR in `docs/adr/NNNN-<slug>.md` (copy [0000-template.md](../adr/0000-template.md)). Team-internal decisions go in an ADR too, prefixed with the team name in the title. Proposed ADRs are PRs; the Director merges them.

## Boards and status

- **Team board:** one pinned `type:board` issue per team, titled `Team board: <team>`. The Manager edits the **issue body** (not comments) at the end of every cycle: in flight, blocked, next up, decisions, risks. Edit the body, don't append.
- **Program board:** one pinned `type:board` issue owned by the Director. Milestone status, cross-team blockers, open RFCs, `needs-human` list, and the phase gate.
- Boards are the first thing a newly started Manager or Director reads.

## Escalation

| Situation | Goes to | How |
|---|---|---|
| Worker stuck after two attempts, unclear requirement | Manager | Comment on the issue, `status:blocked` |
| Needs another team | Other team's Manager | `type:request` issue |
| Teams disagree, contract change, scope change, milestone at risk | Director | Comment on the Program board, linking the issue |
| Product decision, keys, money, hardware, legal or safety judgment | Owner | `status:needs-human` and a one-paragraph question with options and a recommendation |

## Hardware in the loop

Agents cannot touch the Pi, camera, Hailo, servo, lights, buzzer or speaker.

- Every hardware-facing module has a software backend that runs on a laptop: video file in place of the camera, CPU inference in place of Hailo, console in place of GPIO.
- Hardware-only code paths are behind an interface and covered by tests against the fake.
- Issues that need a real device carry `hardware`. A Worker takes them as far as the fake allows, then writes a **hardware test script** on the issue: exact steps and expected output for the owner. The issue moves to `status:needs-human` until the owner posts results.
- PRs state hardware status: `not hardware`, `fake only`, or `verified on device by owner (link)`.

## Data and large files

- Nothing over 1 MB goes into git. No video, model weights, datasets, ONNX/HEF files, or recorded sessions.
- Large artifacts live outside the repo under `data/` (gitignored), with a small manifest in your component (`data-manifest.json`: name, size, sha256, where to get it, who made it).
- Small test fixtures (a few KB of JSONL frames or events) are fine in git under your component's `tests/fixtures/`.

## Secrets

- Real keys live only in the local `.env` (gitignored). `.env.example` lists every variable with a placeholder.
- Never echo a key in a command, log, PR or issue. If you need a key you don't have, file `status:needs-human`.
- Code reads keys from the environment and fails with a clear message when one is missing. It never falls back to a hardcoded value.
