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

### The whole kitchen on one laptop

No camera and no partner database: `tools/run_local.py` serves the end-to-end harness's `Kitchen` (the Orders API, the verdict service and the gate loop on one shared `FakeStateClient`) on a live clock, replays a golden scenario's kitchen, and serves every table at `GET /state`.

```bash
uv run python tools/run_local.py knife-carries-peanut-to-plate
```

Notes are parsed by `FakeLayaParser` and plates checked by `FakeLayaCheck` unless `LAYA_MODEL_DIR` and `LAYA_PLATE_MODEL_DIR` point at the fine-tuned checkpoints (see `.env.example`). With them, add the `laya` package for the run; loading both takes about 15 s:

```bash
uv run --env-file .env --with laya==0.4.14 python tools/run_local.py knife-carries-peanut-to-plate
```

Then point the web app at it (see [Run it](web/README.md#run-it) in `web/README.md`) and open `/expo`.

To run the agents, start the Director with `claude --agent director` and give it a Worker concurrency cap ([Running the org](AGENTS.md#14-running-the-org-claude-code) in AGENTS.md).
