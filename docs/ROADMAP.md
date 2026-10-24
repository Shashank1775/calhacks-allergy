# Roadmap

> **Current phase: PLANNING.** Waiting for the owner to approve the plan. Nothing below has started.

Milestones are ordered by what the demo needs, not by component. After M1 there is always a working end-to-end demo on `main`, and every later milestone swaps a fake for a real part without breaking it. A milestone closes only when the owner signs off on its exit criteria on the Program board.

There are no dates yet (OQ-10). Once the owner sets a deadline, the Director turns these into time boxes.

## Phase gate: PLANNING → BUILD APPROVED

- The owner reviews ARCHITECTURE, the contracts, ORG and the charters, and answers the blocking open questions (marked **blocking** in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)).
- The Director flips the gate line in [AGENTS.md](../AGENTS.md) and here, seeds the backlog (`tools/seed_issues.py`), and starts the Managers.

## M0: Foundations. Everyone can build against fakes.

Exit criteria:

- [ ] `libs/py-common` has models generated from `contracts/schemas`, every Protocol in `contracts/apis.md`, and `InMemoryStateClient` that can load a scenario file.
- [ ] Every team has a package skeleton under `components/<team>/` with a README, a test command, and at least one test running in CI.
- [ ] Every producer's fake exists and is used in at least one consumer test (`FakeJev`, `ExactLaya`, `ConsoleGate`, `ConsoleSpeaker`, recorded frame JSONL, recorded event JSONL).
- [ ] The state team's ADR on how Python reaches SpacetimeDB (OQ-1) is merged, and a hello-world reducer call works from Python against a local SpacetimeDB.
- [ ] Sim generates at least 20 more scenarios that pass validation, beyond the five hand-written golden ones.
- [ ] Perception replays a recorded video on a laptop CPU and writes valid `ObservationFrame` JSONL.
- [ ] CI checks: contract validation, ownership, per-component lint and tests.

## M1: Vertical slice. A simulated kitchen produces correct verdicts on screen.

Exit criteria:

- [ ] End-to-end harness (`tools/e2e`): typed note → `FakeJev` parse → question answered → ticket in a real local SpacetimeDB → sim event stream → provenance exact reachability → verdict → expo board, `ConsoleGate` and `ConsoleSpeaker`.
- [ ] Every golden scenario in `contracts/scenarios/` passes through the harness in `rules_only` mode.
- [ ] Verdict policy has property tests for invariants 1, 2, 5, 8 and 9, and the safety reviewer has signed off.
- [ ] Expo board shows held plates with their chains and per-step confidence, live from SpacetimeDB.
- [ ] Server tablet screen: type a note, answer questions, send. Sending is impossible with an unanswered question.
- [ ] Zone-rule classifier turns recorded frame JSONL into valid edges.

## M2: Live on the Pi. The camera drives it.

Exit criteria (the owner verifies each `hardware` item on the device):

- [ ] YOLOv8 detection and pose on the Hailo-8 with ByteTrack at ≥ 30 FPS. `hardware`
- [ ] Bin slots and plating spot calibrated. "Start ticket" binds the plate.
- [ ] Zone-rule interactions run live and write edges at the target latency.
- [ ] Pass gate: servo, red/green light and buzzer driven by verdicts. Gate goes to `fault` when the feed goes stale. `hardware`
- [ ] Camera overlays color entities by allergen status.
- [ ] A recorded live session replays deterministically through the whole pipeline.

## M3: Models. Learned parts, gated by evaluation.

Exit criteria:

- [ ] Jev parses the intake eval set at a threshold the owner sets, and every miss becomes a question rather than a wrong constraint.
- [ ] Bin profiling from label photos and prep sheets, with chef confirmation of unclear items.
- [ ] Laya fine-tuned on sim data. On held-out sim it never misses an allergen exact reachability finds on the same graph; it may only add. The `full` mode latency budget is met.
- [ ] LSTM benchmarked against zone rules on held-out labeled clips. It is adopted only if it wins; the decision is recorded in an ADR.
- [ ] Cartesia Ink (voice tickets) and Cartesia Sonic ("Hold seven") live.

## M4: Demo hardening. It survives a live demo.

Exit criteria:

- [ ] Repeated Mistakes view with suggested fixes.
- [ ] Late change ("also no dairy") rechecks the plate's full history live.
- [ ] Offline mode: cached parses, local SpacetimeDB, no internet needed after setup.
- [ ] Failure drills: killing any one component makes the gate HOLD or `fault`, never PASS. Each drill is scripted.
- [ ] Optional: Apify menu import seeds prep sheets.
- [ ] Demo script written and rehearsed three times by the owner.
