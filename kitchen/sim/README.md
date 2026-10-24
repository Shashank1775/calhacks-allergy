# kitchen/sim: Simulated kitchens with known truth

**Owner:** team `sim` (agents).

Not a live step. Sim makes kitchens whose truth we know. **One simulated world, two outputs:** event-level scenarios, so the graph and the verdict logic can be tested against known answers, and rendered clips, so the partner's ConvLSTM can learn interaction before it sees a real kitchen.

## What happens here

- A seeded world runs prep (stations, bins, cooks, tools, recipes, tickets) and decides whether each allergen **actually** moves and each wash **actually** cleans.
- Kitchen mistakes are injected on purpose: skipped wash, shared utensil, bare-hand contact, late entity, track loss, amendment, dropped edge.
- The scenario writer turns world runs into scenario files and replay JSONL.
- The clip renderer draws the same world as abstract frames and turns them into labeled ConvLSTM training clips.

The true transfer rates, how observations are derived, what `expected` means and the exact tag strings are in the [sim ground truth decision](../../docs/decisions/sim-ground-truth.md).

## Training clips for the ConvLSTM

The ConvLSTM learns **whether two objects touched and whether material moved**, never what the objects are. So the renderer never draws realistic spoons or forks.

1. **World run.** Cooks move tools between bins, plates, hands and the wash station. Food in a bowl is a blob that shifts, splits or smears when something really touches it.
2. **Abstract frames.** Each step is an RGB frame with a randomized textured background, plain shapes for tools and hands, and containers with food blobs. Colors, textures and sizes are randomized on purpose.
3. **Shared change mask.** Consecutive frames go through `kitchen/shared/change_mask.py`, the same function vision runs on real frames.
4. **Shared clip builder.** Each candidate pair's boxes (with tracker-like jitter) and the masks go through `kitchen/shared/clips.py`, giving the [`interaction_clip`](../../contracts/schemas/interaction_clip.schema.json) format the model reads.
5. **Labels from world truth:** `contact`, `transferred`, `action`, `direction`, `contact_t_ms`. Never from a model.

**Hard negatives** stop the model from learning that box overlap means contact or transfer: boxes overlapping while one passes above the other, motion with no contact, contact with no visible movement (touching, or hovering over the food, which an overhead camera can't tell from touching, so it's labeled contact), camera noise, lighting flicker, missed detections, and variable frame timing.

The owner's example: a spoon touches an omelette dish and the omelette shifts. In the clip, two boxes come together, and right after they overlap, the change mask lights up inside the dish's box. Labels: contact = true, transferred = true.

## Ground truth stays independent

- Expected answers come from the simulated world, **never** from the partner's graph or the verdict policy. Sim imports only `kitchen.shared`.
- Hand-written golden scenarios in `contracts/scenarios/` still require exact matches.

## Files

| File | What it does | State |
|---|---|---|
| `episodes.py` | Short episodes of one tool or hand (A) and one container (B), plus bystanders: motion, food, nudges, and the true labels. Eight kinds: scoop, deposit, wipe, touch, pass over, hover, motion nearby, approach and leave | Built |
| `training_clips.py` | Episodes → abstract frames → shared change mask → shared clip builder → labeled clips, with leak-free train/val/test splits, a manifest and a datasheet | Built |
| `world.py` | The full-shift kitchen for scenarios: layout, bins with profiles, cooks, tools, recipes, the true allergen process, and injected mistakes (seeded) | Not built |
| `scenarios.py` | World runs → scenario files and replay JSONL. CLI: `generate`, `export` | Not built |

### Make a clip dataset and train on it

```bash
uv run python -m kitchen.sim.training_clips --out data/clips/v1 --train 40000 --val 4000 --test 4000 --workers 24
python -m kitchen.lstm.train --data data/clips/v1 --out runs/lstm-v1 --epochs 20   # on a GPU machine with torch
```

A clip is about 80 KB compressed, so 48,000 clips take about 4 GB. Each split has `clips.jsonl` (one `interaction_clip` record per line, with labels) and one npz per clip (keys `x` and `dt_ms`). `kitchen/lstm/train.py` trains the partner's model on them.

## In and out

| Direction | What | Contract | To |
|---|---|---|---|
| Out | Generated scenarios (full set in `data/`; a small subset in git for tests) | `scenario` | verdict, partner graph, platform end-to-end harness |
| Out | Replay streams (`classifier.name = sim_ground_truth`) | `interaction_event`, `entity` JSONL | partner graph, platform end-to-end harness |
| Out | Labeled training clips (box masks + change mask + `dt_ms`) | [`interaction_clip`](../../contracts/schemas/interaction_clip.schema.json) + npz | partner's ConvLSTM |

## Safety rules

See [Safety rules](../../AGENTS.md#3-safety-rules).

| Rule | Our part | Test |
|---|---|---|
| Time runs forward | A tool carries an allergen only after its true contact with the source | Property test over random seeds (`safety`) |
| Blind classifier | Clips contain only box masks, the change mask and timing: no allergens, no classes, no realistic utensils | Schema validation on every clip; a test that the renderer's shape set has no utensil sprites |
| Known-clean source | Track loss and late entities appear in `late_entities` with `origin: unknown`; fresh plates come from a `clean_stack` zone | Mistake-injection unit tests |

## Tests

- Tests live in `kitchen/sim/tests/`. Run `uv run pytest kitchen/sim`.
- They use `FakeStateClient` and `FakeClock` (platform). The corpora and JSONL files we produce are the fakes for "a kitchen happened".
- Every generated file validates against its schema, and the same (generator version, seed) reproduces it byte for byte.
- No seed or kitchen layout appears in two splits.

## Watch out for

- Real kitchens have steam, shadows, flicker and hands everywhere. Randomize hard, calibrate against real `change.npz` recordings, and judge the ConvLSTM only on real held-out clips.
- Every transfer rate is a placeholder until recorded live sessions calibrate it.
- Corpora and clip sets live in `data/` with a `data-manifest.json`; the committed subset stays under 1 MB.
