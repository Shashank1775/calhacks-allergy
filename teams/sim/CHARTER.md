# Team charter: `sim`

## Mission

Make kitchens whose truth we know. A seeded simulator generates realistic prep workflows with ground-truth contamination, so every team can test against known answers and Laya can be trained and gated before it ever sees a real plate.

## Owns

- Paths: `components/sim/` ([OWNERS](../../OWNERS)).
- The kitchen simulator: stations, bins, cooks, tools, recipes, randomized and seeded.
- Scenario generation at scale (schema [`scenario`](../../contracts/schemas/scenario.schema.json)) and interaction-event stream export.
- Laya training and evaluation datasets with held-out splits, the Laya fine-tune, the eval harness, the model card, and the model artifacts. Artifacts stay outside git, listed in a `data-manifest.json`.
- Synthetic `interaction_features` windows (allergen-blind) for interactions' pretraining or stress tests, if they want them.
- Load generators for state's throughput tests.

## Does not own

| Thing | Owner |
|---|---|
| Exact reachability and the verdict policy | provenance. Sim never imports provenance internals to compute expected answers |
| Laya runtime client, timeouts and policy integration | provenance |
| Real video clips, labeling, the LSTM | interactions (OQ-12) |
| The golden set in `contracts/scenarios/` | contracts. Sim *proposes* additions by PR; the Director merges |
| SpacetimeDB module | state |

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| Generated `scenario` corpus (+ CI subset ≤ 1 MB in git) | provenance, platform e2e, sim eval | the files are the fake |
| `interaction_event` JSONL (`classifier.name = sim_ground_truth`) | provenance, platform e2e | recorded JSONL |
| `laya_request` → target `laya_response` training and eval pairs | the Laya fine-tune | — |
| Laya model artifact, model card, eval report | provenance (configures the real `LayaClient`) | until it ships, provenance uses `ExactLaya` |
| Labeled synthetic `interaction_features` windows | interactions (optional) | — |
| State load generator | state | — |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| `scenario`, `ticket`, `interaction_event`, `allergen_profile`, `laya_request`, `laya_response` | contracts | `contracts/fixtures/valid/` |
| `LayaClient` (`ExactLaya` as the exact-reachability reference in eval) | provenance | `ExactLaya`, `AdversarialLaya`. These published fakes are the only provenance code we import |
| `StateClient` (load tests), `Clock`, generated models | state, platform | `InMemoryStateClient` |

## Ground truth stays independent

This is the rule that makes sim worth having.

- The simulator moves allergens in a **simulated world**: each true transfer samples whether the allergen actually moves, using per-action true rates. Each wash samples whether it actually cleans. Ground truth is what happened in that world, not what a noisy-OR engine computes.
- A separate **observation model** turns true events into emitted `interaction_event`s: noisy `probability`, optional dropped edges, optional track loss that re-identifies an entity under a new ID (a `late_entities` entry).
- For **generated** scenarios (tag `generated`), `expected.decision = hold` is a hard requirement: the engine must never PASS a truly contaminated plate. `expected.decision = pass` is a quality target: a false HOLD is tracked, not failed.
- Scenarios tagged `observation-complete` emit an edge for every true transfer. Only those gate provenance in CI. Scenarios with dropped edges measure end-to-end exposure to perception and classifier misses, which is a different team's error.
- Hand-written golden scenarios in `contracts/scenarios/` still require exact matches.

## Invariants we guard

| Invariant | Our part | Test |
|---|---|---|
| 5. Time runs forward | Generated event streams are causally consistent: a tool carries an allergen only after its true contact with the source | Generator property test on random seeds |
| 6. Allergen-blind classifier | Synthetic features never contain allergen fields | Schema validation (`additionalProperties: false`) on every emitted window |
| 8. Unknown history | Track loss and late entities show up in `late_entities` | Injector unit tests |
| 2. Models only tighten (as a gate) | The Laya eval gate: on held-out sim, Laya never misses an allergen that `ExactLaya` finds on the same graph | Eval harness gate, run on every candidate model |

## Stack and layout

Python ≥ 3.11, package `sim`, PyTorch for training (M3; the backing model is OQ-5, so the harness is model-agnostic until it's answered).

```
src/sim/
  world.py       kitchen layout, entities, recipes, true allergen process (seeded)
  observe.py     observation model: probability noise, dropped edges, track loss
  inject.py      error injectors: skipped wash, shared utensil, hand contact, late entity, amendment
  generate.py    CLI: scenarios + event JSONL from (generator version, seed)
  laya_data.py   (request, target response) pairs; splits by seed range and kitchen/recipe family
  laya_eval.py   model-agnostic eval over any LayaClient; report JSON + markdown
  load.py        StateClient load generator
```

Test command: `uv run pytest components/sim`. Generated corpora go in `data/` (gitignored); only seeds, generators and a small CI subset are committed.

## Deliverables by milestone

- **M0:** generator v0 emitting valid scenario files (deterministic per seed); ground-truth model ADR; ≥ 20 generated scenarios (the ROADMAP M0 exit); event JSONL export; 2–3 proposed golden scenarios for edge cases.
- **M1:** error-injection library; ≥ 500-scenario corpus with manifest plus a CI subset for provenance; false-pass and false-hold metrics script; Laya dataset generator v0 with leak-free splits; eval harness v0 (runs `ExactLaya` and `AdversarialLaya`); long-shift stress scenario; state load generator.
- **M2:** calibrate the simulator from recorded live sessions (edge rates, probability distributions, track-loss rate); convert labeled live recordings into scenarios.
- **M3:** Laya fine-tune once OQ-5 is answered; eval gate report and model card; artifact manifest; synthetic features for interactions if requested.
- **M4:** scripted demo-kitchen scenario matching the live demo; failure-drill scenarios.

## Working with other teams

- **provenance:** corpus, CI subset, metrics, Laya model and eval reports. We consume `ExactLaya` only through `LayaClient`.
- **interactions:** synthetic features; realism statistics from labeled clips.
- **state:** load-test profiles at the throughput in [state-tables.md](../../contracts/state-tables.md#throughput-proposed).
- **platform:** the e2e harness replays our scenarios and event streams.
- **perception:** real track-loss and re-ID behavior, so we can model it.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Circular ground truth | The independence rules above; reviewers reject any import of provenance internals |
| Sim-to-real gap | Calibrate from recordings in M2. In M3, also gate Laya against `ExactLaya` on real recorded graphs |
| Laya's backing model unknown (OQ-5) | Model-agnostic harness and datasets now; training scripts after the owner answers |
| Leakage between splits | Split by seed range *and* held-out kitchen/recipe families. A test fails if any seed or scenario ID appears in two splits. Hash manifest |
| Corpus size | Corpora live outside git; the committed CI subset stays ≤ 1 MB |
| Generated scenarios silently drift | Every artifact is reproducible from (generator version, seed); a test regenerates and compares hashes |

## Definition of done (team additions)

- Every generated file validates against its schema, and the same seed reproduces it byte for byte.
- No import from `provenance` except its published `LayaClient` fakes.
- Any change to the true-process or observation model bumps the generator version and regenerates the CI subset in the same PR.

## Staffing

- **M0:** 2 Workers (world/generator; ground-truth ADR and export).
- **M1:** 3 (injectors and corpus; Laya data and eval; load and stress).
- **M2:** 1–2. **M3:** 2–3 for training. **M4:** 1.

Injectors, exporters and the eval harness parallelize well. The true-process model in `world.py` is held by one Worker at a time.
