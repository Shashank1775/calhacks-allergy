# kitchen/lstm: ConvLSTM / ConvGRU interaction model (partner)

**Owner:** the partner (a human). Agents never edit this folder. This README says what the agent teams expect from it, and what they're building for it.

## The idea

The model learns **interaction**, not objects. It never sees a spoon or a fork. It sees two bounding boxes and a **change mask** over time: a gradient that's brighter where pixels moved more between frames. It also gets the elapsed time between steps. For example, if a spoon touches an omelette dish and the omelette shifts five inches, the clip is just two boxes that come together, and a bright patch of change inside the dish's box right after they overlap. The model learns whether the objects **touched**, and whether **material actually moved**. Because nothing in the input says what the objects are, it can't overfit to utensil appearance.

## Input (contract)

- One clip per candidate pair: a tensor `[T, C, H, W]` with channels `box_a`, `box_b`, `change` (and optionally `others`), plus `dt_ms` per step. Spec: [`interaction_clip`](../../contracts/schemas/interaction_clip.schema.json), and [Change mask and clips](../../contracts/apis.md#change-mask-and-clips).
- Clips are built by **one shared builder** (`kitchen/shared/clips.py`) from **one shared change-mask function** (`kitchen/shared/change_mask.py`). The same code runs on live camera frames (vision, then your interactions step) and on sim renders, so training input and live input match.
- No object pixels, classes, kinds or allergen data, ever (invariant 6). The schema rejects extra fields.
- You decide the geometry (H × W, T, stride, full frame or pair crop, mask gain, blur and trail) in an ADR (OQ-20). The defaults are 128 × 128, full frame, T = 16 over about 1 s, gain 4, blur 1.5 px, no trail.

## Output (contract)

[`InteractionClassifier`](../../contracts/apis.md#interactionclassifier): `name = "lstm"`, `version`, and `classify(PairWindow) -> list[(action, probability)]`. Until OQ-19 is settled, two heads map to actions:
- P(contact) → `transfer.contact`
- P(material moved) → `transfer.scoop`

A contact with no visible movement still counts as a possible trace transfer downstream, so trace contamination isn't dismissed just because nothing visibly moved.

## What the agents build for you

- **Training data (sim team).** A 2D renderer draws abstract kitchens: textured backgrounds, food blobs that shift and smear when touched, and plain shapes for tools, never realistic utensils. It runs the shared change-mask and clip code, and labels each clip from the simulated world's truth: `contact`, `transferred`, `action`, `direction`. It deliberately includes hard negatives:
  - boxes that overlap in 2D without touching;
  - hovering;
  - motion without contact;
  - contact without movement;
  - camera noise and lighting flicker;
  - box jitter and variable frame timing.

  Datasets come with leak-free splits and a datasheet (tasks sim-12 to sim-15).
- **Change masks (vision team).** Every real frame comes with its change mask, live and in recordings (`change.npz`), from the shared function (vision-13).
- **The shared builders (platform team).** `kitchen/shared/change_mask.py` and `clips.py` (platform-12), plus `FakeClassifier` and the classifier contract tests.

The interactions step itself is yours too (`kitchen/interactions/`): candidate pairs, zone rules, live pair windows, the fallback wrapper, real-clip labeling and the adoption benchmark. Its suggested task list is on GitHub as issues labeled `owner:partner`.

## Rules you're held to

- **Invariant 7.** Zone rules stay the default. The ConvLSTM becomes primary only if it beats them on the frozen real-clip benchmark, recorded in an ADR that you and the interactions Manager both sign. If it fails to load, raises or times out, `kitchen/interactions/classifier.py` falls back to zone rules.
- **Budget.** Each call must answer within the interactions wrapper's primary timeout (`primary_timeout_ms`, 100 ms) on the Pi; a slower call is skipped for zone rules. That timeout sits inside the 500 ms event-end → edge budget. ONNX on the CPU is fine; running on the Hailo is optional, later.
- **Data.** Weights and datasets stay out of git, listed in a `data-manifest.json`.
- **Contract tests.** Your classifier must pass the classifier suite in `kitchen/shared/contract_tests/`.

## Runtime: the `lstm` classifier

Built: `infer.py` (`LstmClassifier`) runs the trained network as ONNX with onnxruntime on the CPU (the `ml` extra), so the kitchen never needs torch. **It is not the default anywhere.** Zone rules stay primary until the LSTM beats them on the frozen real-clip benchmark (safety rule 7).

**Export** (once per trained checkpoint; torch and onnx are pulled in for this one command, not added to the project). It scripts the network so the loop over steps stays an ONNX `Loop`, compares ONNX with torch on the example clip and random clips of 2, 12, 16 and 24 steps, refuses (and deletes) a file that disagrees by more than 1e-3 in a logit or gives a non-finite logit on either side, and adds `model.onnx` (name, size, sha256, source) to the folder's `data-manifest.json`:

```bash
uv run --with torch==2.14.1 --with onnx==1.23.2 python -m kitchen.lstm.export_onnx \
    --checkpoint data/models/lstm/boxpooled-v1/best.pt --out data/models/lstm/boxpooled-v1/model.onnx \
    --example data/examples/example_clip.npz
```

The ONNX model takes `clip` (float32 `[batch, steps, 3, 128, 128]`, 0 to 1) and `dt_ms` (float32 `[batch, steps]`) and returns `logits` (`[batch, 2]`: contact, material moved). Channels and frame size are fixed by the clip geometry; the step count is free, like the network's (it was trained on 16).

**Run.** `LstmClassifier(model_dir)` (default `LSTM_MODEL_DIR`, else `data/models/lstm/boxpooled-v1`) checks `model.onnx` against its manifest sha256 before loading it. `name` is `"lstm"`, `version` is `<folder>+<first 8 of the sha256>` (`boxpooled-v1+152b93cf` today). `classify(window)` reads only `window.tensor` (uint8 `[T, 3, 128, 128]`), the clip's `dt_ms` and its channel order, and returns `[("transfer.contact", P(contact)), ("transfer.scoop", P(material moved))]`, the mapping in AGENTS.md, Open questions. It never returns a guess: a missing, corrupt or unloadable model raises `LstmModelError`, and a window it wasn't built for (not uint8, wrong channels or frame size, fewer than 2 steps, `dt_ms` of the wrong length) or a non-finite output raises `ValueError`. Either way `kitchen/interactions/classifier.py` falls back to zone rules.

**Measured** (boxpooled-v1, onnxruntime 1.31, Apple M3 laptop):

| | |
|---|---|
| ONNX vs torch, max logit difference | 2.4e-6 (example clip and random clips of 2 to 24 steps) |
| Example clip, `p_contact` / `p_moved` | 0.99948910 / 0.99952169, vs 0.99948907 / 0.99952173 from training |
| Clips made from the example, logits (contact / moved) | change channel blanked (touch without movement): 3.7519 / -7.0128; boxes held apart and still: -13.1163 / -17.2835 |
| Latency per 16-step clip, CPU | 47 ms median (55 ms p95) with onnxruntime's default threads; 103 ms on one thread. With the laptop busy with other work, p95 rose to 230 to 360 ms |

Not yet measured on the Pi 5. Expect it several times slower than the laptop, which puts it past the 100 ms primary timeout (every such call loses its label to zone rules), so time it there before adoption, and remember every candidate pair in an event is one clip.

**Tests** (`uv run pytest kitchen/lstm`): the shared InteractionClassifier contract suite against `LstmClassifier`, the example clip's known scores and two clips made from it on which the outputs disagree (logits pinned within 1e-3, so swapped outputs or a constant answer fail), probabilities in [0, 1], wrong windows and non-finite outputs raising, the scores ignoring ids and features (`safety` tests), and a 16-step clip's p95 under the 100 ms primary timeout. The export's parity check (NaN refused) is in `test_export_onnx.py`, which needs torch and skips without it: `uv run --with torch==2.14.1 python -m pytest kitchen/lstm/tests/test_export_onnx.py`. Load and input-check tests run everywhere; the rest need the exported model and skip without it (CI has none). In a git worktree, point them at the main checkout's data:

```bash
LSTM_MODEL_DIR=<main checkout>/data/models/lstm/boxpooled-v1 \
LSTM_EXAMPLE_CLIP=<main checkout>/data/examples/example_clip.npz uv run pytest kitchen/lstm
```

## Until yours lands

Agents test the fallback path with `FakeClassifier` (platform), which returns scripted labels or fails or hangs on cue. Nothing agent-side waits on you.

## How to hand off

- Open PRs from `partner/<slug>` branches. Any internal layout is fine. Today `model.py` holds the networks, `train.py` trains them on sim clips (`python -m kitchen.lstm.train --data data/clips/v1 --out runs/lstm-v1`, on a machine with torch), `export_onnx.py` turns a checkpoint into ONNX, and `infer.py` is the `lstm` classifier (see [Runtime](#runtime-the-lstm-classifier)).
- Changing the clip spec or the interface is a contract change. Tell the Director, who runs it as an RFC (you ACK; sim and vision follow).
- Agents reach you with issues labeled `owner:partner`.

Open questions that touch you: OQ-19 (outputs), OQ-20 (clip geometry), OQ-12 (real clip recording), OQ-9 (edge direction). See [AGENTS.md §11](../../AGENTS.md#11-open-questions).
