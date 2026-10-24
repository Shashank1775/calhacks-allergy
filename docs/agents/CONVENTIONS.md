# Engineering conventions

Defaults for every component. A team that needs to differ writes a team ADR.

## Languages and tools

| Area | Default |
|---|---|
| Python services and libraries | Python ≥ 3.11 (perception pins exactly what the Pi runs), managed with a **uv workspace** at the repo root. Every Python package under `components/<team>/` and `libs/` is a workspace member |
| Lint and format | `ruff check` and `ruff format` |
| Tests | `pytest`. Markers: `safety` (guards an invariant), `hardware` (needs a real device; skipped in CI), `slow` (more than 10 s; runs nightly, not on every PR) |
| Types | Type hints everywhere; `pyright` in basic mode |
| Web apps (experience) | TypeScript, Node ≥ 20, Vite. The framework is the experience team's ADR |
| SpacetimeDB module (state) | Rust, unless the state team's ADR picks another supported module language |
| ML training (interactions, sim) | PyTorch. Training code lives with the team; artifacts live outside git (see [WORKFLOW.md](WORKFLOW.md#data-and-large-files)) |

## Layout inside a component

```
components/<team>/
  README.md            how to run, test and demo it; which fakes it ships
  pyproject.toml       (Python) package <team>, depends on py-common
  src/<team>/          code
  tests/               unit and contract tests; tests/fixtures/ for small data
  data-manifest.json   pointers to large artifacts kept outside git (if any)
```

Python package names are the team IDs (`intake`, `perception`, ...). The shared library is `py_common`.

## Rules that keep replay deterministic

- **Inject time.** Logic never calls `time.time()` or `datetime.now()`. It takes a `Clock` from `py_common`, so recorded sessions and simulations replay identically.
- **Seed randomness.** Every RNG takes a seed from config. Tests fix it.
- **Pure core, thin edges.** Put logic in pure functions over contract models. Keep I/O (camera, network, GPIO, database) in thin adapters behind the interfaces in [contracts/apis.md](../../contracts/apis.md).
- **JSONL for streams.** Recorded frames, features and events are JSONL, one contract object per line, so any team can replay any other team's output.

## Configuration

- Config is a typed settings object loaded from environment variables with a per-component prefix (`PERCEPTION_`, `PROVENANCE_`, ...). Secrets come only from the environment.
- Every safety-relevant constant (thresholds, timeouts, transfer efficiencies) lives in one named config object, is logged at startup, and is recorded in each verdict's `versions`.

## Logging

- Structured JSON logs to stdout with `component`, `t_ms` and, where relevant, `ticket_id`, `entity_id` and `event_id`.
- Every HOLD logs every reason. Every caught exception on the verdict path logs at ERROR and produces a HOLD.

## Tests a component must have

1. **Contract tests:** everything the component emits validates against its schema; it accepts every valid fixture it consumes.
2. **Fake-swap test:** the component's main flow runs with every external dependency replaced by its fake.
3. **Safety tests** (if it touches the verdict path), marked `safety`. Property-based (`hypothesis`) where the invariant is a property, e.g. "adding a model flag never turns HOLD into PASS".
