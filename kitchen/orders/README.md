# kitchen/orders: Order intake

**Owner:** team `orders` (agents). Also owns [`kitchen/profiling/`](../profiling/README.md).

**Pipeline step:** 1 of the pipeline. Turns what a server types or says into exact, typed allergen constraints, and never guesses. Anything we're unsure of becomes a one-tap question. See [AGENTS.md §2](../../AGENTS.md#2-pipeline-and-code-map).

## What happens here

- The server types the note, or speaks it and Cartesia Ink transcribes it. The server confirms the transcript before it's parsed.
- Laya reads the note and returns constraints (allergen unit, severity, trace sensitivity) and exceptions ("coconut is fine"), each with a confidence.
- Every field below `question_threshold` becomes a one-tap question. The ticket can't be sent until every question is answered.
- Results are cached by normalized note, so a repeated note parses instantly and works offline.
- On send, the ticket is written through `StateClient`, with `pass_threshold` set from its strictest severity (OQ-4).
- A late change ("also no dairy") opens a new draft at `revision + 1` that goes through the same question flow.

## Files

| File | What it does |
|---|---|
| `laya_parser.py` | `LayaParser` adapters: `FakeLayaParser` (keyword parser, for tests) and the fine-tuned Laya adapter |
| `parse.py` | Normalize the note, check the cache, call Laya, and build the `ParseResult` draft |
| `questions.py` | Turn low-confidence fields into questions, fill answers, and decide `sendable` |
| `cache.py` | Normalized-note cache keyed by parser name and version |
| `apply.py` | Write a sent draft as a `Ticket` through `StateClient.put_ticket` |
| `amend.py` | Late changes: `revision + 1` with the full constraint set |
| `transcribe.py` | `Transcriber` adapters: `TextFileTranscriber` now, Cartesia Ink batch (`ink-whisper`) later, per [ADR 0013](../../docs/decisions/0013-cartesia-ink-and-apify-menu-import.md) |
| `api.py` | The [Orders HTTP API](../../contracts/apis.md#orders-http-api) |

Planned data (not files yet): `eval/notes.jsonl`, the ticket-note eval set (orders-02), and `fakes/http/`, example responses for every endpoint and error code that outputs uses as its mock server.

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Note text or audio, answers, send, amend | [Orders HTTP API](../../contracts/apis.md#orders-http-api) | outputs tablet screen |
| Out | Draft with questions | [`parse_result`](../../contracts/schemas/parse_result.schema.json) | outputs tablet screen |
| Out | Sent ticket (tables `ticket`, `ticket_constraint`, `ticket_exception`) | [`ticket`](../../contracts/schemas/ticket.schema.json) via [`StateClient`](../../contracts/apis.md#stateclient) | verdict, partner graph, outputs |
| Uses | Laya | [`LayaParser`](../../contracts/apis.md#layaparser) | fine-tuned Laya |
| Uses | Cartesia Ink | [`Transcriber`](../../contracts/apis.md#transcriber-and-speaker) | external |
| Uses | Allergen units | [`allergen_units.json`](../../contracts/allergen_units.json) | contracts |

We are the single writer of `ticket`, `ticket_constraint`, `ticket_exception`, `bin_profile` and `chef_confirmation` ([state-tables.md](../../contracts/state-tables.md#writers)).

## Safety rules that apply

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`.

| Rule | How we could break it | Test |
|---|---|---|
| 3. No unanswered questions | `/send` accepts a draft with an open question, or a low-confidence field gets no question | `/send` returns `409 unanswered_questions` while any question is open. A property test (`hypothesis`) checks that for any Laya output, every field below `question_threshold` gets a question, and the pipeline adds one if Laya didn't |
| 1. Fail closed | Laya down with no cache hit produces an empty "no constraints" ticket | A `LayaParserUnavailable` cache miss returns `laya_unavailable` and writes nothing. `StateUnavailable` on send leaves the draft unsent |
| 4. Allergens, not contacts | Anything but allergen units in constraints | Every emitted ticket validates against `ticket.schema.json` (`additionalProperties: false`) |
| 9. Late changes recheck everything | Editing a ticket in place, or dropping a constraint on amend | Amend writes `revision + 1` with the full set, and old rows are untouched. A constraint is removed only by an explicitly answered question |

Team rules on top: **never drop a mention.** An allergen mentioned at any confidence becomes a constraint or a question. **Exceptions narrow only on explicit words**, and an exception below threshold becomes a question.

## Fakes

- **We use:** `InMemoryStateClient` (platform) for every `StateClient` call. No test reaches a vendor; network calls are blocked in tests.
- **We ship:** `FakeLayaParser` (covers the eval set), `TextFileTranscriber`, and `fakes/http/` example responses. Every endpoint change updates `fakes/http/` in the same PR.

## Tests

Tests live in `kitchen/orders/tests/`. Run `uv run pytest kitchen/orders`. The eval scorer (orders-02) prints per-field accuracy and `wrong_without_question`, and fails if that is above 0. Any PR that changes parsing posts the scorer output.

## Milestones

- **M0:** `FakeLayaParser`, parse pipeline and API skeleton (orders-01). Eval set with hard cases. Parse cache. Spikes for Cartesia Ink and Apify.
- **M1:** question answers and send gate. Apply through `StateClient`. Amendments. `/v1/transcribe` on the fake. Runs in the e2e harness, and against the partner's real database (needs partner).
- **M2:** runs on the demo LAN against live SpacetimeDB. Latency budgets: parse ≤ 3 s online, ≤ 50 ms cached.
- **M3:** fine-tuned Laya scores the eval set with **zero** wrong constraints above threshold. Cartesia Ink live.
- **M4:** offline mode (warm cache, manual-entry fallback). Failure drill: Laya down means questions or manual entry, never a guess.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Laya is English-only and sometimes wrong | Low-confidence answers become one-tap questions; `FakeLayaParser` keeps tests independent of the model |
| Ambiguous notes ("no nuts": peanuts too?) | Ask, don't guess. Hard cases are in the eval set; the metric that matters is wrong-without-question = 0 |
| Exceptions under coarse units ("coconut is fine" under `tree_nut`, OQ-2) | Stored as a free-text ingredient on the parent unit until OQ-2 is answered |
| Speech-to-text errors ("says me" for sesame) | The server confirms the transcript; voice cases are in the eval set |
| Thresholds unset (OQ-4), cost and offline (OQ-15, OQ-7) | OQ-4 defaults in one logged config object. Parse on request, not on every keystroke |
