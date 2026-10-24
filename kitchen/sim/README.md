# kitchen/sim: Simulated kitchens with known truth

**Owner:** team `sim` (agents)

**Pipeline step:** not a live step. Sim makes kitchens whose truth we know. **One simulated world, two outputs:** event-level scenarios (so the graph, the verdict logic and Laya can be tested and trained against known answers) and rendered clips (so the partner's ConvLSTM can learn interaction before it sees a real kitchen).

## What happens here

- A seeded simulated world runs prep workflows (stations, bins, cooks, tools, recipes, tickets) and samples whether each allergen **actually** moves or each wash **actually** cleans.
- An observation model turns true events into emitted `interaction_event`s: noisy probabilities, optional dropped edges, and track loss that re-identifies an entity under a new ID.
- Error injectors add the mistakes and sensing failures that matter.
- A 2D renderer draws the same world as abstract frames and turns them into ConvLSTM training clips (see below).
- Output: scenario corpora, replay JSONL, Laya training and evaluation pairs, a model-agnostic Laya eval harness with a hard gate, and labeled clip datasets.

## Rendered clips for the ConvLSTM

The partner's ConvLSTM learns **whether two objects touched and whether material moved**, never what the objects are. So the renderer never draws realistic spoons or forks. The pipeline:

1. **World run.** Cooks move tools between bins, plates, hands and the wash station, with 2D positions over time. Contents are tracked too: food in a bowl is a blob that shifts, splits or smears when something really touches it.
2. **Abstract frames.** Each step is drawn as an RGB frame:
   - a randomized textured background;
   - plain shapes for tools and hands;
   - containers with food blobs.
   
   Appearance is randomized on purpose: colors, textures, sizes, aspect ratios.
3. **Shared change mask.** Each pair of consecutive frames goes through `kitchen/shared/change_mask.py`, the same function vision runs on real camera frames. The result is a gradient that's brighter where more moved.
4. **Shared clip builder.** Each candidate pair's boxes (with tracker-like jitter) and the masks go through `kitchen/shared/clips.py`. That gives a `[T, C, H, W]` tensor (`box_a`, `box_b`, `change`) plus `dt_ms`, the [`interaction_clip`](../../contracts/schemas/interaction_clip.schema.json) format the model reads.
5. **Labels from world truth.** Each clip records `contact`, `transferred`, `action`, `direction` and `contact_t_ms`, taken from what happened in the world, never from any model.

**Hard negatives** are what stop the model from learning that box overlap means transfer:
- boxes overlapping in 2D while one passes above the other (no contact);
- hovering;
- motion with no contact (someone else stirring nearby);
- contact with no visible movement (labeled contact, not transferred);
- camera noise;
- lighting flicker;
- missed detections;
- variable frame timing.

The example from the owner: a spoon touches an omelette dish and the omelette shifts five inches. In the clip, two boxes come together, and right after they overlap, the change mask lights up inside the dish's box. Labels: contact = true, transferred = true.

## Ground truth stays independent

This is what makes sim worth having.

- Expected answers come from what happened in the simulated world, **never** from the partner's graph or the verdict policy. Sim never imports `kitchen.graph` or `kitchen.verdict` internals. The only imports allowed are `kitchen.shared` and verdict's published `LayaClient` fakes.
- For generated scenarios (tag `generated`), an expected HOLD is a hard requirement: nobody may PASS a truly contaminated plate. An expected PASS is a quality target, so a false HOLD is tracked, not failed.
- Scenarios tagged `observation-complete` emit an edge for every true transfer. Only those gate CI.
- Hand-written golden scenarios in `contracts/scenarios/` still require exact matches.

## Files

| File | What it does |
|---|---|
| `kitchen.py` | The simulated world: layout, bins with profiles, cooks, tools, recipes, and the true allergen process (seeded) |
| `scenarios.py` | Observation model and scenario writer: world runs → scenario files and replay JSONL. Also the CLI (`generate`, `export`, `metrics`, `load`) |
| `errors.py` | Error injectors: skipped wash, shared utensil, bare-hand contact, late entity, track loss, amendment, dropped edge, probability noise |
| `laya_data.py` | Laya training data: (`laya_request`, target `laya_response`) pairs with leak-free train/val/test splits |
| `laya_eval.py` | Model-agnostic Laya evaluation over any `LayaClient`, with the hard gate and a report |
| `render.py` | 2D clip renderer: world runs → abstract RGB frames → shared change mask → shared clip builder → labeled clips, including hard negatives |
| `clip_data.py` | ConvLSTM clip datasets: balanced, with leak-free splits (by seed and held-out kitchen layouts), manifest and datasheet |

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| Out | Generated scenario corpus (full set in `data/`; CI subset ≤ 1 MB in git) | `scenario` | verdict, partner graph, platform e2e |
| Out | Replay streams (`classifier.name = sim_ground_truth`) | `interaction_event`, `entity` JSONL | partner graph, platform e2e |
| Out | Laya training and eval pairs | `laya_request`, `laya_response` | Laya fine-tune (M3) |
| Out | Laya model artifact, model card, eval report | `LayaClient` config | verdict |
| Out | Labeled training clips (box masks + change mask + `dt_ms`) | [`interaction_clip`](../../contracts/schemas/interaction_clip.schema.json) + npz | partner's ConvLSTM |
| Out | Load profiles | `StateClient` calls | partner database (throughput) |
| In | `ExactLaya`, `AdversarialLaya` | `LayaClient` | verdict |
| In | `InMemoryStateClient`, `Clock`, generated models | `StateClient` | platform (fake), partner (real) |

## Safety rules that apply

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules).

| Invariant | Our part | Test |
|---|---|---|
| 5. Time runs forward | Generated streams are causal: a tool carries an allergen only after its true contact with the source | Generator property test over random seeds (`safety`) |
| 6. Allergen-blind and object-blind classifier | Clips contain only box masks, the change mask and timing: no allergen fields, no classes, no realistic utensils | Schema validation on every clip; a test that the renderer's shape set has no utensil sprites |
| 8. Clean only from a known-clean source | Track loss and late entities appear in `late_entities` with `origin: unknown`; fresh plates come from a `clean_stack` zone | Injector unit tests |
| 2. Models only tighten (as a gate) | On held-out sim, Laya never misses an allergen the reference finds (ground truth now, `ExactLaya` on the real graph later) | Eval gate exits non-zero on any miss (`safety`) |

## Fakes

- **We use:** `InMemoryStateClient` and `FixedClock` (platform), and `ExactLaya` / `AdversarialLaya` (verdict) through `LayaClient` only.
- **We ship:** the corpora and JSONL files themselves. They are the fakes for "a kitchen happened".

## Tests

- Live in `kitchen/sim/tests/`; run `uv run pytest kitchen/sim`. The small CI scenario subset lives in `kitchen/sim/tests/fixtures/scenarios/`.
- Every generated file validates against its schema, and the same (generator version, seed) reproduces it byte for byte.
- Any change to the true process or the observation model bumps the generator version and regenerates the CI subset in the same PR.

## Milestones

- **M0:** generator v0 (deterministic per seed); ground-truth ADR; ≥ 20 generated scenarios; JSONL export; 2–3 proposed golden scenarios; clip-renderer ADR, reviewed with the partner (sim-12); renderer v0 with a first labeled clip set (sim-13).
- **M1:** error injectors; ≥ 500-scenario corpus with a CI subset and a metrics script; Laya dataset v0; eval harness v0; long-shift stress scenario; load generator; hard negatives and domain randomization (sim-14); a large clip dataset with leak-free splits for the partner (sim-15).
- **M2:** calibrate rates and track loss from recorded live sessions; turn labeled recordings into scenarios; compare sim change-mask statistics with real ones and tune the renderer to close the gap.
- **M3:** Laya fine-tune once OQ-5 is answered; eval gate report and model card; refresh clip datasets as the partner's model needs.
- **M4:** scripted demo-kitchen scenario matching the live demo; failure-drill scenarios.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Circular ground truth | The independence rules above. Reviewers reject any import of graph or verdict internals |
| Sim-to-real gap | Calibrate from recordings in M2; in M3 also gate Laya on real recorded graphs |
| What backs Laya (OQ-5) | Model-agnostic datasets and harness now; training after the owner answers |
| Leakage between splits | Split by seed range **and** held-out kitchen/recipe families; a test fails if any seed or scenario appears in two splits |
| Corpus size | Corpora live in `data/` (gitignored) with a `data-manifest.json`; the committed subset stays ≤ 1 MB |
| Sim-to-real gap in change masks (real kitchens have steam, shadows, flicker, hands everywhere) | Shared change-mask function; domain randomization and noise; calibrate against real `change.npz` recordings in M2; the ConvLSTM is judged on real held-out clips, never on sim |
| The model learns "overlap means transfer" | Hard negatives at a fixed share of every dataset; per-hard-negative metrics in the datasheet |
| Scenario schema expresses hard vs soft expectations only via tags | ADR in sim-02; RFC if a field is needed |
