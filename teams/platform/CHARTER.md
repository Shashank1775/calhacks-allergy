# Team charter: `platform`

## Mission

Make it possible for eight teams to build in parallel without stepping on each other. Platform provides one shared library, types generated from the contracts, a fake for every piece of shared state, a CI that catches drift, and an end-to-end harness that proves the pieces fit.

## Owns

- Paths: `contracts/` (the Director merges every change), `libs/`, `tools/`, `.github/`, `.claude/`. The authoritative list is [OWNERS](../../OWNERS).
- No pipeline stage. Platform owns the glue between them: the contract tooling, `libs/py-common` (`py_common`), generated TypeScript types (`libs/ts-contracts`), the `InMemoryStateClient`, `tools/e2e`, CI, and the GitHub bootstrap scripts.

## Does not own

- **What the contracts say.** The Director decides that through RFCs ([WORKFLOW.md](../../docs/agents/WORKFLOW.md#contract-changes-rfcs)). Platform keeps them valid, versioned and generated.
- **The real `StateClient`, the SpacetimeDB module, and the TS bindings generated from the module.** These belong to `state`.
- **Domain fakes**, which ship with their producers: `FakeJev` (intake), `ExactLaya` and `AdversarialLaya` (provenance), `ConsoleGate` and `ConsoleSpeaker` (experience), recorded `ObservationFrame` JSONL (perception), recorded `InteractionEvent` JSONL (interactions).
- **Golden scenario content.** Sim generates scenarios and any team may hand-write one. Platform validates them and runs them in CI.
- **`docs/`** (Director).

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| `py_common.models`: Pydantic v2 models generated from [`contracts/schemas`](../../contracts/schemas/) | every Python team | — |
| `py_common.interfaces`: every Protocol in [apis.md](../../contracts/apis.md) (`StateClient`, `JevClient`, `LayaClient`, `Transcriber`, `Speaker`, `GateDriver`, `InteractionClassifier`) and the `*Unavailable` exceptions | every Python team | — |
| `py_common.clock`: injectable `Clock` (`SystemClock`, `ManualClock`), required by [CONVENTIONS.md](../../docs/agents/CONVENTIONS.md#rules-that-keep-replay-deterministic) | every Python team | `ManualClock` |
| `StateClient` | every Python team | **`InMemoryStateClient`**, with failure injection and scenario loading |
| `py_common.scenarios`: loads and iterates `scenario` files in time order | provenance, sim, platform e2e | — |
| StateClient contract test suite, parametrized over implementations | state (runs it against the real client) | — |
| `libs/ts-contracts`: TypeScript types generated from the schemas | experience | — |
| `tools/e2e`: the end-to-end harness | Director, all teams | — |
| CI (`.github/workflows/`), `tools/validate_contracts.py`, `tools/check_ownership.py`, `tools/bootstrap_github.sh`, `tools/seed_issues.py` | everyone | — |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| `contracts/schemas`, `allergen_units.json`, fixtures | Director (via RFC) | — |
| Golden and generated scenarios | sim, any team via PR | the five hand-written scenarios in `contracts/scenarios/` |
| Real `StateClient` (for the M1 "real local SpacetimeDB" end-to-end run) | state | `InMemoryStateClient` |
| `FakeJev`, intake parse pipeline | intake | — |
| Provenance engine and verdict policy | provenance | — |
| `ConsoleGate`, `ConsoleSpeaker` | experience | — |

## Invariants we guard

Platform code is not on the verdict path, but its tools are how several invariants get enforced:

| Invariant | How platform guards it | Test |
|---|---|---|
| 6, allergen-blind classifier | Generated models use `extra="forbid"`, so an allergen field cannot be added to `InteractionFeatures` without an RFC. The invalid fixture `has-allergen-field.json` must be rejected by both JSON Schema and the model | `test_models_reject_invalid_fixtures` (`safety`) |
| 1, fail closed | `InMemoryStateClient` can be told to raise `StateUnavailable`, so every consumer can test its fail-closed path | `test_failure_injection_raises` |
| 5 and 9, time-forward and full history | The scenario loader yields events in `t_start_ms` order and never drops events; the fake never deletes interactions or old ticket revisions | `test_scenarios_time_ordered`, `test_revisions_kept` |
| 8, unknown history | `upsert_entities` in the fake preserves `first_seen_t_ms` | `test_upsert_preserves_first_seen` (`safety`) |
| 2, 5, 8, 9 end to end | CI runs every golden scenario through provenance and `tools/e2e` and compares against `expected` | `golden-scenarios` CI job |
| Rule 2, stay in your lane | `check_ownership.py` fails on unowned files and reports which teams a PR touches | CI `ownership` job |

## Stack and layout

- Python ≥ 3.11, uv workspace at the repo root with member globs `components/*` and `libs/*`, so adding a component needs no edit to root config. Tooling: ruff, pytest, pyright (basic). CI is GitHub Actions with `astral-sh/setup-uv`.
- Codegen: JSON Schema 2020-12 → Pydantic v2 (candidate: `datamodel-code-generator`), and → TypeScript (candidate: `json-schema-to-typescript`). **Unverified:** whether either tool handles the cross-file `$ref`s into `common.schema.json` and the `if/then` in `verdict`. platform-02 settles this. The fallback is hand-written models, checked against every fixture in CI.
- Layout:

```
libs/py-common/   pyproject.toml, src/py_common/{models/ (generated), interfaces.py, clock.py, scenarios.py, fakes/state.py}, tests/
libs/ts-contracts/  package.json, src/ (generated .d.ts), scripts/gen
tools/   validate_contracts.py, check_ownership.py, codegen/, e2e/, bootstrap_github.sh, seed_issues.py, backlog/
```

- Test command: `uv run pytest libs/py-common` and `uv run python -m tools.e2e --all-scenarios` (from M1).

## Deliverables by milestone

- **M0:** uv workspace and per-component CI (platform-01). Generated Pydantic models with a CI drift check (platform-02). Protocols and `Clock` (platform-03). `InMemoryStateClient` with failure injection and scenario loader (platform-04). The StateClient contract suite. Generated TS types. PR-level ownership and label checks. Hardened, idempotent bootstrap scripts. A lint for `.claude/agents` frontmatter.
- **M1:** `tools/e2e` harness on fakes: typed note → `FakeJev` → answered question → ticket → scenario events → provenance → verdict → `ConsoleGate` and `ConsoleSpeaker` (platform-05). CI runs golden scenarios on every PR touching provenance or contracts, and sim-generated scenarios nightly. The e2e harness swaps in the real `StateClient` against a local SpacetimeDB.
- **M2:** deterministic replay check: the same recorded JSONL produces byte-identical verdicts on every run. One-command stack start on laptop and Pi (`hardware` for the Pi half; the owner verifies).
- **M3:** CI plumbing for model evaluations (Laya vs exact reachability, LSTM vs zone rules) posted as artifacts. A checker that model artifacts carry a `data-manifest.json`.
- **M4:** failure-drill harness in `tools/e2e` (kill each component, assert HOLD or `fault`, never PASS). An offline-mode check with no network after setup. Demo runbook tooling.

## Working with other teams

- **Director:** platform proposes, the Director merges, for anything in `contracts/`. Tooling changes that affect every team are announced on the Program board before merge.
- **state:** platform defines the `StateClient` Protocol and the in-memory reference behavior; state implements the real one. Both run the same contract suite. A behavior difference is a bug in one of them, settled on a `type:request`.
- **sim:** the scenario loader and new scenario tags; nightly CI runs of generated scenarios.
- **Every producer team:** registers its fake with `tools/e2e`. Platform never writes another team's fake.
- **experience:** consumes `libs/ts-contracts`. Asks for generator changes through `type:request`.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Codegen tools mishandle 2020-12 cross-file `$ref` or `if/then` | Spike inside platform-02. Fall back to hand-written models checked against every fixture |
| `InMemoryStateClient` drifts from real SpacetimeDB behavior | One shared contract suite run against both, with state as co-reviewer on fake changes |
| Root `uv.lock` and CI workflow become merge hotspots with many agents | Glob workspace members. Teams may regenerate `uv.lock` in their own PRs: on conflict, rebase and run `uv lock`, never hand-merge. CI jobs path-filtered per component |
| CI time grows past usefulness | Path filters, `slow` marker runs nightly only, uv and npm caching |
| How Python reaches SpacetimeDB is undecided (OQ-1) | e2e runs on `InMemoryStateClient` until state-03 lands. The real-DB job is additive |
| Model and hardware questions (OQ-5, OQ-11) | Not platform's to answer. The harness only uses fakes for these |

## Definition of done (team additions)

- Generated code is reproducible: regenerating on a clean checkout produces no diff, and CI enforces it.
- Every tool has `--help`, works from the repo root, and is idempotent where it touches GitHub (`--dry-run` supported).
- CI changes are proven on the PR itself: link a run that fails when it should and passes when it should.
- No breaking change to a tool every team uses without a Program board notice one cycle ahead.

## Staffing

- **M0:** 3 Workers in parallel: codegen and models (platform-02, then TS types), interfaces and fake (platform-03 → platform-04 → contract suite, one careful Worker because the fake defines reference behavior), CI and tooling (platform-01, ownership and label checks, bootstrap, agent lint).
- **M1:** 2 Workers: one on the e2e harness, one on CI scenario jobs and the real-DB e2e job.
- **M2–M4:** 1–2 Workers, mostly on the replay, eval and failure-drill harnesses.
