# Contracts

The only things teams may depend on across team lines. If it is not in this folder, it is another team's private implementation detail and can change without notice.

| File | What it is |
|---|---|
| [`allergen_units.json`](allergen_units.json) | Seed data for the allergen unit table (the nine major allergens). |
| [`schemas/`](schemas/) | JSON Schema (draft 2020-12) for every message that crosses a team boundary. |
| [`apis.md`](apis.md) | HTTP endpoints and the Python interfaces (`StateClient`, `LayaParser`, `LayaClient`, ...) every team codes against. |
| [`state-tables.md`](state-tables.md) | SpacetimeDB tables and reducers, and which team is the single writer of each table. |
| [`fixtures/valid/<schema>/`](fixtures/valid/) | Example payloads that **must** validate. Use them in your tests. |
| [`fixtures/invalid/<schema>/`](fixtures/invalid/) | Payloads that **must not** validate. Each guards an invariant. |
| [`scenarios/`](scenarios/) | Golden kitchen scenarios with ground-truth verdicts (schema: `scenario`). The partner's graph, the verdict policy and the end-to-end harness must pass all of them. |

`uv run python tools/validate_contracts.py` checks all of the above. CI runs it on every PR.

## Rules

1. **Change contracts in their own PR.** It touches only `contracts/` and names the teams it affects. The Director merges it; a partner interface also needs the partner's OK. See [AGENTS.md §6](../AGENTS.md#6-how-work-flows).
2. **Single writer per table.** Each SpacetimeDB table has exactly one team that writes it ([state-tables.md](state-tables.md#writers)). Everyone else reads.
3. **Every interface has a fake.** The producing team ships it. For the partner's components, the platform team writes the fake, and the partner's real code must pass the same contract tests (`kitchen/shared/contract_tests/`).
4. **Generated types are generated.** Python models (`kitchen/shared/models.py`) and TypeScript types (`web/src/generated/`) are generated from `schemas/` by the platform team's tooling. Never hand-edit generated files; change the schema instead.
5. **Fixtures are part of the contract.** A schema change that breaks an existing valid fixture breaks its consumers, whatever the diff looks like.
6. **New invariant, new invalid fixture.** When an invariant can be expressed in JSON Schema, add a fixture under `fixtures/invalid/` that violates it.

## Adding a golden scenario

Any team can add one in a contracts PR. A good scenario is small (≤ 10 events), tests one idea, says in `description` why the expected decision is right, and tags the invariant it guards. Expected decisions must hold for any reasonable choice of transfer efficiency (`0 < k ≤ 1`). If the answer depends on a tuning constant, the scenario is a test of the tuning, not the logic, and belongs in the graph's or verdict's own tests.
