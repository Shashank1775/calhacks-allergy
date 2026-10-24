# kitchen/gate: Pass gate and voice

**Owner:** team `outputs` (agents). Also owns [`web/`](../../web/README.md).

**Pipeline step:** the last step. Makes each verdict physical (servo arm, red or green light, buzzer) and spoken ("Hold seven"). See [AGENTS.md §2](../../AGENTS.md#2-pipeline-and-code-map).

## What happens here

- The gate service runs on the Pi and subscribes to verdicts through `StateClient`.
- A pure state machine decides `idle`, `pass`, `hold` or `fault`. It is closed by default, and only a PASS for the plate at the pass, for its ticket's **current** revision, opens the arm.
- If the verdict feed goes silent past a set limit, vision's `/v1/health` goes stale, or anything throws, the gate goes to `fault`: arm closed, red light, buzzer pulsing.
- Every hold is spoken as a short line ("Hold seven"). Stale lines are dropped, not queued. Voice is never on the critical path to HOLD.
- We never decide anything. We render what the verdict team decided.

## Files

| File | What it does |
|---|---|
| `service.py` | The gate state machine (pure transitions) and the loop that feeds it verdicts and health |
| `driver.py` | `GateDriver` implementations: `ConsoleGate` and `MockGPIOGate` now, the GPIO driver in M2 |
| `voice.py` | `Speaker` implementations: `ConsoleSpeaker` now, Cartesia Sonic after the spike |
| `lines.py` | Verdict → short spoken line, with numbers as words |

### Gate state machine

| From | Event | To |
|---|---|---|
| any | start, or no verdict yet for the plate at the pass | `idle` (closed, red) |
| any | PASS verdict for the plate at the pass, for the ticket's current revision | `pass` (open, green) |
| any | HOLD verdict, or a verdict for an older revision | `hold` (closed, red, buzzer) |
| any | feed silent past the limit, vision health stale, `StateUnavailable`, or any exception | `fault` (closed, red, buzzer pulsing) |

This table is the spec. Any change to it is `safety-critical` and updates this table in the same PR.

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Verdicts and chains | [`verdict`](../../contracts/schemas/verdict.schema.json) via [`StateClient.subscribe`](../../contracts/apis.md#stateclient) | verdict team |
| In | Ticket revisions and numbers | [`ticket`](../../contracts/schemas/ticket.schema.json) via `StateClient` | orders |
| In | Vision health | [`GET /v1/health`](../../contracts/apis.md#vision-http-api-on-the-pi) | vision |
| Out | Arm, lights, buzzer | [`GateDriver`](../../contracts/apis.md#gatedriver) | hardware |
| Out | Spoken line | [`Speaker`](../../contracts/apis.md#transcriber-and-speaker) | speaker |

We write no database tables.

## Safety rules that apply

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). All gate work is `safety-critical` and gets the safety reviewer.

| Rule | How we could break it | Test (`safety`) |
|---|---|---|
| 1. Fail closed | Opening on stale, missing or malformed data | Default is closed. Silence past the limit, stale health, `StateUnavailable` or any exception gives `fault` |
| 2. Models only tighten | Recomputing or softening a decision | Only `verdict.decision == "pass"` opens. There is no decision logic here |
| 9. Late changes recheck everything | Keeping an old revision's PASS after an amendment | A verdict for an older ticket revision holds |

Every golden scenario run through `InMemoryStateClient` must drive `MockGPIOGate` to the scenario's expected decision.

## Fakes

- **We use:** `InMemoryStateClient` (platform) and a mocked vision health endpoint.
- **We ship:** `ConsoleGate`, `MockGPIOGate` (records every `show()` call), and `ConsoleSpeaker`. The platform e2e harness uses them.

## Tests

Tests live in `kitchen/gate/tests/`. Run `uv run pytest kitchen/gate`. The service runs on fakes with `uv run python -m kitchen.gate.service --fake` (documented once outputs-05 lands).

## Milestones

- **M0:** gate state machine with `ConsoleGate` and `MockGPIOGate` (outputs-03). `ConsoleSpeaker` and the line formatter (outputs-04).
- **M1:** gate service driven by verdicts through `StateClient` (outputs-05). Cartesia Sonic spike (outputs-11).
- **M2:** GPIO driver for servo, red/green light and buzzer, verified on the device by the owner `hardware`. Fault on a stale feed shown on the real gate `hardware`.
- **M3:** Cartesia Sonic live behind `Speaker`, within the verdict latency budget.
- **M4:** scripted failure drills (kill the feed, get `fault`).

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Hardware not known yet (OQ-11) | `GateDriver` interface plus `MockGPIOGate`. Hardware tasks wait for M2, each with an owner test script |
| Gate opens on stale data | The fault-on-stale state machine, `safety` tests and M4 drills |
| Cartesia Sonic API unverified | Spike in M1. `ConsoleSpeaker` until then |
| Audio output on the Pi unknown (OQ-11) | Part of the Sonic spike. Voice failures never touch the gate state |
