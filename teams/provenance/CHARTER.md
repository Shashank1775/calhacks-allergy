# Team charter: `provenance`

## Mission

For every plate that reaches the pass, decide whether a forbidden allergen could have reached it, explain the chain step by step, and **HOLD whenever in doubt**. This team writes the code that stands between a kitchen mistake and a guest. It is the safety-critical team.

## Owns

- Paths: `components/provenance/` ([OWNERS](../../OWNERS)).
- Stage 5 (provenance graph) and stage 6 (final analysis) of [ARCHITECTURE.md](../../docs/ARCHITECTURE.md), plus the Repeated Mistakes *analysis* behind stage 7.
- Single writer of the SpacetimeDB tables `entity_risk`, `verdict`, `verdict_step` and `mistake_pattern` ([state-tables.md](../../contracts/state-tables.md#writers)).
- The Laya runtime integration: the `LayaClient` adapter, timeouts, response validation, and the `ExactLaya` and `AdversarialLaya` fakes ([apis.md](../../contracts/apis.md#layaclient)).
- The verdict policy, the clean-confidence definition (ADR), and the safety-relevant constants: per-action transfer efficiency *k*, flag thresholds, Laya timeout, mode.

## Does not own

| Thing | Owner |
|---|---|
| The Laya model, its training data, fine-tuning and evaluation | sim |
| Which interactions happen and how they're classified | interactions (edge direction, OQ-9, is joint) |
| Ticket parsing, constraints, thresholds on the ticket, bin profiles | intake |
| SpacetimeDB module and the real `StateClient` | state |
| Gate, voice, expo board, Repeated Mistakes *view* | experience |
| Contents of `contracts/scenarios/` | contracts (Director merges); sim generates scenarios at scale |

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| `verdict` + `chain_step` (tables `verdict`, `verdict_step`) | experience (gate, voice, expo board) | `ScriptedVerdictSource`: replays fixture or scenario verdicts into `InMemoryStateClient` on a `Clock` schedule |
| `allergen_profile` as `entity_risk` (live, propagated) | experience (overlays) | `ScriptedVerdictSource` also emits risk rows |
| `mistake_pattern` | experience (Repeated Mistakes view) | `ScriptedVerdictSource` emits fixture patterns |
| `laya_request` | the Laya model (sim trains it) | none needed; sim builds requests from scenarios |
| `LayaClient` implementations `ExactLaya` and `AdversarialLaya` | provenance tests; sim's eval harness (as the exact-reachability reference) | they *are* the fakes |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| `ticket` (constraints, exceptions, revision, pass_threshold) | intake | `InMemoryStateClient` loaded from a scenario |
| `entity`, `plate_binding`, zone `pass` | perception | scenario loader |
| `bin_profile` (`allergen_profile`) | intake | scenario `bins` |
| `interaction_event` | interactions (live), sim (synthetic) | scenario `events`, recorded event JSONL |
| `laya_response` | the Laya model | `ExactLaya`, `AdversarialLaya` |
| `StateClient`, `Clock`, generated models | state (real), platform (fake, models) | `InMemoryStateClient` |
| `scenario` (golden + generated) | contracts, sim | the files themselves |

## Invariants we guard

Every test below carries the `safety` marker. Where the invariant is a property, the test is property-based (`hypothesis`).

| Invariant | How our code could break it | Guarding test |
|---|---|---|
| 1. Fail closed | An exception, timeout, `StateUnavailable`, missing ticket, missing binding or stale feed reaches a PASS | Fault injection at every dependency call site: the decision is always `hold`, with reason `error`, `missing_input`, `stale_track` or `model_unavailable` |
| 2. Models only tighten | Laya output removes a flag or lifts confidence | For any graph and any Laya response (random, empty, late, malformed, "plate is clean"), `decide(full)` is `hold` whenever `decide(rules_only)` is, and `flagged ⊇ exact flags` |
| 4. Forbidden allergens, not contacts | The policy accepts entity-level bans, or an exception suppresses a unit flag | The policy reads only allergen units. `ticket.exceptions` never suppresses a flag while profiles are unit-level (OQ-2 default) |
| 5. Time runs forward | Edges processed out of order create a path | Shuffling the input order never changes the result, and an edge into a source after that source's edge out never contaminates. Equal or overlapping timestamps resolve to the stricter order (ADR) |
| 8. Clean only from a known-clean source | An unknown-history entity is treated as clean, or a non-`clean_stack` entity is treated as clean | Any plate whose ancestry includes an unwashed entity with `origin: unknown` is `hold` with `unknown_history`; `origin: clean_stack` and `shift_start` entities start clean (scenarios `unknown-tool-mid-shift`, `clean-plate-from-stack`). Unknown starts at probability 1 for every unit (OQ-13 default) |
| 9. Late changes recheck everything | An amendment reuses the previous verdict incrementally | The verdict for revision *r* is recomputed from full history and equals a from-scratch run. The golden scenario `late-amendment-adds-dairy` passes |

We also never feed allergen data back to interactions (invariant 6).

## Stack and layout

Python ≥ 3.11, package `provenance` in the uv workspace, `hypothesis` for property tests. It runs on the laptop. Pure core, thin edges ([CONVENTIONS.md](../../docs/agents/CONVENTIONS.md)):

```
src/provenance/
  graph.py      edge ordering + noisy-OR propagation + wash reset (pure)
  exact.py      plate ancestor subgraph, reachability, chain extraction (pure)
  policy.py     decide(): the single function that returns pass or hold (pure, plain code)
  laya.py       LayaClient adapter with timeout and schema validation; ExactLaya, AdversarialLaya
  risk.py       entity_risk publisher
  mistakes.py   Repeated Mistakes aggregation and suggested-fix templates
  service.py    StateClient subscriptions; triggers on plate-at-pass and ticket revision
  settings.py   ProvenanceSettings: k per action, thresholds, timeouts, mode (logged at startup, recorded in verdict.versions)
```

Test command: `uv run pytest components/provenance` (`-m safety` for the safety suite).

## Deliverables by milestone

- **M0:** propagation engine; exact reachability with chains; verdict policy; `ExactLaya` and `AdversarialLaya`; all golden scenarios pass in CI (`rules_only`); clean-confidence ADR; `ScriptedVerdictSource` for experience.
- **M1:** service wired to `StateClient` (in-memory, then a real local SpacetimeDB) that triggers on plate-at-pass and amendments and writes `verdict` and `verdict_step` atomically; `entity_risk` publisher; consolidated property suite for invariants 1, 2, 5, 8 and 9 signed off by the safety reviewer; zero false passes on sim's observation-complete corpus; the e2e harness passes.
- **M2:** live edges from interactions; staleness detection (no plate updates, edge feed lag) leads to HOLD; rules-only latency ≤ 200 ms; deterministic replay of a recorded live session. No hardware work for this team.
- **M3:** `full` mode through `LayaClient` with timeout → `model_unavailable`; ≤ 1 s budget; ADR on `trace_sensitive` semantics (OQ-8).
- **M4:** Repeated Mistakes aggregation with suggested fixes; live late-change recheck; provenance failure drills (DB down, Laya down, corrupt edge), each of which ends in HOLD.

## Working with other teams

- **interactions:** edge semantics and direction (OQ-9); classifier name and version flow into `verdict.versions.classifier`; recorded event JSONL.
- **sim:** scenario corpus and the Laya model. We provide `ExactLaya` through `LayaClient` as their reference. They never use our engine to produce expected answers.
- **intake:** ticket revisions, thresholds (OQ-4), exception semantics (OQ-2).
- **perception:** plate entering the `pass` zone triggers a verdict; `first_seen_t_ms`; re-acquired tracks get new IDs; stale tracks.
- **state:** atomic `record_verdict(verdict, steps)`; `entity_risk` write rate.
- **experience:** verdict feed and chain display. The gate's own `fault` state on a stale feed is theirs.
- **Safety reviewer:** every PR labeled `safety-critical`.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Thresholds unknown (OQ-4) | Placeholder defaults in `ProvenanceSettings`; the owner sets the real values; each verdict records the values used |
| Clean-confidence ill-defined | M0 ADR. It must be monotone: more evidence of a forbidden allergen never raises confidence |
| `trace_sensitive: false` semantics (OQ-8) | Treat every constraint as trace-sensitive until the ADR lands |
| Unknown-history prior (OQ-13), edge direction (OQ-9) | Strict defaults: probability 1; contact and wipe go both ways |
| Exceptions like "coconut is fine" (OQ-2) | Unit-level profiles can't prove the exception is the only source, so exceptions are shown but never suppress a flag |
| Plates and late-arriving cooks appear mid-shift | Settled in planning: entities leaving a `clean_stack` zone start clean (`entity.origin`); late cooks' hands stay unknown until they wash |
| A false-positive wash erases real contamination (the unsafe direction) | Cap wash efficacy in settings; only washes in a `wash` zone count; track wash-then-hold patterns in mistakes |
| Graph grows over a long shift | Incremental propagation for risk; full recompute for verdicts; perf test on sim's long-shift scenario |
| Laya slow in `full` mode makes everything HOLD | Correct behavior. Switching to `rules_only` is a human action and is recorded on each verdict |

## Definition of done (team additions)

- Every PR touching `graph`, `exact`, `policy`, `laya` or `service` is labeled `safety-critical` and has `review:safety-approved` before merge.
- All golden scenarios pass in CI, and so does sim's observation-complete CI subset (zero false passes).
- Every new HOLD reason has a test. Every caught exception path is covered by fault injection.
- Changing any safety-relevant constant updates `versions` and is called out in the PR's safety section.
- Determinism: the same inputs produce byte-identical verdict JSON.

## Staffing

- **M0:** 2 Workers (engine; policy and fakes).
- **M1:** 3 (service; risk publisher; property suite).
- **M2–M3:** 2–3. **M4:** 2.

`policy.py` is held by one careful Worker at a time and is never split across parallel PRs. Graph performance, the risk publisher, mistakes and the fakes parallelize well.
