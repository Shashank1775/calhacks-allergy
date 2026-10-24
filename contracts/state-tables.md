# SpacetimeDB tables and reducers

Logical spec. The state team implements it as a SpacetimeDB module in `components/state/` and may choose physical types (structs, `Vec`, JSON strings) as long as the [`StateClient`](apis.md#stateclient-python-interface) and generated TypeScript bindings expose these fields.

## Writers

Every table has exactly **one** writer team. The module enforces this by checking the caller's identity against a `writer` registry, so a bug in one team cannot corrupt another team's table.

| Table | Writer | Readers | Schema |
|---|---|---|---|
| `allergen_unit` | state (seed from `allergen_units.json`) | all | `allergen_units.json` |
| `shift` | state (`start_shift`) | all | — |
| `zone` | perception (calibration) | all | — |
| `ticket` | intake | provenance, experience | `ticket` |
| `ticket_constraint` | intake | provenance, experience | `ticket.constraints[]` |
| `ticket_exception` | intake | provenance, experience | `ticket.exceptions[]` |
| `bin_profile` | intake | provenance, experience | `allergen_profile` |
| `chef_confirmation` | intake | experience | — |
| `entity` | perception | all | `entity` minus `allergen_profile` |
| `plate_binding` | perception | provenance, experience | `scenario.plate_bindings[]` |
| `interaction` | interactions | provenance, experience | `interaction_event` |
| `entity_risk` | provenance | experience | `allergen_profile` (live, propagated) |
| `verdict` | provenance | experience | `verdict` minus `chain` |
| `verdict_step` | provenance | experience | `verdict.chain[]` |
| `mistake_pattern` | provenance | experience | `mistake_pattern` |

The logical `Entity.allergen_profile` in the schema is the join: `bin_profile` for bins, `entity_risk` for everything else, and `{unknown: true}` for entities with `origin: unknown` that nothing has reset yet.

## Tables

| Table | Primary key | Indexed | Columns |
|---|---|---|---|
| `allergen_unit` | `id` | | `name`, `default_severity`, `parent_id?` |
| `shift` | `shift_id` | | `wall_clock_start`, `active` |
| `zone` | `zone_id` | `kind` | `kind` (`bin_slot`, `plating_spot`, `wash`, `clean_stack`, `pass`, `other`), `polygon` (normalized points) |
| `ticket` | `(ticket_id, revision)` | `ticket_id`, `status` | `number`, `status`, `created_t_ms`, `source`, `note`, `pass_threshold`, `is_latest` |
| `ticket_constraint` | `(ticket_id, revision, allergen_unit_id)` | `ticket_id` | `severity`, `trace_sensitive`, `confidence` |
| `ticket_exception` | `(ticket_id, revision, allergen_unit_id, ingredient)` | `ticket_id` | |
| `bin_profile` | `entity_id` | | `probabilities`, `source`, `unclear_ingredients`, `confirmed_by_chef` |
| `chef_confirmation` | `confirmation_id` | `entity_id` | `ingredient`, `status` (`open`, `answered`), `allergen_unit_ids` |
| `entity` | `entity_id` | `kind`, `zone` | `label`, `zone`, `box`, `track_id`, `origin`, `first_seen_t_ms`, `last_seen_t_ms` |
| `plate_binding` | `plate_id` | `ticket_id` | `ticket_id`, `t_ms` |
| `interaction` | `event_id` | `t_start_ms`, `a_id`, `b_id` | all `interaction_event` fields |
| `entity_risk` | `entity_id` | | `probabilities`, `unknown`, `source`, `t_ms` |
| `verdict` | `verdict_id` | `ticket_id`, `plate_id` | all `verdict` fields except `chain` |
| `verdict_step` | `(verdict_id, step)` | `verdict_id` | all `chain_step` fields |
| `mistake_pattern` | `pattern_id` | `allergen_unit_id` | all `mistake_pattern` fields |

Every ticket revision is kept, in all three ticket tables, so any verdict can be re-explained against the exact revision it judged. `put_ticket` sets `is_latest` on the new revision and clears it on the previous one in the same transaction.

## Reducers

| Reducer | Caller | Effect |
|---|---|---|
| `seed_allergen_units(units)` | state (init) | Upsert `allergen_unit` |
| `start_shift(shift_id, wall_clock_start)` | state (the owner runs the state team's shift CLI) | New active shift. Tools, hands and plates start clean |
| `set_zones(zones)` | perception | Replace calibration |
| `put_ticket(ticket)` | intake | Insert, or amend with `revision + 1` and new constraint rows |
| `put_bin_profile(entity_id, profile)` | intake | Upsert |
| `open_chef_confirmation(entity_id, ingredient)` / `answer_chef_confirmation(id, allergen_unit_ids)` | intake | |
| `upsert_entities(entities)` | perception | Batched at ~5 Hz. Never deletes; stale entities keep `last_seen_t_ms` |
| `bind_plate(plate_id, ticket_id, t_ms)` | perception | |
| `record_interactions(events)` | interactions | Insert; duplicate `event_id` is a no-op |
| `put_entity_risk(entity_id, profile, t_ms)` | provenance | Upsert |
| `record_verdict(verdict, steps)` | provenance | Insert verdict and its chain atomically |
| `put_mistake_pattern(pattern)` | provenance | Upsert |

## Throughput (proposed)

- `upsert_entities`: about 5 calls per second, up to ~30 entities each.
- `record_interactions`: bursts of up to ~20 per second.
- Expo board subscriptions: `entity`, `entity_risk`, `verdict`, `verdict_step`, `mistake_pattern`, `ticket`.
