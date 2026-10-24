# database: SpacetimeDB module and real StateClient (partner)

**Owner:** the partner (human). Agents never edit this folder. This note tells you what the agents expect from it and what they're building around it.

## What lives here

- The SpacetimeDB module, in [`allergen-yhz-81/`](allergen-yhz-81/): the Rust code is [`spacetimedb/src/lib.rs`](allergen-yhz-81/spacetimedb/src/lib.rs), and [`spacetime.json`](allergen-yhz-81/spacetime.json) publishes it as the local database `allergen-yhz81`. It holds every table, reducer and single-writer rule in [contracts/state-tables.md](../contracts/state-tables.md), seeded from [contracts/allergen_units.json](../contracts/allergen_units.json).
- The real Python [`StateClient`](../contracts/apis.md#stateclient), which every Python component uses to read and write the database.
- The generated TypeScript bindings that `web/` (team outputs) uses to subscribe to tables.
- A small shift CLI (`start_shift`), run by a person at shift start.

## What's built so far

| Piece | State |
|---|---|
| Tables | `allergen`, `entity`, `entity_state`, `interaction`, `ticket` are in `lib.rs`. Not yet: `zone`, `bin_profile`, `plate_binding`, `verdict` |
| Reducers | `register_entity`, `record_interaction`, `move_entity`, `put_ticket`. Not yet: `put_bin_profile`, `set_zones`, `bind_plate`, `record_verdict`, and single-writer checks |
| Python `StateClient` | Not started |
| TypeScript bindings for `web/` | Not generated yet |
| Shift CLI | Not started |

## What agents expect

- Every method raises `StateUnavailable` on connection loss, timeout or a rejected reducer, and never returns stale data as if it were current (invariant 1).
- `subscribe()` exposes the age of the last update, so the gate can go `fault` on a stale feed.
- Writes are idempotent by primary key. Each table has exactly one writer team, enforced by caller identity.
- Every ticket revision is kept. `upsert_entities` never deletes an entity and never changes `first_seen_t_ms` or `origin` (invariants 8, 9).
- `list_interactions` returns events in time order.
- Throughput: `upsert_entities` about 5 times a second with up to ~30 entities; `record_interactions` bursts up to ~20 a second.

These are checked by the `StateClient` contract suite in `kitchen/shared/contract_tests/` (platform). `FakeStateClient` is the reference behavior.

## Until it lands

Agents use `FakeStateClient` (platform, `kitchen/shared/fakes/`). Tasks that need the real database carry the `needs-partner` label. The web screens use `FakeStateSource`, which replays a golden scenario, until your TS bindings exist.

## Handing off

- Open PRs from branches named `partner/<slug>`. CI lets those branches edit partner-owned folders.
- Any internal layout is fine.
- The tables and the `StateClient` interface in `contracts/` are the deal. Changing them means telling the Director (an RFC issue).

## What agents build around it

- **Every team** reads and writes through `StateClient`.
- **sim:** a load generator to measure your throughput.
- **outputs:** the tablet and expo screens, on your TS bindings.
- **platform:** the fake, the contract suite, and the end-to-end harness.

## Unverified and open

- How a reducer reads the caller's identity (for single-writer checks) hasn't been checked.
- The `spacetime` CLI and a Rust toolchain weren't installed on the owner's Mac.
- How Python reaches the database and where it runs: the default is SpacetimeDB 2.13 running locally, reached with HTTP reducer calls plus polling. It's your call; see the Database row in [AGENTS.md, Open questions](../AGENTS.md#11-open-questions).
