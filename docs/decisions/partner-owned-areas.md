# The partner owns the interaction model, the graph and the data system

- **Status:** accepted
- **Date:** 2026-10-23
- **Decided by:** owner, director

## Context

The owner's partner, a human, is building the ConvLSTM interaction model and the entity graph and data system. Agents must not collide with that work or wait on it. The interactions step (pair windows, zone rules, the fallback wrapper, the adoption benchmark) is tightly coupled to the ConvLSTM, so it belongs on the same side of the boundary.

## Decision

- The partner owns `kitchen/entities/` (entity store and sync), `kitchen/interactions/` (pairs, features, clips, zone rules, edges), `kitchen/lstm/` (the ConvLSTM), `kitchen/graph/` (provenance graph and exact reachability) and `database/` (SpacetimeDB module and real `StateClient`). Agents never edit them, and CI fails any non-`partner/` branch that touches them.
- The boundary is the interfaces in `contracts/apis.md` (`EntityStore`, `InteractionClassifier`, `ProvenanceGraph`, `StateClient`). The platform team writes their fakes, plus contract tests the partner's real code must pass.
- Agents keep supplying what the partner's steps consume: frames and change masks (vision), the shared change-mask function and clip builder (platform), and training clips and scenario streams (sim).
- All Python is one package, `kitchen`, with one folder per pipeline step, and six agent teams: platform, orders, vision, verdict, sim, outputs.

## Consequences

- Agent tasks never block on the partner. Integration steps carry `needs-partner`.
- The verdict team tests the policy against `FakeProvenanceGraph`, which answers from scenario ground truth. The golden scenarios are the shared test both sides must pass.
- The blind-classifier and rules-fallback safety rules are the partner's to uphold in the interactions step; the schemas still enforce the blind classifier at the boundary.
- Changing a partner interface needs the partner's OK on the contracts PR.
