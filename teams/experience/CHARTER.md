# Team charter: `experience`

## Mission

Make the system's decisions visible and physical: the gate that holds a plate, the voice that says why, and the screens servers, chefs and expo use. We never decide anything ourselves; we show exactly what intake, perception and provenance decided, and when in doubt we show HOLD.

## Owns

- Paths: `components/experience/` (see [OWNERS](../../OWNERS)).
- Pipeline stage 7 (Outputs) in [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#7-outputs--team-experience-repeated-mistakes-analysis-by-provenance):
  - **Web screens:** server tablet (ticket entry, one-tap questions), chef confirmation, expo board (held plates, chains, camera overlays, Repeated Mistakes view), and the cook's "start ticket" button.
  - **Gate service** (Python, on the Pi): implementations of [`GateDriver`](../../contracts/apis.md#gatedriver) for the servo, red/green light and buzzer, plus the verdict subscription that drives them.
  - **Voice:** implementations of [`Speaker`](../../contracts/apis.md#transcriber-cartesia-ink-and-speaker-cartesia-sonic), backed by Cartesia Sonic ("Hold seven").

## Does not own

| Thing | Owner |
|---|---|
| Parse logic, questions, the send rule | intake (we render their API) |
| Verdicts, chains, Repeated Mistakes analysis | provenance (we render `verdict`, `verdict_step`, `mistake_pattern`) |
| Video stream, plate binding, health endpoint | perception (we call their HTTP API) |
| SpacetimeDB TS bindings | state (`state-02`) |
| TS types generated from `contracts/schemas` | platform |
| Any SpacetimeDB table. We write none | — |

We change state only through the intake and perception HTTP APIs.

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| `GateDriver` | experience gate service; platform e2e harness | `ConsoleGate`, `MockGPIOGate` (records calls) |
| `Speaker` | experience gate service; platform e2e harness | `ConsoleSpeaker` |
| `ScenarioStateSource` (in-browser) | our tests; anyone demoing the UI without a database | Replays a `contracts/scenarios/*.json` file into the same store the SpacetimeDB source fills |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| [Intake HTTP API](../../contracts/apis.md#intake-http-api) | intake | Mock server (MSW) built from intake's `fakes/http/` example responses |
| [Perception HTTP API](../../contracts/apis.md#perception-http-api-on-the-pi): `/v1/plates/start`, `/stream.mjpg`, `/v1/health` | perception | Mock server and a still frame |
| Tables `ticket`, `ticket_constraint`, `bin_profile`, `chef_confirmation`, `entity`, `entity_risk`, `plate_binding`, `verdict`, `verdict_step`, `mistake_pattern` via TS bindings | state | `ScenarioStateSource` |
| [`StateClient`](../../contracts/apis.md#stateclient-python-interface) (gate service subscribes to `verdict`) | state (real), platform (fake) | `InMemoryStateClient` |
| Cartesia Sonic (vendor) | external | `ConsoleSpeaker` |

## Invariants we guard

| Invariant | How we can break it | Guarding test (`safety`) |
|---|---|---|
| 1. Fail closed | Gate opens on stale, missing or malformed data | Gate state machine tests: default is closed; only a `pass` verdict, for the plate at the pass, for the ticket's **current** revision, opens it; verdict feed silent longer than the configured limit, or perception health stale, means `fault`; any exception means `fault` |
| 2. Models only tighten | The UI recomputes or softens a decision on the client | UI test: the pass badge renders only from `verdict.decision == "pass"`; no client-side decision logic exists (code review checks this too) |
| 3. No unanswered questions | Tablet lets a server send with an open question | UI test: Send is disabled until `sendable`; a `409 unanswered_questions` from intake shows the open questions again |
| 9. Late changes recheck everything | Showing an old revision's PASS after an amendment | Gate and expo tests: a plate whose latest verdict is for an older ticket revision shows "rechecking" and the gate holds |

## Stack and layout

```
components/experience/
  web/                      TypeScript + Vite app; routes /tablet, /chef, /expo (framework: experience-02 ADR)
    src/state/              StateSource interface; ScenarioStateSource; SpacetimeDB source (after state-02)
    src/routes/             tablet, chef, expo
  src/experience/gate/      gate state machine; GateDriver adapters: console, mock_gpio, gpio (M2)
  src/experience/voice/     Speaker adapters: console, cartesia_sonic (M3); line formatter
  tests/                    Python tests (gate, voice)
```

Test commands: `npm --prefix components/experience/web test` (vitest) and `uv run --package experience pytest components/experience`. The GPIO library is picked in M2 once the hardware is known (OQ-11).

## Deliverables by milestone

- **M0:** expo board skeleton on `ScenarioStateSource` (`experience-01`); framework and layout ADR; gate state machine with `ConsoleGate` and `MockGPIOGate`; `ConsoleSpeaker` and the spoken-line formatter.
- **M1:** tablet screen with the question gate; expo board chain view with per-step confidence; live SpacetimeDB bindings; chef confirmation screen; gate service driven by verdicts through `StateClient`; "start ticket" button against a mock; Cartesia Sonic spike.
- **M2:** GPIO gate driver (servo, red/green, buzzer) verified on the device by the owner `hardware`; fault on a stale feed shown on the real gate `hardware`; camera overlays colored by `entity_risk` (OQ-6); "start ticket" against real perception.
- **M3:** Cartesia Sonic voice live behind `Speaker`, within the verdict latency budget.
- **M4:** Repeated Mistakes view with suggested fixes; amendment flow on the tablet with the recheck visible on expo; scripted gate failure drills (kill the feed, get `fault`); demo polish.

## Working with other teams

- **intake:** API shapes, error codes, how questions render. Their example responses are our mock.
- **perception:** `/v1/plates/start` semantics and error codes, stream URL, health, and CORS for browser calls to the Pi (request filed in M1).
- **state:** TS bindings and subscription queries for the expo board.
- **provenance:** what each reason code and chain field means on screen.
- **platform:** TS types from schemas, and npm jobs in CI.
- **owner:** hardware inventory (OQ-11), screen layout for the demo, voice choice and Cartesia keys.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| TS bindings arrive late | `ScenarioStateSource` behind the same `StateSource` interface, so every screen is built and tested first |
| Hardware not yet known (OQ-11) | `GateDriver` interface and `MockGPIOGate`; `hardware` tasks deferred to M2 with owner test scripts |
| Gate opens on stale data | Fault-on-stale state machine, `safety` tests, M4 failure drills |
| Overlay boxes (5 Hz) lag 30 FPS video (OQ-6) | Interpolate client-side; revisit burned-in overlays with perception |
| Browser can't reach the Pi (CORS, LAN) | Agree the CORS policy with perception early; a mock server in tests |
| Cartesia Sonic API unverified | Spike in M1; `ConsoleSpeaker` until then; voice is never on the critical path to HOLD |

## Definition of done (team additions)

- UI PRs include a screenshot of the change running on `ScenarioStateSource`.
- Any gate change is `safety-critical`, and updates the state-machine table in `src/experience/gate/README.md`.
- Hardware PRs include an owner test script on the issue.

## Staffing

- **M0:** 2–3 Workers: web skeleton, gate state machine, voice formatter.
- **M1:** 3–4 Workers in parallel: tablet, expo chain view, gate service, chef screen. All gate work goes to one careful Worker, with the safety reviewer on every gate PR.
