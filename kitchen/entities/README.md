# kitchen/entities: Entity database (partner)

**Owner:** the partner (a human). Agents never edit this folder. This README tells you what the agent teams expect from it, and what they're building around it.

## What this component does

It runs on the Pi. Vision hands it every `ObservationFrame`; it keeps every entity seen this shift in memory (ID, kind, label, zone, box, first and last seen, origin) and pushes entity rows to SpacetimeDB about 5 times a second.

## What the agents rely on

- **The interface:** [`EntityStore`](../../contracts/apis.md#entitystore) in `contracts/apis.md`: `ingest(frame)`, `get(entity_id)`, `snapshot()`, `start_sync(state, hz=5.0)`, `stop_sync()`.
- **Input:** [`observation_frame`](../../contracts/schemas/observation_frame.schema.json). Every detection already carries `entity_id`, `kind`, `zone`, `box` and `origin`, set by vision's identity step.
- **Output:** [`entity`](../../contracts/schemas/entity.schema.json) rows via `StateClient.upsert_entities`. You are the single writer of the `entity` table ([state-tables.md](../../contracts/state-tables.md#writers)). Allergen profiles are not yours: `bin_profile` comes from orders, and `entity_risk` comes from your graph.
- **Safety rules** ([AGENTS.md §3](../../AGENTS.md#3-safety-rules)):
  - Invariant 8: never delete an entity, and never change `first_seen_t_ms` or `origin` after it's set.
  - Invariant 1: a sync failure must surface as `StateUnavailable` or visible lag, never as freshness that isn't there.
  - Invariant 5: `t_ms` only moves forward.
- **Contract tests:** `kitchen/shared/contract_tests/` (written by platform, task platform-06). The fake already passes them, and your real store should pass the same suite.
- **Test data:** vision's recorded frame fixture pack (`kitchen/vision/tests/fixtures/pack/`, task vision-05), including a tool that leaves and re-enters the frame, which must come back as a new entity.

## Until yours lands

The agents use `FakeEntityStore` (platform, in `kitchen/shared/fakes/`): it applies frames to a dict, and `start_sync` writes snapshots to whatever `StateClient` it's given. Nothing agent-side is blocked on you. Tasks whose last step needs your real store are flagged `needs-partner` (for example vision-06).

## How to hand off

- Open PRs from branches named `partner/<slug>`. CI lets those touch partner folders; agent branches can't.
- Any internal layout is fine. `store.py` and `sync.py` are only suggestions.
- The interface in `contracts/apis.md` is the deal. If you need to change it, tell the Director, who will run it as an RFC so every consumer updates together.
- Need something from the agents? Open an issue with their `team:` label. Agents reach you with issues labeled `owner:partner`.

## What the agents build around it

- **vision** calls `ingest` on every frame without blocking its frame loop (vision-06), and sets `origin` on every detection (vision-04).
- **platform** owns `FakeEntityStore` and the contract tests (platform-06).
- **outputs** draws camera overlays from the `entity` table.
- Your **graph** reads entities, as does the **verdict** team (plate bindings and the pass zone).

## Open questions that touch you

- OQ-1: how Python reaches SpacetimeDB. Your call, since the database is yours.
- OQ-11: which Pi and Hailo hardware.

See [AGENTS.md §11](../../AGENTS.md#11-open-questions).
