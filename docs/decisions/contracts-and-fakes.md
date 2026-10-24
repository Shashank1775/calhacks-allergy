# Contracts first, and every boundary has a fake

- **Status:** accepted
- **Date:** 2026-10-23
- **Decided by:** owner, director

## Context

The real pipeline needs hardware (Pi, Hailo, camera, servo), a paid API (Cartesia), a fine-tuned model (Laya) and a running database. Agents can't touch hardware, and waiting on other teams would serialize the whole build.

## Decision

- Every cross-team message has a JSON Schema in `contracts/schemas/`, with valid and invalid fixtures. Every cross-team call has an interface in `contracts/apis.md`.
- Every interface has a fake named `Fake<Thing>`. The producing team ships it; for the partner's components, the platform team writes it, and the partner's real code must pass the same contract tests. Every component's tests pass with all other components replaced by fakes.
- Contracts change in their own PR, which the Director reviews.

## Consequences

- Every agent team can start on day one, without waiting on the partner.
- Integration bugs move to contract boundaries, where fixtures and the end-to-end harness catch them.
- Contract churn is expensive by design. Get them right early, and add fields rather than change them.
