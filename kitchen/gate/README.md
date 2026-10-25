# kitchen/gate: Pass gate and voice

**Owner:** team `outputs` (agents). Also owns [`web/`](../../web/README.md).

The last step. Makes each verdict physical (servo arm, red or green light, buzzer) and spoken ("Hold seven").

## What happens here

- The gate service runs on the Pi and polls `StateClient` a few times a second: the zones, the plate at the pass, its ticket and its latest verdict. Polling means a database that stops answering shows up at once as `fault`. The `StateClient` must time out its own calls, because a call that hangs would freeze the gate in its last state.
- A pure state machine decides `idle`, `pass`, `hold` or `fault`. It is closed by default, and only a PASS for the plate at the pass, for its ticket's **current** revision, opens the arm.
- If the verdict feed goes silent past a set limit, vision's `/health` goes stale, or anything throws, the gate goes to `fault`: arm closed, red light, buzzer pulsing.
- Every hold is spoken as a short line ("Hold seven"). Stale lines are dropped, not queued. Voice is never on the critical path to HOLD.
- We never decide anything. We render what the verdict team decided.

## Files

| File | What it does |
|---|---|
| `service.py` | The gate state machine (pure transitions), and `GateService`, the loop that feeds it the pass and vision health each `tick()`. `--fake <scenario>` replays a golden scenario through `FakeGate` and `FakeSpeaker` |
| `driver.py` | `GateDriver` implementations: `FakeGate` now, the GPIO driver once the hardware is wired |
| `voice.py` | Verdict → short spoken line, and `Speaker` implementations: `FakeSpeaker` now, Cartesia Sonic later |

### Gate state machine

| From | Event | To |
|---|---|---|
| any | start, or no verdict yet for the plate at the pass | `idle` (closed, red) |
| any | two or more plates at the pass, each seen within `lost_plate_crowds_ms` | `hold` (closed, red, buzzer) |
| any | PASS verdict for the plate at the pass, for the ticket's current revision | `pass` (open, green) |
| any | HOLD verdict, or a verdict for an older revision | `hold` (closed, red, buzzer) |
| any | feed silent past the limit, vision health stale, `StateUnavailable`, or any exception | `fault` (closed, red, buzzer pulsing) |

**When a plate counts at the pass.** A plate lifted out of view keeps a last row that says pass, and nothing writes another. So for each plate in a pass zone, the gate checks how long it has gone unseen on vision's frame clock: the later of the newest `last_seen_t_ms` of any entity and vision's newest frame (`/health` `last_frame_t_ms`), minus the plate's own `last_seen_t_ms`. Both are frame times, so database lag can't drop a plate vision still has. The newest frame matters when frames stop and resume: the rows can't advance while vision sees nothing, so without it a plate that left during the stop would count at the pass until the first new rows landed. With it, that plate is gone at the first frame back, and the gate goes from `fault` to `idle`, never back to `pass`. The expo screen's gate panel does the same with its own `/health` poll, for live feeds only (a replay's rows run on the scenario's clock).

- **Up to `plate_lost_ms` (1000 ms): the plate is at the pass.** Only such a plate's verdict can drive the gate, and a lone plate lifted out goes idle 1 s after it was last seen. `plate_lost_ms` copies vision's `track_lost_ms` (`kitchen/vision/common.py`) and must change with it; `web/src/gate.ts` has the same constant.
- **Up to `lost_plate_crowds_ms` (5000 ms): it still counts toward two plates at the pass (hold).** A hand or a missed detection can hide a plate for over a second. Vision then ends its track, and when the plate is seen again it's a new, unbound entity: on its own it shows idle (closed), and beside another plate it crowds the pass. Without this window, the gate would open for a PASS plate beside the hidden one.
- **The gap that's left:** a plate hidden for over 5 s beside a PASS plate lets the gate open for that plate, until the hidden one is seen again. The demo keeps one plate at the pass at a time. A `safety` test pins this; change it only with this paragraph.
- **The cost:** after a plate is lifted out, the next plate can't pass for up to 5 s.
- **The newest frame's cost:** a plate's rows trail vision's newest frame by the entity sync (5 Hz) and, on the expo screen, the `/state` poll (500 ms). A plate still in view whose rows trail by over 1 s (a stalled sync, a slow database) drops out of the pass: `idle`, closed.

This table is the spec. Any change to it is `safety-critical` and updates this table in the same PR.

## In and out

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Verdicts and chains | [`verdict`](../../contracts/schemas/verdict.schema.json) via [`StateClient.subscribe`](../../contracts/apis.md#stateclient) | verdict team |
| In | Ticket revisions and numbers | [`ticket`](../../contracts/schemas/ticket.schema.json) via `StateClient` | orders |
| In | Vision health | [`GET /health`](../../contracts/apis.md#vision-http-api-on-the-pi) | vision |
| Out | Arm, lights, buzzer | [`GateDriver`](../../contracts/apis.md#gatedriver) | hardware |
| Out | Spoken line | [`Speaker`](../../contracts/apis.md#transcriber-and-speaker) | speaker |

We write no database tables.

## Safety rules

See [Safety rules](../../AGENTS.md#3-safety-rules). All gate work is `safety-critical`.

| Rule | How we could break it | Test (`safety`) |
|---|---|---|
| Fail closed | Opening on stale, missing or malformed data | Default is closed. Silence past the limit, stale health, `StateUnavailable` or any exception gives `fault` |
| Fail closed | Counting a plate that left while frames had stopped | Frames resuming without the plate: `idle` at the first frame back, before any new rows, never `pass` |
| Fail closed | Letting one plate through with another still at the pass | Two plates hold while each was seen within `lost_plate_crowds_ms`, including one hidden for over a second beside a PASS plate. The gap past 5 s is pinned by its own test |
| Plain-code verdict | Recomputing or softening a decision | Only `verdict.decision == "pass"` opens. There is no decision logic here |
| Late changes recheck everything | Keeping an old revision's PASS after an amendment | A verdict for an older ticket revision holds |

## Tests

- Tests live in `kitchen/gate/tests/`. Run `uv run pytest kitchen/gate`.
- They use `FakeStateClient` (platform), `FakeVerdictFeed` (verdict) and a fake vision health reader.
- Demo: `uv run python -m kitchen.gate.service --fake late-amendment-adds-dairy` prints the gate states and spoken lines. Every golden scenario run through `FakeStateClient` must drive `FakeGate` to the scenario's expected decision.
- We ship `FakeGate` (prints and records every `show()` call) and `FakeSpeaker`. The end-to-end harness uses them.

## Watch out for

- The exact servo, light, buzzer and speaker hardware isn't confirmed. `GateDriver` and `FakeGate` keep everything testable; hardware tasks end with a test script for the owner.
- Audio output on the Pi is unverified. Voice failures never touch the gate state.
