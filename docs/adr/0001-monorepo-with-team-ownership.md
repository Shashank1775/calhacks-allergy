# 0001: One monorepo, one owner per path

- **Status:** accepted
- **Date:** 2026-10-23
- **Deciders:** director, owner

## Context

Eight teams of agents work in parallel on one product. Agents are fast and literal. Without hard boundaries they edit each other's files, collide in merges, and build hidden dependencies.

## Decision

- One repository. Each team owns exactly the paths listed for it in `OWNERS`. Longest prefix wins, and every tracked file has exactly one owner.
- Teams depend on each other only through `contracts/`.
- CI fails on unowned files and reports which owners a PR touches. A PR that touches more than one team's paths needs the `cross-team` label and the other Manager's approval.

## Consequences

- Integration changes are visible in one place, and the end-to-end harness can run everything from one checkout.
- Shared files (root `pyproject.toml`, `uv.lock`) are hotspots. Lockfiles are exempt side effects (WORKFLOW.md#lockfiles); everything else needs the platform team.
