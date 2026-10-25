# calhacks-allergy

A kitchen allergen tracker. A camera on a Raspberry Pi with a Hailo-8 watches food prep and tracks every bin, tool, hand and plate. Every contact is logged as an edge in a provenance graph in SpacetimeDB. When a plate reaches the pass, the system works out which allergens could have reached it and holds the plate if its ticket forbids any of them.

> **Status: hackathon MVP, M0 in progress.** The Hailo-8 isn't connected yet, so everything runs locally and stays ready to upload. See [AGENTS.md](AGENTS.md).

## Where things are

| Path | What | Built by |
|---|---|---|
| [`AGENTS.md`](AGENTS.md) | **The one file agents read**: rules, roles, workflow, teams, roadmap, open questions | — |
| [`kitchen/`](kitchen/README.md) | All Python code, one folder per pipeline step | agents + partner |
| `kitchen/entities/`, `kitchen/interactions/`, `kitchen/lstm/`, `kitchen/graph/`, [`database/`](database/) | Entity store, interactions, ConvLSTM, provenance graph, SpacetimeDB module | **partner** |
| [`web/`](web/) | Tablet and expo screens | agents (outputs) |
| [`contracts/`](contracts/README.md) | Data shapes and interfaces shared between folders, plus golden test scenarios | agents (platform), Director reviews |
| [`tools/`](tools/) | Contract validator, local runner (`run_local.py`) | agents (platform) |
| [`docs/decisions/`](docs/decisions/) | One page per decision that's hard to reverse | — |

## For the partner

You own `kitchen/entities/`, `kitchen/interactions/`, `kitchen/lstm/`, `kitchen/graph/` and `database/`. Agents never edit them; CI enforces that.

- **The interface agents code against** is in [`contracts/apis.md`](contracts/apis.md) (`EntityStore`, `ProvenanceGraph`, `InteractionClassifier`, `StateClient`). Each of your folders has a README explaining what's expected.
- **Until your code lands, agents use fakes** (`kitchen/shared/fakes/`). When it does, it must pass the contract tests in `kitchen/shared/contract_tests/`.
- **Open PRs from `partner/<slug>` branches.** Structure the insides of your folders however you like.

## Running

```bash
uv sync --all-extras
uv run python tools/validate_contracts.py
uv run pytest -m 'not hardware and not slow'
```

### Getting the models

The learned models aren't in git (nothing over 1 MB goes in git). They're zips on this repo's GitHub releases, about 1.9 GB in all. The repo is private, so `gh` must be logged in to an account that can see it.

| Release | Asset | Unzips to | What it is |
|---|---|---|---|
| [`laya-models-v1`](https://github.com/Shashank1775/calhacks-allergy/releases/tag/laya-models-v1) | `laya-v1.zip` | `data/models/laya-v1/` | Laya note parser (`LAYA_MODEL_DIR`) |
| | `laya-plate-v2.zip` | `data/models/laya-plate-v2/` | Laya plate check (`LAYA_PLATE_MODEL_DIR`) |
| | `laya_training.zip` | `data/laya_training/` | Laya training data |
| [`lstm-models-v2_4`](https://github.com/Shashank1775/calhacks-allergy/releases/tag/lstm-models-v2_4) | `boxpooled-v2_4.zip` | `data/models/lstm/boxpooled-v2_4/` | The shipped ConvGRU interaction classifier (`LSTM_MODEL_DIR`) |
| | `boxpooled-v1.zip` | `data/models/lstm/boxpooled-v1/` | Its first version (the default `LSTM_MODEL_DIR`) |
| [`vision-model-v1`](https://github.com/Shashank1775/calhacks-allergy/releases/tag/vision-model-v1) | `stock-yolov8s.zip` | `data/models/stock-yolov8s/` | The laptop's YOLOv8s bundle (ONNX, the default `VISION_BUNDLE_DIR`) |
| | `stock-yolov8s-hailo8l.zip` | `data/models/stock-yolov8s-hailo8l/` | The Pi's Hailo-8L bundle |
| | `yolov8s-weights.zip` | `data/weights/` | Stock YOLOv8s and YOLOv8s-pose weights and their ONNX exports |

From the repo root:

```bash
# Laya: note parser and plate check (about 780 MB each), and the training data
gh release download laya-models-v1 -p laya-v1.zip -p laya-plate-v2.zip -D data/models
unzip -o data/models/laya-v1.zip -d data/models
unzip -o data/models/laya-plate-v2.zip -d data/models
gh release download laya-models-v1 -p laya_training.zip -D data
unzip -o data/laya_training.zip -d data

# The ConvGRU interaction classifier
gh release download lstm-models-v2_4 -p 'boxpooled-*.zip' -D data/models/lstm
unzip -o data/models/lstm/boxpooled-v2_4.zip -d data/models/lstm
unzip -o data/models/lstm/boxpooled-v1.zip -d data/models/lstm

# Vision: the laptop's bundle, the Pi's Hailo-8L bundle and the stock weights
gh release download vision-model-v1 -p stock-yolov8s.zip -p stock-yolov8s-hailo8l.zip -D data/models
unzip -o data/models/stock-yolov8s.zip -d data/models
unzip -o data/models/stock-yolov8s-hailo8l.zip -d data/models
gh release download vision-model-v1 -p yolov8s-weights.zip -D data
unzip -o data/yolov8s-weights.zip -d data
```

Each release's notes give every zip's size and sha256 (check one with `shasum -a 256 <zip>`), and each unzipped folder has a `data-manifest.json` with every file's size and sha256.

### The whole kitchen on one laptop

No camera and no partner database: `tools/run_local.py` serves the end-to-end harness's `Kitchen` (the Orders API, the verdict service and the gate loop on one shared `FakeStateClient`) on a live clock, replays a golden scenario's kitchen, and serves every table at `GET /state`.

```bash
uv run python tools/run_local.py knife-carries-peanut-to-plate
```

Notes are parsed by `FakeLayaParser` and plates checked by `FakeLayaCheck` unless `LAYA_MODEL_DIR` and `LAYA_PLATE_MODEL_DIR` point at the fine-tuned checkpoints (see `.env.example` and [Getting the models](#getting-the-models)). With them, add the `laya` package for the run; loading both takes about 15 s:

```bash
uv run --env-file .env --with laya==0.4.14 python tools/run_local.py knife-carries-peanut-to-plate
```

Then point the web app at it (see [Run it](web/README.md#run-it) in `web/README.md`) and open `/expo`.

To run the agents, start the Director with `claude --agent director` and give it a Worker concurrency cap ([Running the org](AGENTS.md#14-running-the-org-claude-code) in AGENTS.md).
