# Team charter: `state`

## Mission

Make SpacetimeDB the single, consistent, enforced source of shared state: the module and its tables, the rule that each table has exactly one writer, and the clients every other team uses to read and write it.

## Owns

- Paths: `components/state/`. The authoritative list is [OWNERS](../../OWNERS).
- The `state` box in [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#pipeline): the SpacetimeDB module implementing [state-tables.md](../../contracts/state-tables.md), writer-identity enforcement, the real `StateClient` behind the Protocol in [apis.md](../../contracts/apis.md#stateclient-python-interface), TypeScript bindings generated from the module for `experience`, the allergen seed, local DB setup and operations, and throughput.

## Does not own

- **The `StateClient` Protocol and `InMemoryStateClient`.** Platform owns these in `libs/py-common`. Changes to the Protocol go through an RFC to `contracts/apis.md`.
- **The table and reducer spec.** That is `contracts/state-tables.md`, changed by RFC and merged by the Director. State implements it, and may choose physical types as long as the logical fields are exposed.
- **What gets written.** Each writer team owns the logic that decides its rows (intake: tickets and bin profiles; perception: entities, zones and plate bindings; interactions: edges; provenance: risk, verdicts and mistake patterns).
- **Where the database runs for the demo.** That's OQ-7, the owner's call; the default is local standalone.

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| SpacetimeDB module (all tables and reducers in `state-tables.md`, with single-writer enforcement) | everyone, via clients | — (the fake is `InMemoryStateClient`, owned by platform) |
| Real `StateClient` (Python), as a package in `components/state/` | intake, perception, interactions, provenance, platform e2e | platform's `InMemoryStateClient` |
| `subscribe()` change feed with a staleness signal | experience (gate goes `fault`), provenance | `InMemoryStateClient.subscribe` |
| Generated TypeScript bindings for the module | experience (expo board, screens) | — |
| Writer registry and per-service identity provisioning script | every writer team | — |
| Local dev setup: start, publish, seed, reset, inspect | everyone | — |
| Throughput report at the rates in `state-tables.md` | Director, perception, interactions | — |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| `contracts/state-tables.md`, `contracts/schemas`, `contracts/allergen_units.json` | Director (via RFC) | — |
| `StateClient` Protocol, generated models, `StateUnavailable` | platform (`py_common`) | — |
| StateClient contract test suite | platform | `InMemoryStateClient` is the reference behavior |

## Invariants we guard

| Invariant | How state guards it | Test (`safety`) |
|---|---|---|
| 1, fail closed | Every `StateClient` method raises `StateUnavailable` on connection loss, timeout, or a rejected reducer call. It never returns empty or stale data as if it were current. `subscribe()` exposes the age of the last update, so the gate can go `fault` | `test_db_down_raises`, `test_subscription_staleness_reported` |
| 4, forbidden allergens only | Ticket tables have no columns for entities or contacts | Schema parity test against `ticket.schema.json` |
| 5, time runs forward | `interaction` is indexed by `t_start_ms`; `list_interactions` returns events in time order | `test_list_interactions_time_ordered` |
| 8, unknown history | `upsert_entities` never deletes and never overwrites `first_seen_t_ms`, so a mid-shift entity stays detectable | `test_upsert_preserves_first_seen` |
| 9, late changes recheck everything | Amendments keep every prior revision's constraint and exception rows; interactions are never deleted during a shift | `test_amendment_keeps_prior_revisions` |
| Single writer per table ([contracts rule 3](../../contracts/README.md#rules)) | Reducers check the caller's identity against the writer registry and reject any other caller | `test_wrong_writer_rejected`, one case per reducer |

## Stack and layout

- **Module:** Rust, the [CONVENTIONS](../../docs/agents/CONVENTIONS.md) default. **Unverified:** the current SpacetimeDB major version and its module API (table and reducer macros, how a reducer reads the caller's identity, the codegen command and flags for TypeScript bindings). The first Worker pins a version and records it in the state-01 ADR. Every later claim is checked against that pinned version's docs.
- **Python client:** how Python reaches SpacetimeDB is open (OQ-1): HTTP reducer calls plus SQL polling, a small bridge process on an official SDK, or a community Python SDK. state-01 decides with measurements. Until then every consumer uses `InMemoryStateClient`.
- **Local tooling:** the `spacetime` CLI and a Rust toolchain. Neither was installed on the owner's Mac when this charter was written. Installing them is a `status:needs-human` step for the owner, with exact commands supplied by the Worker.
- Layout:

```
components/state/
  README.md          install, start, publish, seed, reset, inspect
  module/            SpacetimeDB module (Cargo project)
  client/            Python package `state` (real StateClient), pyproject.toml, tests/
  bindings/ts/       generated TypeScript bindings (never hand-edited)
  scripts/           dev.sh (start + publish + seed), provision_identities, throughput
```

- Test commands: module tests via cargo, and `uv run pytest components/state/client` (needs a local DB; integration tests are skipped in CI until the CI job can install the CLI).

## Deliverables by milestone

- **M0:** ADR on Python access, with a hello-world reducer call from Python against a local DB (state-01, a ROADMAP M0 exit item). Local dev script plus a module skeleton that seeds `allergen_unit` from `contracts/allergen_units.json` (state-04). The full module with every table, reducer and writer check (state-02). A CI job that builds the module and runs its tests (state-05).
- **M1:** real `StateClient` passing the shared contract suite (state-03). `subscribe()` with a staleness signal (state-06). Identity provisioning plus negative tests for every reducer (state-07). Generated TS bindings for experience (state-08). Throughput run (state-09). Preservation semantics tests (state-10). The e2e harness runs against the real local DB.
- **M2:** LAN deployment: Pi services write to the laptop's DB. Measure Pi→DB latency against the 500 ms edge budget. Reconnect behavior. Verify the staleness signal end to end with the gate (`hardware`; the owner verifies).
- **M3:** index and query support for provenance's subgraph extraction (`a_id`, `b_id`, `t_start_ms`) at full-shift volumes. Model versions recorded with verdict rows.
- **M4:** offline operation (local standalone, no internet). Restart recovery with no data loss mid-shift. Export of shift data so Repeated Mistakes can span shifts. Failure drills for the DB going down (clients raise, gate HOLDs).

## Working with other teams

- **platform:** co-own the contract suite. Report any behavior difference between the fake and the real client as a `type:request`. Ask platform to wire the real-DB e2e job once state-03 lands.
- **experience:** TS bindings and subscription shape. Experience tells us which tables it subscribes to; the list is in `state-tables.md`.
- **perception and interactions:** batch sizes and rates (`upsert_entities` at about 5 Hz with up to ~30 entities; `record_interactions` in bursts of about 20 per second). Share the throughput report.
- **provenance:** reads of the full time-ordered history for rechecks; atomic `record_verdict` with its steps.
- **intake:** ticket revisions and amendments; `put_bin_profile`; chef confirmations.
- **Director:** any table or reducer change is an RFC. Raise spec gaps found during implementation (for example a reducer with no `StateClient` method) as RFCs, not quiet additions.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| No first-class Python path (OQ-1) | Measure options in state-01. Hide the choice behind `StateClient` so it can change without touching consumers |
| SpacetimeDB version churn breaks the module or codegen | Pin the CLI and crate versions in the ADR and README. Upgrades are their own issue |
| Polling-based subscriptions add latency or miss the gate's staleness budget | Measure in state-06 and state-09. Expose last-update age explicitly. Escalate if the budget can't be met |
| Per-service identities are awkward to provision | One script, tokens only in `.env`, never committed. Document identity rotation |
| Persistence or restart loses mid-shift history, breaking invariant 9 | Restart test in M4; until then, document the persistence configuration we rely on |
| Tooling missing on the owner's machine | Install steps are a `needs-human` checklist; the CI job installs the CLI on Linux runners |
| Hosting location undecided (OQ-7) | Build for local standalone first. Keep the endpoint configurable |

## Definition of done (team additions)

- The SpacetimeDB version used is the pinned one, and README commands work as written from a clean checkout once the CLI is installed.
- Any reducer change comes with a wrong-writer negative test.
- Integration tests state whether they ran against a real local DB (paste the output) or were skipped.
- Generated TS bindings are regenerated in the same PR as the module change that affects them.

## Staffing

- **M0:** 2 Workers in parallel: one on the state-01 spike and ADR, one on state-04 (dev setup and skeleton). Then one careful Worker on state-02, since the module's table definitions are a single file that conflicts badly if split.
- **M1:** 2–3 Workers: StateClient then subscribe (state-03 → state-06, one Worker), enforcement and preservation tests (state-07, state-10), bindings and throughput (state-08, state-09).
- **M2–M4:** 1 Worker, plus owner time for LAN and hardware verification.
