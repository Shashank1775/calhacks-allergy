# tests/e2e: end-to-end harness

**Owner:** team `platform` (agents). Task: platform-05 (M1).

Proves the pieces fit. It runs a whole kitchen through the system, with every component used only through its published interface ([`contracts/apis.md`](../../contracts/apis.md)), and checks the verdicts against known answers.

## What it runs

```
typed note ──▶ FakeLayaParser parse ──▶ scripted answers to questions ──▶ ticket (StateClient)
scenario file ──▶ bins, plate bindings, interaction events (StateClient)
plate reaches the pass ──▶ FakeProvenanceGraph ──▶ verdict policy (kitchen/verdict)
verdict ──▶ ConsoleGate + ConsoleSpeaker
```

- **Default:** everything on fakes: `InMemoryStateClient`, `FakeLayaParser`, `FakeProvenanceGraph`, `ConsoleGate`, `ConsoleSpeaker`, under a `FixedClock`.
- **With the partner's parts** (`needs-partner`): swap in the partner's real `StateClient` (local SpacetimeDB) and the real `ProvenanceGraph` with flags. The expected results are identical.

## Commands

```bash
uv run pytest tests/e2e                                  # every golden scenario + the typed-note flow, on fakes
uv run pytest tests/e2e --state real                     # partner's StateClient against a local SpacetimeDB
uv run pytest tests/e2e --graph real                     # partner's ProvenanceGraph instead of the fake
```

## What passing means

- For every file in [`contracts/scenarios/`](../../contracts/scenarios/), each plate's verdict matches `expected`: decision, allergens reached, reason codes, and chain event IDs where given.
- The typed-note flow cannot send a ticket with an unanswered question (invariant 3), and the stored ticket validates against `ticket.schema.json`.
- Two runs produce identical output (deterministic under `FixedClock`).
- The harness imports no team internals, only `kitchen.shared` interfaces and each folder's public entry point.

A failure here is a `type:bug` against the team whose output first diverges from `expected`. It is never fixed by editing the scenario.
