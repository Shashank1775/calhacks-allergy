# calhacks-allergy

A kitchen allergen tracker. A camera on a Raspberry Pi with a Hailo-8 watches food prep and tracks every bin, tool, hand and plate. Every contact is logged as an edge in a provenance graph in SpacetimeDB. When a plate reaches the pass, the system works out which allergens could have reached it and holds the plate if its ticket forbids any of them.

> **Status: PLANNING.** The plan, contracts and code layout are in place. The files are skeletons that describe what goes where, with no product code yet. Agent implementation starts when the owner approves (see the phase gate in [AGENTS.md](AGENTS.md)).

## Where things are

| Path | What | Built by |
|---|---|---|
| [`AGENTS.md`](AGENTS.md) | **The one file agents read**: rules, roles, workflow, teams, roadmap, open questions | — |
| [`kitchen/`](kitchen/README.md) | All Python code, one folder per pipeline step | agents + partner |
| `kitchen/entities/`, `kitchen/lstm/`, `kitchen/graph/`, [`database/`](database/) | Entity store, LSTM, provenance graph, SpacetimeDB module | **partner** |
| [`web/`](web/) | Tablet, chef and expo screens | agents (outputs) |
| [`contracts/`](contracts/README.md) | Data shapes and interfaces shared between folders, plus golden test scenarios | agents (platform), Director merges |
| [`tools/`](tools/) | CI checks, GitHub setup, backlog seeding | agents (platform) |
| [`docs/decisions/`](docs/decisions/) | Architecture decision records | Director |

## For the partner

You own `kitchen/entities/`, `kitchen/lstm/`, `kitchen/graph/` and `database/`. Agents will never edit them; CI enforces that.

- **The interface agents code against** is in [`contracts/apis.md`](contracts/apis.md) (`EntityStore`, `ProvenanceGraph`, `InteractionClassifier`, `StateClient`). Each of your folders has a README explaining what's expected.
- **Until your code lands, agents use fakes** (`kitchen/shared/fakes/`). When it does, it must pass the contract tests in `kitchen/shared/contract_tests/`.
- **Open PRs from `partner/<slug>` branches.** Structure the insides of your folders however you like.

## For the owner: running the agents

See [AGENTS.md §14](AGENTS.md#14-running-the-org-claude-code). In short: approve the plan, run `tools/bootstrap_github.sh` and `tools/seed_issues.py`, then start the Director with `claude --agent director`.

## Checks

```bash
pip install jsonschema
python3 tools/validate_contracts.py
python3 tools/check_ownership.py
python3 tools/seed_issues.py --dry-run
```
