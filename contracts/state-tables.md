# SpacetimeDB tables and reducers

Logical spec. The **partner** implements it as a SpacetimeDB module (Rust) in `database/allergen-yhz-81/`, database `allergen-yhz81`. The module may use its own physical names (for example `id` for `entity_id`, `p` for `probability`, or a list of `{allergen_id, p}` pairs for `probabilities`) as long as the [`StateClient`](apis.md#stateclient) and the generated TypeScript bindings expose these fields.

## Writers

Every table has exactly **one** writer team, and each team writes only through its own `StateClient` methods.

| Table | Writer | Readers | Schema |
|---|---|---|---|
| `allergen` | partner database (seeded from `allergen_units.json`) | all | [`allergen_units.json`](allergen_units.json) |
| `zone` | vision (calibration) | all | — |
| `ticket` | orders | verdict, partner graph, outputs | `ticket` |
| `bin_profile` | orders | partner graph, verdict, outputs | `allergen_profile` |
| `entity` | partner entity store | all | `entity` (identity fields) |
| `entity_state` | partner (entity store and graph) | all | `entity` (zone, box and allergen profile over time) |
| `plate_binding` | vision | verdict, partner graph, outputs | `scenario.plate_bindings[]` |
| `interaction` | partner interactions | partner graph, outputs | `interaction_event` |
| `verdict` | verdict | outputs | `verdict` |

The logical `Entity` in the schema is the `entity` row joined with its latest `entity_state` (zone, box, allergens, unknown). A bin's allergens come from `bin_profile` until the graph writes a state for it. A plate's `ticket_id` comes from `plate_binding`.

- **Who fills which `entity_state` fields.** Every row carries every field. The entity store owns `zone` and `box`; the graph owns `probabilities`, `unknown` and `caused_by`. Each writer copies the fields it doesn't own forward from the entity's latest row (or, for a bin with no row yet, from `bin_profile`), so a zone update can never wipe a bin's allergens.
- **`allergen_profile.source` in the join.** A bin with no graph-written state takes its `bin_profile.source`. Otherwise: `unknown: true` gives `unknown_history`; a row caused by a `clean.wash` edge gives `wash_reset`; a row caused by any other edge gives `propagated`; and a row with no `caused_by` gives `shift_start_clean`.

## Tables

| Table | Primary key | Indexed | Columns |
|---|---|---|---|
| `allergen` | `id` | | `name`, `default_severity` |
| `zone` | `zone_id` | `kind` | `kind` (`bin_slot`, `plating_spot`, `wash`, `clean_stack`, `pass`, `other`), `polygon` (normalized points) |
| `ticket` | `ticket_id` | | `revision`, `number`, `note`, `source`, `created_t_ms`, `pass_threshold`, `forbidden` (allergen IDs) |
| `bin_profile` | `entity_id` | | `probabilities`, `unknown`, `source` |
| `entity` | `entity_id` | `kind` | `kind`, `label`, `origin`, `first_seen_t_ms` |
| `entity_state` | `state_id` (auto) | `(entity_id, t_ms)` | `entity_id`, `version`, `t_ms`, `zone`, `box`, `probabilities`, `unknown`, `caused_by` (the `event_id` that changed it, or none) |
| `plate_binding` | `plate_id` | `ticket_id` | `ticket_id`, `t_ms` |
| `interaction` | `event_id` | `t_start_ms` | `t_end_ms`, `a_id`, `b_id`, `action`, `probability`, `direction`, `classifier` |
| `verdict` | `verdict_id` | `ticket_id`, `plate_id` | all `verdict` fields, including its `chain` |

- **History is append-only.** `entity` rows are written once and `entity_state` rows are never edited or deleted; a change is a new row. The graph and late-change rechecks read the full history from these rows and from `interaction`.
- **A ticket row holds the latest revision.** An amendment replaces it with `revision + 1`. Each verdict records the revision it judged, and anything acting on an older revision's verdict holds.
- **Each shift starts with an empty database.** Tools, hands and plates start clean because vision gives them `origin: shift_start`.

## Reducers

| Reducer | Caller | Effect |
|---|---|---|
| `register_entity(entity)` | partner entity store | Insert once. Registering again is a no-op; `first_seen_t_ms` and `origin` never change |
| `move_entity(state)` | partner entity store (zone, box) and graph (allergens) | Append an `entity_state` row |
| `record_interaction(event)` | partner interactions | Insert; a duplicate `event_id` is a no-op |
| `put_ticket(ticket)` | orders | Insert, or replace with the next revision |
| `put_bin_profile(entity_id, profile)` | orders | Upsert |
| `set_zones(zones)` | vision | Replace calibration |
| `bind_plate(plate_id, ticket_id, t_ms)` | vision | Insert; a plate binds once |
| `record_verdict(verdict)` | verdict | Insert, chain included |

## Throughput (proposed)

- `move_entity`: about 5 times a second for up to ~30 entities.
- `record_interaction`: bursts of up to ~20 a second.
- Expo board subscriptions: `entity`, `entity_state`, `ticket`, `verdict`.
