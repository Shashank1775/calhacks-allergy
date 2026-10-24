# kitchen/shared: models, interfaces, clock and fakes

**Owner:** team `platform` (agents). Generated files are regenerated, never edited.

Everything the other folders import to talk to each other. Every Python folder in `kitchen/` depends on this one; this one depends on nothing in `kitchen/`. If two teams need the same type, interface or fake, it lives here, generated from or written against [`contracts/`](../../contracts/README.md).

## What lives here

- **Models** generated from [`contracts/schemas`](../../contracts/schemas/): `Ticket`, `Entity`, `ObservationFrame`, `InteractionEvent`, `Verdict` and the rest, plus the small table dataclasses (`Zone`, `PlateBinding`, `ChefConfirmation`, `ChangeEvent`).
- **Interfaces**: every Protocol in [`contracts/apis.md`](../../contracts/apis.md) and its `*Unavailable` exception.
- **Clock**: the only way code reads time, so replays are deterministic.
- **Config**: typed settings loaded from the environment, with every safety constant in one place.
- **Fakes** for the database and for every partner-owned part, so agents never wait on the partner ([§4 Partner-owned areas](../../AGENTS.md#4-partner-owned-areas)).
- **Contract tests**: one suite per interface. Every fake passes it, and the partner's real code must pass it too.

## Files

| File | What it does |
|---|---|
| `models.py` | **Generated** Pydantic v2 models for every schema (`extra="forbid"`). Rebuild with `uv run python tools/gen_models.py`; CI fails if it is stale |
| `interfaces.py` | Protocols `StateClient`, `EntityStore`, `ProvenanceGraph`, `InteractionClassifier`, `JevClient`, `LayaClient`, `Transcriber`, `Speaker`, `GateDriver`, `Clock`, and their exceptions |
| `clock.py` | `SystemClock` (live) and `FixedClock` (tests and replay) |
| `config.py` | Settings base class: per-folder env prefix, secrets from env only, safety constants logged at startup |
| `fakes/` | `InMemoryStateClient` + scenario loader, `FakeEntityStore`, `FakeProvenanceGraph`, `ScriptedClassifier` |
| `contract_tests/` | Reusable pytest suites, one per interface, parametrized over implementations |
| `change_mask.py` | The one change-mask function (RGB frame pair → gradient mask), used by vision on real frames and by sim on rendered frames |
| `clips.py` | The one clip builder (boxes + change masks over time → `interaction_clip` + `[T, C, H, W]` tensor), used by sim and by the partner (live pair windows in `kitchen/interactions/`, and training) |

TypeScript types for `web/` are generated from the same schemas (task platform-07); see `web/README.md` for where they land.

## Rules

- **Generated is generated.** Never hand-edit `models.py` or the TS types. Change the schema through an RFC ([§6 How work flows](../../AGENTS.md#6-how-work-flows)), then regenerate.
- **Signatures match `contracts/apis.md` exactly.** A method that isn't in apis.md doesn't go in `interfaces.py`.
- **Fakes pass the same contract tests as the real thing.** If a fake and a real implementation disagree, one of them has a bug; settle it on an issue.
- **Fakes for partner parts do no domain math.** `FakeProvenanceGraph` answers from a scenario's ground truth (`expected`), never by propagating allergens, so it never duplicates the partner's work.
- **Fail-closed hooks.** Every fake can be told to raise its `*Unavailable` error, or to hang, so every consumer can test its HOLD path ([§3 Safety rules](../../AGENTS.md#3-safety-rules), invariant 1).
- **Invariant 6.** Generated models reject unknown fields, so an allergen field can never sneak into `InteractionFeatures` or a clip. `clips.py` has no parameter for object pixels, classes or kinds.
- **One implementation for train and live.** Nobody else writes change-mask or clip code. A test builds the same scene through the sim path and the live path and expects identical tensors.
- **Invariant 8.** `InMemoryStateClient` and `FakeEntityStore` never delete entities and never change `first_seen_t_ms` or `origin`.

## Tests

```bash
uv run pytest kitchen/shared
uv run pytest kitchen/shared -m safety
```

- Every file in `contracts/fixtures/valid/` parses with its model and every file in `fixtures/invalid/` is rejected.
- Every scenario in `contracts/scenarios/` loads into `InMemoryStateClient`.
- Every fake passes its suite in `contract_tests/`.

## Milestones

- **M0:** project setup and CI (platform-01), generated models (platform-02), interfaces and clock (platform-03), `InMemoryStateClient` and scenario loader (platform-04), partner fakes and contract suites (platform-06), TS types, change mask and clip builder (platform-12).
- **M1:** end-to-end harness in [`tests/e2e/`](../../tests/e2e/README.md) (platform-05). Golden scenarios in CI. The partner's real `StateClient` and graph plugged into the same suites and harness once they exist (`needs-partner`).
- **M2–M4:** deterministic replay check, model-evaluation CI artifacts, failure-drill harness.

## Risks

| Risk | Mitigation |
|---|---|
| Codegen mishandles cross-file `$ref` or the `if/then` in `verdict` | platform-02 tests every fixture. Rules the generator can't express get a hand-written validator with a test |
| Fakes drift from the partner's real behavior | One contract suite run against both. The partner reviews changes to fakes of their parts |
| `models.py` and `uv.lock` become merge hotspots | Regenerate, never hand-merge ([§8 Engineering conventions](../../AGENTS.md#8-engineering-conventions)) |
| How Python reaches SpacetimeDB is undecided (OQ-1, partner) | Everything runs on `InMemoryStateClient` until the partner's client exists |
