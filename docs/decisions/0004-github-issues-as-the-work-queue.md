# 0004: GitHub issues are the work queue and the source of truth

- **Status:** accepted
- **Date:** 2026-10-23
- **Deciders:** director, owner

## Context

Dozens of agents start and stop across sessions. Chat context and local memory don't survive or don't get shared, and every agent uses the same GitHub account.

## Decision

- All work lives in GitHub issues with `team:`, `type:`, `status:` and `prio:` labels and milestones. PRs close issues.
- Managers dispatch: a label change plus a signed `CLAIM` comment. Workers never self-assign.
- Status lives in pinned board issues whose bodies the Manager or Director rewrites each cycle.
- Every comment and PR is signed with the agent's role, team and handle.

## Consequences

- Any agent can be restarted and recover its state from GitHub.
- No GitHub Projects board (the token lacks the `project` scope). Labels and milestones are enough.
- Approvals are labels plus signed comments, since an account can't approve its own PR.
