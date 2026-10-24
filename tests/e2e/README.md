# tests/e2e: end-to-end harness

**Owner:** team `platform` (agents).

Proves the pieces fit. It runs a whole kitchen through the system, with every component used only through its published interface ([`contracts/apis.md`](../../contracts/apis.md)), and checks the verdicts against known answers. This is the only place golden scenarios run end to end; the verdict team's own golden runner checks the policy alone.

## What it runs

```
typed note ──▶ Orders HTTP API (FakeLayaParser) ──▶ one-tap answers ──▶ ticket ──▶ StateClient
scenario file ──▶ bins, plate bindings, interaction events ──▶ StateClient
plate reaches the pass ──▶ VerdictService (FakeProvenanceGraph + FakeLayaCheck) ──▶ verdict ──▶ StateClient
verdict ──▶ GateService ──▶ FakeGate + FakeSpeaker
```

- **Default:** everything on fakes under one `FakeClock`: `FakeStateClient`, `FakeLayaParser`, `FakeTranscriber`, `FakeProvenanceGraph`, `FakeLayaCheck`, `FakeGate`, `FakeSpeaker`. Vision is simulated: the harness writes the `entity_state` row a plate gets when it enters the pass, and reports a fresh frame every gate tick.
- **With the partner's parts** (`needs-partner`): `--state real` and `--graph real` select the partner's `StateClient` and `ProvenanceGraph`. Until they're wired into `conftest.py`, those runs fail with a message, so a run that never touched the partner's code can't look green. The expected results are identical.

| File | What it does |
|---|---|
| `conftest.py` | `Kitchen`: one shift wired from each step's public entry point (the Orders API via FastAPI's test client, `VerdictService`, `GateService`), plus the `kitchen` fixture. [`tools/run_local.py`](../../tools/run_local.py) serves this same `Kitchen` on a live clock |
| `test_golden_scenarios.py` | Every golden scenario end to end, judged from what the system wrote: the verdict row (decision, flagged allergens, Laya's flags, reasons, chain), the gate state when it landed, and the spoken holds. Also determinism across processes and hash seeds |
| `test_typed_note.py` | A typed note through orders (refused while questions are open), the plate at the pass, then a late amendment through orders that rechecks the plate and closes the gate. Also: an amendment Laya misreads still keeps the allergen, and the gate holds on a new revision before its recheck lands |
| `test_local_runner.py` | The local runner: every golden scenario played on its own clock with its tickets typed through the Orders API, `GET /state` (every table in `contracts/state-tables.md`, read-only, CORS for the web origin only), a tablet ticket showing up beside the scenario's, and the gate still ticking after a failed step |

## Commands

```bash
uv run pytest tests/e2e                                  # every golden scenario + the typed-note flow, on fakes
uv run pytest tests/e2e --state real                     # partner's StateClient against a local SpacetimeDB (fails until wired in)
uv run pytest tests/e2e --graph real                     # partner's ProvenanceGraph instead of the fake (fails until wired in)
uv run python tools/run_local.py late-amendment-adds-dairy   # the same shift, live: Orders API + GET /state on :8001
```

## What passing means

- For every file in [`contracts/scenarios/`](../../contracts/scenarios/), each plate's verdict matches `expected`: decision, allergens reached, reason codes, and chain event IDs where given.
- The typed-note flow cannot send a ticket with an unanswered question, and the stored ticket validates against `ticket.schema.json`.
- Two separate Python processes with different hash seeds produce byte-identical output (deterministic under `FakeClock`).
- The harness imports no team internals, only `kitchen.shared` interfaces and each folder's public entry point.

A failure here is a bug against the team whose output first diverges from `expected`. It is never fixed by editing the scenario.
