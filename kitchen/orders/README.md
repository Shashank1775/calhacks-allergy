# kitchen/orders: Tickets and bin labels

**Owner:** team `orders` (agents).

Turns what people type or say into exact allergen data, and never guesses. Two jobs, both through Laya and the same "ask when unsure" rule:

- **Tickets:** a server's note ("severe nut allergy, coconut is fine") becomes forbidden allergens.
- **Bins:** at shift start, each bin's label becomes an allergen profile.

## What happens here

- The server types the note, or speaks it and Cartesia Ink transcribes it. The server confirms the transcript before it's parsed.
- Laya reads the note and returns constraints (allergen, severity, trace sensitivity) and exceptions ("coconut is fine"), each with a confidence.
- Every field below `question_threshold` becomes a one-tap question. The ticket can't be sent until every question is answered.
- On send, the ticket is written through `StateClient`, with `pass_threshold` set from its strictest severity.
- A late change ("also no dairy") opens a new draft at `revision + 1` that goes through the same question flow.
- At shift start, a person types or pastes each bin's label. Laya reads it into allergen probabilities, the person checks or adjusts them on the tablet, and the profile is saved. Laya is calibrated, so an unsure answer is a middle probability that the graph carries forward, never a zero.

## Files

| File | What it does |
|---|---|
| `laya_parser.py` | `LayaParser` adapters: `FakeLayaParser` (keyword parser, for tests) and the fine-tuned Laya adapter |
| `tickets.py` | Note → draft with questions; answers; send; amend at `revision + 1` |
| `bins.py` | Bin label → `AllergenProfile`; save the checked profile through `StateClient.put_bin_profile` |
| `transcribe.py` | `Transcriber` adapters: `FakeTranscriber` now, Cartesia Ink batch later ([decision](../../docs/decisions/cartesia-speech.md)) |
| `api.py` | The [Orders HTTP API](../../contracts/apis.md#orders-http-api) |

Planned data: `eval/notes.jsonl`, the ticket-note eval set, and `fakes/http/`, example responses for every endpoint that outputs uses as its mock server.

## In and out

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Note text or audio, answers, send, amend, bin labels | [Orders HTTP API](../../contracts/apis.md#orders-http-api) | outputs tablet screen |
| Out | Draft with questions | [`parse_result`](../../contracts/schemas/parse_result.schema.json) | outputs tablet screen |
| Out | Sent ticket (`ticket` table) | [`ticket`](../../contracts/schemas/ticket.schema.json) via [`StateClient`](../../contracts/apis.md#stateclient) | verdict, partner graph, outputs |
| Out | Bin profile (`bin_profile` table) | [`allergen_profile`](../../contracts/schemas/allergen_profile.schema.json) via `StateClient.put_bin_profile` | partner graph, verdict, outputs |
| Uses | Laya | [`LayaParser`](../../contracts/apis.md#layaparser) | fine-tuned Laya ([decision](../../docs/decisions/laya-for-notes-and-labels.md)) |
| Uses | Cartesia Ink | [`Transcriber`](../../contracts/apis.md#transcriber-and-speaker) | external |

Bin entity IDs come from vision: `bin-4` is whatever sits in `slot-4`.

## Safety rules

See [Safety rules](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`.

| Rule | How we could break it | Test |
|---|---|---|
| No unanswered questions | `/send` accepts a draft with an open question, or a low-confidence field gets no question | `/send` returns `409 unanswered_questions` while any question is open. A property test checks that for any Laya output, every field below `question_threshold` gets a question |
| Fail closed | Laya down produces an empty "no constraints" ticket or an empty bin profile | `LayaParserUnavailable` returns `laya_unavailable` and writes nothing; the tablet falls back to manual entry. `StateUnavailable` on send leaves the draft unsent |
| Allergens, not contacts | Anything but allergen units in constraints or profiles | Every emitted ticket and profile validates against its schema (`additionalProperties: false`) |
| Late changes recheck everything | Editing a ticket in place, or dropping a constraint on amend | Amend writes `revision + 1` with the full set, and old revisions are untouched |

Team rules on top: **never drop a mention.** An allergen mentioned at any confidence becomes a constraint or a question. **Exceptions narrow only on explicit words**, and an exception below threshold becomes a question.

## Tests

- Tests live in `kitchen/orders/tests/`. Run `uv run pytest kitchen/orders`.
- They use `FakeStateClient` (platform) for every database call. No test reaches a vendor.
- We ship `FakeLayaParser`, `FakeTranscriber`, and `fakes/http/` example responses.
- The eval scorer prints per-field accuracy and `wrong_without_question`, and fails if that is above 0.

## Watch out for

- Laya is English-only, reads at most 512 tokens, and is near chance until fine-tuned. Low-confidence answers become questions, and `FakeLayaParser` keeps tests independent of the model.
- Ambiguous notes ("no nuts": peanuts too?): ask, don't guess. The metric that matters is wrong-without-question = 0.
- Speech-to-text errors ("says me" for sesame): the server confirms the transcript.
