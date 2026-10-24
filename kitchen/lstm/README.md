# kitchen/lstm: LSTM interaction classifier (partner)

**Owner:** the partner (a human). Agents never edit this folder. This README tells you what the agent teams expect from it, and what they're building around it.

## What this component does

It classifies a window of one entity pair's features as `transfer.contact`, `transfer.scoop`, `transfer.wipe`, `clean.wash` or `unimportant`, each with a probability. It runs on the Pi CPU, alongside the zone rules it hopes to replace.

## What the agents rely on

- **The interface:** [`InteractionClassifier`](../../contracts/apis.md#interactionclassifier): `name = "lstm"`, `version`, and `classify(window) -> list[(action, probability)]`.
- **Input:** a time-ordered window of [`interaction_features`](../../contracts/schemas/interaction_features.schema.json) rows for one pair. The schema has `additionalProperties: false`, so no allergen information can reach the model (invariant 6).
- **Adoption rule (invariant 7):** zone rules stay the default. The LSTM becomes the primary classifier only if it beats zone rules on the frozen held-out benchmark (`kitchen/interactions/benchmark.py`, task interactions-11), recorded in an ADR that you and the interactions Manager both sign. Even then, `kitchen/interactions/classifier.py` falls back to zone rules if the LSTM fails to load, raises or times out.
- **Budget:** inference has to fit within the 500 ms event-end → edge budget on the Pi CPU (ONNX is fine). Weights stay out of git; list them in a `data-manifest.json` and keep the files in `data/`.
- **Contract tests:** the classifier suite in `kitchen/shared/contract_tests/` (platform-06).

## What the agents give you

- **Training input:** per-pair feature windows exported by `kitchen/interactions/features.py` (interactions-12). The window spec (length, stride, padding) is agreed with you on that issue.
- **Labels:** the labeled clip dataset format, validator and labeling tool (interactions-09, interactions-10). Splits are by recording session, and the held-out manifest is hashed and frozen, so never train on it.
- **The benchmark:** a cost-weighted score where missed transfers and false washes cost the most (interactions-11).
- **Optional:** synthetic feature windows from sim, for pre-training.

## Until yours lands

The agents test the fallback path with `ScriptedClassifier` (platform, in `kitchen/shared/fakes/`), which returns scripted labels or fails or hangs on cue. Nothing agent-side is blocked on you.

## How to hand off

- Open PRs from branches named `partner/<slug>`. CI lets those touch partner folders; agent branches can't.
- Any internal layout is fine. `model.py`, `train.py` and `infer.py` are only suggestions.
- The interface in `contracts/apis.md` is the deal. If you need to change it, or need a new feature column (which is a contract change), tell the Director, who will run it as an RFC.
- Agents reach you with issues labeled `owner:partner`.

## Open questions that touch you

- OQ-12: who records and labels real clips.
- OQ-9: edge direction (the interactions ADR, interactions-04).

See [AGENTS.md §11](../../AGENTS.md#11-open-questions).
