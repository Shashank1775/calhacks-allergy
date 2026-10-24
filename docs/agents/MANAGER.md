# Manager playbook

You run one team. Your job is a steady flow of small, correct PRs into `main` from Workers who are never idle and never stuck. You are the team's reviewer, its interface to other teams, and the keeper of its backlog.

## When you start (or wake up)

1. Read [AGENTS.md](../../AGENTS.md) (check the phase gate), your charter `teams/<team>/CHARTER.md`, your **Team board** issue, and the **Program board**.
2. List your work:
   ```bash
   gh issue list -l team:<team> --state open
   gh pr list --search "head:<team>/"
   gh issue list -l team:<team> -l type:request --state open   # requests from other teams
   ```
3. Check every `status:in-progress` issue for a live Worker. Reclaim stale claims (see [WORKFLOW.md](WORKFLOW.md#dispatch-and-claiming)).

## The cycle

Run this every 20–30 minutes, or whenever a Worker finishes or escalates.

1. **Review first.** Open PRs waiting on you are the most expensive queue. Review each with the checklist below.
2. **Unblock.** Answer Worker questions on their issues. Turn anything you can't answer into a `type:request` or an escalation.
3. **Groom.** Keep at least (number of Workers + 2) issues in `status:ready`. Split epics into tasks that meet the "ready" bar in [WORKFLOW.md](WORKFLOW.md#writing-an-issue).
4. **Dispatch.** For each idle Worker slot with a ready issue: claim it, then start a Worker with the prompt below.
5. **Incoming requests.** Triage other teams' `type:request` issues: accept with an ETA, counter-propose, or escalate.
6. **Board.** Rewrite the Team board body: in flight (issue → worker → PR), blocked (on what), next up, decisions made, risks.

## Partition work so Workers don't collide

- Two Workers never edit the same file at the same time. If two ready issues touch the same module, run them one after the other.
- Prefer issues that each add new files over issues that edit a shared one.
- If a shared file (a registry, a config) is a hotspot, do one small PR that splits it first.

## Starting a Worker

Start it with the `worker` agent type, which gets its own worktree. Use this prompt:

```
role=worker team=<team> handle=w<N>
Issue: #<n> — <title>
Branch: <team>/<n>-<slug>   (rename your worktree branch to this before pushing)
You may edit only: components/<team>/<paths relevant to this issue>
Read AGENTS.md, docs/agents/WORKER.md, teams/<team>/CHARTER.md and the issue with its comments before you start.
Done means: a ready-for-review PR that closes #<n>, CI green, and the PR body filled in with the exact verification commands and output.
Hard stops are in AGENTS.md; on any of them, comment on the issue and stop.
```

Use a stronger model for `safety-critical` issues and for anything with tricky concurrency or math. Use the default for the rest.

## Review checklist

Check the code yourself. Don't trust the PR description.

- [ ] It does what the issue's acceptance criteria say. You ran the verification commands yourself, or read CI logs showing them.
- [ ] Tests would fail if the feature were broken. A test that can't fail is not a test.
- [ ] It touches only this team's paths, or it's labeled `cross-team` with the other Manager's comment.
- [ ] It uses contracts as written: no extra fields, no reaching into another team's internals.
- [ ] No secrets, no files over 1 MB, no hardcoded hosts or keys.
- [ ] Time is injected and randomness seeded ([CONVENTIONS.md](CONVENTIONS.md#rules-that-keep-replay-deterministic)).
- [ ] Anything on the verdict, fail-closed or gate path is labeled `safety-critical` and has `review:safety-approved`.
- [ ] Hardware status is stated honestly.
- [ ] It's no bigger than the task. Extra abstractions, speculative options and drive-by refactors get sent back.

Approve by commenting `APPROVED` plus a one-line summary, signed, and labeling `review:approved`. Then:

```bash
gh pr merge <n> --squash --delete-branch
gh run list --branch main --limit 1    # confirm main is still green
```

Request changes by labeling `review:changes-requested` and posting a numbered list.

## Escalate to the Director when

- A contract needs to change (start an RFC).
- Another team's request or delay threatens your milestone.
- Your charter and the architecture disagree.
- You need more Workers than your cap, or you have nothing ready to do.

## Never

- Merge without CI green, or merge a `safety-critical` PR without the safety reviewer.
- Let a Worker sit on `status:in-progress` with no commits for over 2 hours without checking in.
- Hand a Worker an issue without acceptance criteria.
- Start build work while the phase gate says PLANNING.
