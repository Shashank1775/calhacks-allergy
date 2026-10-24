# Team charter: `intake`

## Mission

Turn what a server types or says into exact, typed allergen constraints, and never guess: anything we are unsure of becomes a one-tap question. Give every bin an allergen profile at shift start.

## Owns

- Paths: `components/intake/` (see [OWNERS](../../OWNERS)).
- Pipeline stages 1 (Order intake) and 2 (Entity profiling) in [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#stages).
- The [intake HTTP API](../../contracts/apis.md#intake-http-api) and the real and fake implementations of [`JevClient`](../../contracts/apis.md#jevclient) and [`Transcriber`](../../contracts/apis.md#transcriber-cartesia-ink-and-speaker-cartesia-sonic).
- Single writer of the SpacetimeDB tables `ticket`, `ticket_constraint`, `ticket_exception`, `bin_profile` and `chef_confirmation` ([state-tables.md](../../contracts/state-tables.md#writers)).
- The parse cache and the ticket-note eval set.

## Does not own

| Thing | Owner |
|---|---|
| Tablet and chef screens (we supply the logic, they render it) | experience |
| The `allergen_unit` table and its seed | state |
| Applying the pass threshold to a plate (we only record `pass_threshold` on the ticket) | provenance |
| Bin positions, slots and entity IDs | perception |
| Generated models, Protocols, `InMemoryStateClient` | platform |
| Answers to OQ-2, OQ-4 and OQ-5 | owner |

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| Intake HTTP API (`/v1/parse`, `/answers`, `/send`, `/amend`, `/transcribe`, `/profiles/bins`, `/menu/import`) | experience | Example request/response JSON for every endpoint and error code in `components/intake/fakes/http/`, for experience's mock server |
| [`parse_result`](../../contracts/schemas/parse_result.schema.json) | experience (via HTTP) | `FakeJev`-backed pipeline |
| [`ticket`](../../contracts/schemas/ticket.schema.json) rows: `ticket`, `ticket_constraint`, `ticket_exception` | provenance, experience | Tickets in `contracts/scenarios/` loaded by `InMemoryStateClient` |
| [`allergen_profile`](../../contracts/schemas/allergen_profile.schema.json) in `bin_profile` | provenance, experience | `FakeJev.profile_bin` |
| `chef_confirmation` rows | experience | Seeded rows in our HTTP fakes |
| `JevClient` | intake (internal), any team needing a parse in tests | `FakeJev`: a deterministic keyword parser covering the eval set |
| `Transcriber` | intake (internal) | `TextFileTranscriber` |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| [`StateClient`](../../contracts/apis.md#stateclient-python-interface) | state (real), platform (fake) | `InMemoryStateClient` |
| Generated models, Protocols, `Clock` | platform | — |
| [`allergen_units.json`](../../contracts/allergen_units.json) | contracts | — |
| Jev (vendor; backing unknown, OQ-5) | external | `FakeJev` |
| Cartesia Ink (speech to text) | external | `TextFileTranscriber` |
| Apify Actor (menu import, optional) | external | Recorded menu JSON fixture |

## Invariants we guard

| Invariant | How we can break it | Guarding test (`safety`) |
|---|---|---|
| 3. No unanswered questions | `/send` accepts a draft with an open question, or a low-confidence field slips through without a question | HTTP test: `/send` returns `409 unanswered_questions` for any draft with an open question. Property test (`hypothesis`): for any `JevClient` output, every field below `question_threshold` ends up with a question; the pipeline adds one if Jev didn't |
| 4. Forbidden allergens, not contacts | Writing anything but allergen units into constraints | Contract test: every emitted ticket validates against `ticket.schema.json` (`additionalProperties: false`) |
| 9. Late changes recheck everything | Editing a ticket in place instead of writing `revision + 1` with the full constraint set | Test: `/amend` + `/send` writes a new revision; old revision rows are untouched |
| 1. Fail closed | Jev down with no cache hit producing an empty "no constraints" ticket | Test: `JevUnavailable` with no cache hit returns error `jev_unavailable`; no ticket is written |

Two team rules on top of these:

- **Never drop a mention.** If a note mentions an allergen at any confidence, it becomes a constraint or a question. It is never silently left out.
- **Exceptions narrow only on explicit words.** An exception below threshold becomes a question.

## Stack and layout

Python package `intake` per [CONVENTIONS.md](../../docs/agents/CONVENTIONS.md). The HTTP framework is chosen in `intake-01` (FastAPI suggested). Tests never reach a vendor; network calls are blocked in the test session.

```
components/intake/
  src/intake/parse/       normalize note, cache, threshold check, question builder
  src/intake/jev/         JevClient adapters: fake.py now, real adapter after the spike
  src/intake/speech/      Transcriber adapters
  src/intake/profiling/   bin profiling, chef confirmation
  src/intake/api/         HTTP API
  eval/notes.jsonl        ticket-note eval set with expected constraints
  fakes/http/             example responses for experience
```

Test command: `uv run --package intake pytest components/intake`. Eval: `uv run --package intake intake-eval`.

## Deliverables by milestone

- **M0:** `FakeJev`, the parse pipeline and the HTTP skeleton (`intake-01`); eval set and scorer; parse cache; vendor spikes for Jev (OQ-5), Cartesia Ink and Apify.
- **M1:** question flow with the send gate (invariant 3); apply to `StateClient`; amendments at `revision + 1`; bin profiling and chef confirmation on `FakeJev`; `/v1/transcribe` on the fake; intake runs inside the platform e2e harness and against a real local SpacetimeDB.
- **M2:** intake runs on the demo LAN against live SpacetimeDB; the shift-start profiling workflow runs with real bin labels; latency budgets met (parse ≤ 3 s, cached ≤ 50 ms).
- **M3:** real Jev adapter scores the eval set at the owner's threshold, with **zero** wrong constraints above threshold; Jev bin profiling from label photos and prep sheets; Cartesia Ink live.
- **M4:** offline mode (warm cache, manual fallback); failure drill: Jev down means questions or manual entry, never a guess; optional Apify menu import.

None of intake's work needs the Pi. Voice capture on the tablet may need a human to test the microphone.

## Working with other teams

- **experience:** owns the screens that call our API. We agree error codes and question rendering with them, and we ship example responses before the real endpoints land.
- **state:** writer registry for our five tables. `StateClient` has no chef-confirmation methods yet; we file the RFC.
- **provenance:** reads `ticket` revisions and `pass_threshold`, and rechecks on amendment. The meaning of `trace_sensitive` comes from their ADR (OQ-8).
- **platform:** generated models, `InMemoryStateClient`, e2e harness slot for intake.
- **sim:** scenario tickets should use our default thresholds (OQ-4) so they stay realistic.
- **owner:** OQ-2 (sub-units like coconut), OQ-4 (thresholds), OQ-5 (what backs Jev), API keys, OQ-15 (budget).

## Risks and open questions

| Risk | Mitigation |
|---|---|
| We don't know what backs Jev (OQ-5) | Everything behind `JevClient`; `FakeJev` until the spike lands; the spike is filed `needs-human` for keys |
| Ambiguous notes ("no nuts": peanuts too?) | Ask, don't guess. The eval set has a hard-case section, and the metric that matters is wrong-without-question = 0 |
| Exceptions under coarse units ("coconut is fine" under `tree_nut`, OQ-2) | Store as a free-text ingredient on the parent unit until OQ-2 is answered |
| Speech-to-text errors ("sesame" heard as "says me") | Show the transcript for the server to confirm or edit before parsing; voice cases in the eval set |
| Thresholds unset (OQ-4) | Defaults from OQ-4 in one config object, logged at startup |
| Cost and offline (OQ-15, OQ-7) | Normalized-note cache; parse only on send or explicit request, not every keystroke |

## Definition of done (team additions)

- Any PR that changes parsing posts `intake-eval` output in the PR body, with wrong-without-question at 0.
- Every new or changed endpoint updates `fakes/http/` in the same PR.
- No test makes a network call.

## Staffing

- **M0:** 3 Workers in parallel: pipeline and API, eval set (good warm-up work), vendor spikes.
- **M1:** 3 Workers. The send gate (`intake-06`) goes to one careful Worker, and the safety reviewer reviews it. Amendments and profiling can run in parallel once `intake-01` lands.
