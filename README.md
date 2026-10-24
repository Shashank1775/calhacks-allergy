# calhacks-allergy

A kitchen allergen tracker. A camera on a Raspberry Pi with a Hailo-8 watches food prep, tracks every bin, tool, hand and plate, and logs every contact as an edge in a provenance graph in SpacetimeDB. When a plate reaches the pass, the system works out which allergens could have reached it and holds the plate if its ticket forbids any of them.

> **Status: PLANNING.** The architecture, contracts and agent org are drafted. No product code yet. Implementation starts when the owner approves. See the phase gate in [AGENTS.md](AGENTS.md).

## Built by agent teams

This repo is built by many AI agents working in parallel: a Director, eight team Managers, and Workers under each Manager. Everything they need is in the repo.

| Start here | |
|---|---|
| [AGENTS.md](AGENTS.md) | Rules every agent follows |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The system, its nine safety invariants, and the team boundaries |
| [docs/agents/ORG.md](docs/agents/ORG.md) | Roles, teams, ownership |
| [docs/agents/WORKFLOW.md](docs/agents/WORKFLOW.md) | Issues, branches, PRs, reviews, RFCs |
| [docs/agents/LAUNCH.md](docs/agents/LAUNCH.md) | How to start the org with Claude Code |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones M0–M4 and their exit criteria |
| [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) | Decisions waiting on the owner |
| [contracts/](contracts/README.md) | Schemas, interfaces and golden scenarios shared between teams |
| `teams/<team>/CHARTER.md` | What each team owns and delivers |

## Teams

`platform` · `intake` · `state` · `perception` · `interactions` · `provenance` · `sim` · `experience`. See [ORG.md](docs/agents/ORG.md#teams).

## Checks

```bash
pip install jsonschema
python3 tools/validate_contracts.py
python3 tools/check_ownership.py
python3 tools/seed_issues.py --dry-run
```
