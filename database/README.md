# database: SpacetimeDB module and real StateClient (partner)

**Owner:** the partner (human). Agents never edit this folder. This note tells you what the agents expect from it and what they're building around it.

## What lives here

- The SpacetimeDB module: every table, reducer and single-writer rule in [contracts/state-tables.md](../contracts/state-tables.md), seeded from [contracts/allergen_units.json](../contracts/allergen_units.json).
- The real Python [`StateClient`](../contracts/apis.md#stateclient), which every Python component uses to read and write the database.
- The generated TypeScript bindings that `web/` (team outputs) uses to subscribe to tables.
- A small shift CLI (`start_shift`), run by a person at shift start.

## What agents expect

- Every method raises `StateUnavailable` on connection loss, timeout or a rejected reducer, and never returns stale data as if it were current (invariant 1).
- `subscribe()` exposes the age of the last update, so the gate can go `fault` on a stale feed.
- Writes are idempotent by primary key. Each table has exactly one writer team, enforced by caller identity.
- Every ticket revision is kept. `upsert_entities` never deletes an entity and never changes `first_seen_t_ms` or `origin` (invariants 8, 9).
- `list_interactions` returns events in time order.
- Throughput: `upsert_entities` about 5 times a second with up to ~30 entities; `record_interactions` bursts up to ~20 a second.

These are checked by the `StateClient` contract suite in `kitchen/shared/contract_tests/` (platform). `InMemoryStateClient` is the reference behavior.

## Until it lands

Agents use `InMemoryStateClient` (platform, `kitchen/shared/fakes/`). Tasks that need the real database carry the `needs-partner` label. The web screens use a fake data source until your TS bindings exist.

## Handing off

- Open PRs from branches named `partner/<slug>`. CI lets those branches edit partner-owned folders.
- Any internal layout is fine.
- The tables and the `StateClient` interface in `contracts/` are the deal. Changing them means telling the Director (an RFC issue).

## What agents build around it

- **Every team** reads and writes through `StateClient`.
- **sim:** a load generator to measure your throughput (sim-10).
- **outputs:** the expo board, tablet and chef screens, on your TS bindings.
- **platform:** the fake, the contract suite, and the end-to-end harness.

## Unverified and open

- The current SpacetimeDB version and module API, and how a reducer reads the caller's identity, haven't been checked.
- The `spacetime` CLI and a Rust toolchain weren't installed on the owner's Mac.
- **OQ-1:** how Python reaches SpacetimeDB: HTTP reducer calls, a bridge process, or a community SDK. Your call.
- **OQ-7:** local standalone on the laptop or Maincloud (default: local).

See [AGENTS.md §11](../AGENTS.md#11-open-questions).
