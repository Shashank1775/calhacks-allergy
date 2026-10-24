# 0006: The interactions step moves to the partner

- **Status:** accepted
- **Date:** 2026-10-23
- **Deciders:** owner, director

## Context

The partner is building the ConvLSTM interaction model, which sits inside the interactions step. Splitting pair windows, zone rules, the fallback wrapper and the adoption benchmark across agents and the partner would put one tightly coupled step on two sides of a boundary.

## Decision

- `kitchen/interactions/` is partner-owned, alongside `kitchen/entities/`, `kitchen/lstm/`, `kitchen/graph/` and `database/`. Agents never edit it, and CI enforces this.
- The agent team `interactions` is dissolved. Six agent teams remain: platform, orders, vision, verdict, sim, outputs.
- Its planned tasks become a suggested list for the partner (GitHub issues relabeled `owner:partner`).
- Agents keep supplying what the step consumes: frames and change masks (vision), the shared change-mask function and clip builder (platform), and training clips and scenario streams (sim).

## Consequences

- No agent task waits on interactions. The end-to-end harness uses scenario event streams instead of live edges.
- Invariants 6 and 7 are now the partner's to uphold in this step. The schemas still enforce invariant 6 at the boundary.
