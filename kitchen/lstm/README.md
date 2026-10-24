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
- **The shared builders (platform team).** `kitchen/shared/change_mask.py` and `clips.py` (platform-12), plus `ScriptedClassifier` and the classifier contract tests.

The interactions step itself is yours too (`kitchen/interactions/`): candidate pairs, zone rules, live pair windows, the fallback wrapper, real-clip labeling and the adoption benchmark. Its suggested task list is on GitHub as issues labeled `owner:partner`.

## Rules you're held to

- **Invariant 7.** Zone rules stay the default. The ConvLSTM becomes primary only if it beats them on the frozen real-clip benchmark, recorded in an ADR that you and the interactions Manager both sign. If it fails to load, raises or times out, `kitchen/interactions/classifier.py` falls back to zone rules.
- **Budget.** It must fit the 500 ms event-end → edge budget on the Pi. ONNX on the CPU is fine; running on the Hailo is optional, later.
- **Data.** Weights and datasets stay out of git, listed in a `data-manifest.json`.
- **Contract tests.** Your classifier must pass the classifier suite in `kitchen/shared/contract_tests/`.

## Until yours lands

Agents test the fallback path with `ScriptedClassifier` (platform), which returns scripted labels or fails or hangs on cue. Nothing agent-side waits on you.

## How to hand off

- Open PRs from `partner/<slug>` branches. Any internal layout is fine; `model.py`, `train.py` and `infer.py` are only suggestions.
- Changing the clip spec or the interface is a contract change. Tell the Director, who runs it as an RFC (you ACK; sim and vision follow).
- Agents reach you with issues labeled `owner:partner`.

Open questions that touch you: OQ-19 (outputs), OQ-20 (clip geometry), OQ-12 (real clip recording), OQ-9 (edge direction). See [AGENTS.md §11](../../AGENTS.md#11-open-questions).
