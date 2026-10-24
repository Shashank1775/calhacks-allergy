# Launching the org

For the owner and the Director. Nothing here runs while the phase gate says PLANNING, except the Director in planning mode.

Claude Code details below were checked against the Claude Code docs on 2026-10-23. Items marked *(experimental)* or *(unverified)* may change.

## One-time setup (owner)

1. Install tools: `gh` (logged in), `uv`, Node ≥ 20, and Rust plus the SpacetimeDB CLI for the state team.
2. Copy `.env.example` to `.env` and fill in the keys you have. Agents never see this file's contents.
3. Install the recommended Claude Code permissions so agents can run git, gh, uv and tests without a prompt for every command. Review the file first:
   ```bash
   cp tools/claude-settings.recommended.json .claude/settings.json
   ```
   Deny rules in settings can be bypassed with unusual command spellings, so the real guard for `main` is the GitHub ruleset that `tools/bootstrap_github.sh` creates.
4. Run `tools/bootstrap_github.sh` once: labels, milestones, and the `main` ruleset (requires PRs, blocks force-pushes).

## Shape of a running org

| Agent | How it runs | Lifetime |
|---|---|---|
| Director | One interactive session at the repo root: `claude --agent director` | Long-lived |
| Managers (8) | One session each, started by the owner or the Director (options below) | Long-lived; wake on a loop |
| Workers | Subagents a Manager spawns with the `worker` agent type, each in its own git worktree | One issue, then done |
| Reviewers | Subagents of the `reviewer` agent type, spawned per PR | One review, then done |

Nesting is Director → Manager → Worker at most, which fits Claude Code's default subagent depth (3).

## Starting Managers

Pick one option.

**A. Desktop app (simplest).** Open one Code session per team. Each gets its own worktree. Paste the Manager prompt below. Use the app's split view to watch several at once.

**B. CLI background sessions** *(research preview)*. Each gets its own worktree automatically:
```bash
claude --bg --agent team-manager --name mgr-perception "<Manager prompt for perception>"
claude agents                 # list running sessions
claude attach mgr-perception  # look in, steer
claude logs mgr-perception
claude stop mgr-perception
```
A background session never pushes to `main` or force-pushes. Idle, unattached sessions are stopped after about an hour, so keep long-lived Managers on a loop (below) or pin them.

**C. Headless one-shot runs** for batch work (for example a Manager grooming its backlog once):
```bash
claude -p "<prompt>" -w mgr-perception-groom --permission-mode acceptEdits --max-turns 60 --output-format json
```
`-p` runs don't clean up their worktree; remove it with `git worktree remove`. Whether `--agent` behaves the same under `-p` is *(unverified)*.

**Not recommended as the backbone:** Agent teams *(experimental)*. One team per session, no nesting, teammates share the lead's checkout, and it is interactive only. It's fine for a single Manager coordinating a few Workers on disjoint files.

## Keeping Managers awake

- In a long-lived Manager session: `/loop 20m Run one Manager cycle per docs/agents/MANAGER.md`. It only fires while that session is open and idle, and recurring loops expire after 7 days.
- Desktop scheduled tasks (Code tab → Scheduled) also work while the app is open and the Mac is awake. Tick "Worktree" so each run gets its own.
- Cloud `/schedule` routines are a poor fit here: one-hour minimum, fresh clone, and no GitHub issue triggers.
- The Mac must stay awake. Closing the lid sleeps it even with keep-awake on.

## Prompts

### Director

```
role=director handle=director
You are the Director of this repo. Read AGENTS.md and docs/agents/DIRECTOR.md, then run one Director cycle.
Concurrency cap for the whole org: <N> Workers. Phase gate: <PLANNING | BUILD APPROVED per owner on <date>>.
Report to me (the owner) only: decisions you need from me, milestone status, and anything at risk.
```

### Manager

```
role=manager team=<team> handle=mgr-<team>
You are the Manager of team <team>. Read AGENTS.md, docs/agents/MANAGER.md and teams/<team>/CHARTER.md,
then your Team board issue. Run Manager cycles. Max concurrent Workers: <k>.
Dispatch only status:ready issues in milestone <M>. Escalate to the Director on the Program board.
```

### Worker

Workers are started by their Manager with the prompt in [MANAGER.md](MANAGER.md#starting-a-worker).

### Safety reviewer

```
role=reviewer handle=safety
Review PR #<n> as the safety reviewer per docs/agents/REVIEWER.md. Do not edit code.
Post one review comment with numbered findings, then apply review:safety-approved or review:changes-requested.
```

## Suggested ramp

| Stage | Who runs | Workers |
|---|---|---|
| Planning (now) | Director only, with the owner | 0 |
| M0, first hours | All 8 Managers. Each dispatches only unblocked issues (spikes and skeletons first) | 1–2 per team |
| M0, once `platform-02`…`04` merge | Everyone at full speed | 2–3 per team |
| M1 onward | Scale teams on the critical path (usually provenance, state, platform) | Up to the owner's cap |

## Things that bite with many agents on one machine and one GitHub account

- **One identity.** Every comment looks like the owner wrote it. That's why signatures are mandatory.
- **`@claude` in GitHub text** can trigger the Claude GitHub Action if it's installed. Agents never write it.
- **Auto memory is shared** by every worktree of this repo on the Mac. Task state belongs in GitHub, not memory.
- **Worktrees pile up.** A Manager removes its Workers' worktrees after merge. Run `git worktree prune` weekly.
- **Subagent worktrees branch from `origin/main`,** not from the parent's branch. Workers always start from `main`.
- **Rate limits and budget.** Concurrency multiplies token spend. The owner sets the cap; Managers don't exceed it.
