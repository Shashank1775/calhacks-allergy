# SpacetimeDB tables and reducers

Logical spec. The **partner** implements it as a SpacetimeDB module in `database/` and may choose physical types (structs, `Vec`, JSON strings) as long as the [`StateClient`](apis.md#stateclient) and generated TypeScript bindings expose these fields.

## Writers

Every table has exactly **one** writer team. The module enforces this by checking the caller's identity against a `writer` registry, so a bug in one team cannot corrupt another team's table.

| Table | Writer | Readers | Schema |
|---|---|---|---|
| `shift` | partner database (`start_shift`) | all | — |
| `zone` | vision (calibration) | all | — |
| `ticket` | orders | verdict, partner graph, outputs | `ticket` |
| `bin_profile` | orders | verdict, partner graph, outputs | `allergen_profile` |
| `entity` | partner entity store | all | `entity` minus `allergen_profile` |
| `plate_binding` | vision | verdict, partner graph, outputs | `scenario.plate_bindings[]` |
| `interaction` | partner interactions | partner graph, outputs | `interaction_event` |
| `entity_risk` | partner graph | outputs | `allergen_profile` (live, propagated) |
| `verdict` | verdict | outputs | `verdict` |

The logical `Entity.allergen_profile` in the schema is the join: `bin_profile` for bins, `entity_risk` for everything else, and `{unknown: true}` for entities with `origin: unknown` that nothing has reset yet. The allergen list is the file [`allergen_units.json`](allergen_units.json); the partner may mirror it in a table, but agents read the file.

## Tables

| Table | Primary key | Indexed | Columns |
|---|---|---|---|
| `shift` | `shift_id` | | `wall_clock_start`, `active` |
| `zone` | `zone_id` | `kind` | `kind` (`bin_slot`, `plating_spot`, `wash`, `clean_stack`, `pass`, `other`), `polygon` (normalized points) |
| `ticket` | `(ticket_id, revision)` | `ticket_id`, `status` | `number`, `status`, `created_t_ms`, `source`, `note`, `pass_threshold`, `constraints`, `exceptions`, `is_latest` |
| `bin_profile` | `entity_id` | | `probabilities`, `unknown`, `source` |
| `entity` | `entity_id` | `kind`, `zone` | `label`, `zone`, `box`, `track_id`, `origin`, `first_seen_t_ms`, `last_seen_t_ms` |
| `plate_binding` | `plate_id` | `ticket_id` | `ticket_id`, `t_ms` |
| `interaction` | `event_id` | `t_start_ms`, `a_id`, `b_id` | all `interaction_event` fields |
| `entity_risk` | `entity_id` | | `probabilities`, `unknown`, `source`, `t_ms` |
| `verdict` | `verdict_id` | `ticket_id`, `plate_id` | all `verdict` fields, including its `chain` |

Every ticket revision is kept, so any verdict can be re-explained against the exact revision it judged. `put_ticket` sets `is_latest` on the new revision and clears it on the previous one in the same transaction.

## Reducers

| Reducer | Caller | Effect |
|---|---|---|
| `start_shift(shift_id, wall_clock_start)` | partner database (shift CLI, run by a person at shift start) | New active shift. Tools, hands and plates start clean |
| `set_zones(zones)` | vision | Replace calibration |
| `put_ticket(ticket)` | orders | Insert, or amend with `revision + 1` |
| `put_bin_profile(entity_id, profile)` | orders | Upsert |
| `upsert_entities(entities)` | partner entity store | Batched at ~5 Hz. Never deletes; stale entities keep `last_seen_t_ms` |
| `bind_plate(plate_id, ticket_id, t_ms)` | vision | |
| `record_interactions(events)` | partner interactions | Insert; duplicate `event_id` is a no-op |
| `put_entity_risk(entity_id, profile, t_ms)` | partner graph | Upsert |
| `record_verdict(verdict)` | verdict | Insert, chain included |

## Throughput (proposed)

- `upsert_entities`: about 5 calls per second, up to ~30 entities each.
- `record_interactions`: bursts of up to ~20 per second.
- Expo board subscriptions: `entity`, `entity_risk`, `verdict`, `ticket`.
