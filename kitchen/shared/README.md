# kitchen/shared: models, interfaces, clock and fakes

**Owner:** team `platform` (agents). Generated files are regenerated, never edited.

Everything the other folders import to talk to each other. Every Python folder in `kitchen/` depends on this one; this one depends on nothing else in `kitchen/`. If two teams need the same type, interface or fake, it lives here, generated from or written against [`contracts/`](../../contracts/README.md).

## Files

| File | What it does |
|---|---|
| `models.py` | **Generated** Pydantic models for every schema (`extra="forbid"`), plus the table dataclasses `Zone`, `PlateBinding`, `EntityState` and `ChangeEvent`. Rebuild with `uv run python tools/gen_models.py` |
| `interfaces.py` | Every Protocol in [`contracts/apis.md`](../../contracts/apis.md) and its `*Unavailable` exception |
| `clock.py` | `SystemClock` (live) and `FakeClock` (tests and replay) |
| `config.py` | Settings base class: per-folder env prefix, secrets from env only, safety constants logged at startup |
| `fakes/` | `FakeStateClient` + scenario loader, `FakeEntityStore`, `FakeProvenanceGraph`, `FakeClassifier` |
| `contract_tests/` | One pytest suite per interface; every fake passes it, and the partner's real code must too |
| `change_mask.py` | The one change-mask function (RGB frame pair → gradient mask), used by vision on real frames and by sim on rendered frames |
| `clips.py` | The one clip builder (boxes + change masks over time → `interaction_clip` + `[T, C, H, W]` tensor), used by sim and by the partner |

TypeScript types for `web/` are generated from the same schemas into `web/src/generated/`.

## Rules

- **Generated is generated.** Never hand-edit `models.py` or the TS types. Change the schema in a contracts PR, then regenerate.
- **Signatures match `contracts/apis.md` exactly.** A method that isn't in apis.md doesn't go in `interfaces.py`.
- **Fakes pass the same contract tests as the real thing.** If a fake and a real implementation disagree, one of them has a bug.
- **Fakes for partner parts do no domain math.** `FakeProvenanceGraph` answers from a scenario's ground truth (`expected`), never by propagating allergens.
- **Fail-closed hooks.** Every fake can be told to raise its `*Unavailable` error, or to hang, so every consumer can test its HOLD path.
- **One implementation for training and live.** Nobody else writes change-mask or clip code. A test builds the same scene through the sim path and the live path and expects identical tensors. `clips.py` has no parameter for object pixels, classes or kinds.
- **Known-clean source.** `FakeStateClient` and `FakeEntityStore` never delete entities and never change `first_seen_t_ms` or `origin`.

## Tests

```bash
uv run pytest kitchen/shared
```

- Every file in `contracts/fixtures/valid/` parses with its model and every file in `fixtures/invalid/` is rejected.
- Every scenario in `contracts/scenarios/` loads into `FakeStateClient`.
- Every fake passes its suite in `contract_tests/`.

## Watch out for

- Codegen may mishandle cross-file `$ref` or the `if/then` in `verdict`. Test every fixture; anything the generator can't express gets a small hand-written validator with a test.
- Fakes can drift from the partner's real behavior. One contract suite runs against both, and the partner reviews changes to fakes of their parts.
