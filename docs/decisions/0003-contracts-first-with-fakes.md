# 0003: Contracts first, and every boundary has a fake

- **Status:** accepted
- **Date:** 2026-10-23
- **Deciders:** director, owner

## Context

The real pipeline needs hardware (Pi, Hailo, camera, servo), paid APIs (Cartesia, Jev, Laya) and a running database. Agents can't touch hardware, and waiting on other teams serializes the whole org.

## Decision

- Every cross-team message has a JSON Schema in `contracts/schemas/`, with valid and invalid fixtures. Every cross-team call has an interface in `contracts/apis.md`.
- The producing team ships a fake for each contract it produces. For the partner's components, the platform team writes the fake, and the partner's real code must pass the same contract tests. Every component's tests pass with all other components replaced by fakes.
- Contracts change in their own PR, which the Director merges.

## Consequences

- Every agent team can start on day one, without waiting on the partner.
- Integration bugs move to contract boundaries, where fixtures and the end-to-end harness catch them.
- Contract churn is expensive by design. Get them right early, and add fields rather than change them.
