# 0005: The partner owns the entity graph and data system and the LSTM; code is laid out by pipeline step

- **Status:** accepted
- **Date:** 2026-10-23
- **Deciders:** owner, director

## Context

The owner's partner, a human, is building the LSTM and the entity graph/data system. Agents must not collide with that work or wait on it. The first layout (`components/<team>/` with team names like `intake`, `perception`, `provenance` and `experience`) also wasn't intuitive to find things in.

## Decision

- The partner owns `kitchen/entities/` (entity store and 5 Hz sync), `kitchen/lstm/` (LSTM classifier), `kitchen/graph/` (provenance graph and exact reachability) and `database/` (SpacetimeDB module and real `StateClient`). Agents never edit them, and CI fails any non-`partner/` branch that touches them.
- The boundary is the interfaces in `contracts/apis.md` (`EntityStore`, `ProvenanceGraph`, `InteractionClassifier`, `StateClient`). The platform team writes fakes for them, plus contract tests that the partner's real code must pass.
- All Python is one package, `kitchen`, with one folder per pipeline step: orders, profiling, vision, entities, interactions, lstm, graph, verdict, gate, sim and shared. Screens are in `web/`. Each folder has a README, and each file starts with a docstring giving its owner, inputs, outputs and rules.
- Agent teams are renamed to match their folders: platform, orders, vision, interactions, verdict, sim, outputs.
- All agent guidance is consolidated into one file, `AGENTS.md`.

## Consequences

- Agent tasks never block on the partner. Integration steps carry `needs-partner`.
- The verdict team builds and tests the policy against `FakeProvenanceGraph`, which answers from scenario ground truth. The golden scenarios are the shared test both sides must pass.
- Changing a partner interface needs the partner's ACK on the RFC.
