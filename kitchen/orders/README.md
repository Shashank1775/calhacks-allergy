# kitchen/orders: Tickets and demo bins

**Owner:** team `orders` (agents).

Turns what a server types or says into exact allergen data, and never guesses. Laya reads ticket notes; anything it isn't sure about becomes a one-tap question. Bin profiles are hard-coded for the demo.

## What happens here

- The server types the note, or speaks it and Cartesia Ink transcribes it. The server confirms the transcript before it's parsed.
- Laya reads the note and returns constraints (allergen, severity, trace sensitivity) and exceptions ("coconut is fine"), each with a confidence.
- Every field below the question threshold (**0.85**) becomes a one-tap question. An allergen Laya scores below **0.15** is left out. The ticket can't be sent until every question is answered.
- On send, the ticket is written through `StateClient.put_ticket` as `forbidden` (every allergen in the answered draft) plus `pass_threshold` from the strictest severity: preference 0.80, intolerance 0.90, allergy 0.95, anaphylactic 0.99. Its ID is `t<number>`.
- Severity, trace sensitivity and exceptions stay in the draft. **An exception never narrows `forbidden`:** "coconut is fine" still forbids all of `tree_nut`.
- A late change ("also no dairy") re-parses the original note plus the change into a new draft for `revision + 1`, with the same question flow. On send it keeps the ticket's ID and number; `forbidden` keeps every allergen the old revision had unless a person answered "No" to it; `pass_threshold` never gets looser.
- At startup, `save_bins()` writes every bin in `DEMO_BINS` through `StateClient.put_bin_profile`. Edit that list to match the counter.

## Files

| File | What it does |
|---|---|
| `laya_parser.py` | `LayaParser` adapters: `LocalLayaParser` (the fine-tuned Laya, run locally) and `FakeLayaParser` (keyword rules, for tests and offline demos) |
| `tickets.py` | `TicketDesk`: note → draft with questions; answers; send; amend at `revision + 1` |
| `bins.py` | `DEMO_BINS`, the hard-coded bin profiles, and `save_bins()` |
| `transcribe.py` | `Transcriber` adapters: `FakeTranscriber` now, Cartesia Ink batch later ([decision](../../docs/decisions/cartesia-speech.md)) |
| `api.py` | The [Orders HTTP API](../../contracts/apis.md#orders-http-api). `uv run python -m kitchen.orders.api` |
| `eval/` | `demo_notes.json`, the controlled demo notes, and `run_demo.py`, which scores a parser on them |
| `fakes/http/` | Example request and response for every endpoint, for the tablet's mock server. Regenerate with `uv run python -m kitchen.orders.fakes.make_examples` |
| `training/` | Building Laya training rows, fine-tuning on a GPU machine, and evaluating a checkpoint ([labeling guide](training/labeling_guide.md)) |

## In and out

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Note text or audio, answers, send, amend | [Orders HTTP API](../../contracts/apis.md#orders-http-api) | outputs tablet screen |
| Out | Draft with questions | [`parse_result`](../../contracts/schemas/parse_result.schema.json) | outputs tablet screen |
| Out | Sent ticket (`ticket` table) | [`ticket`](../../contracts/schemas/ticket.schema.json) via [`StateClient`](../../contracts/apis.md#stateclient) | verdict, partner graph, outputs |
| Out | Bin profiles (`bin_profile` table) | [`allergen_profile`](../../contracts/schemas/allergen_profile.schema.json) via `StateClient.put_bin_profile` | partner graph, verdict, outputs |
| Uses | Laya | [`LayaParser`](../../contracts/apis.md#layaparser) | fine-tuned Laya ([decision](../../docs/decisions/laya-for-notes-and-labels.md)) |
| Uses | Cartesia Ink | [`Transcriber`](../../contracts/apis.md#transcriber-and-speaker) | external |

Bin entity IDs come from vision: `bin-4` is whatever sits in `slot-4`.

## Running it

```bash
uv run python -m kitchen.orders.api                          # keyword fake, port 8001
LAYA_MODEL_DIR=data/models/laya-v1 uv run python -m kitchen.orders.api
uv run python -m kitchen.orders.eval.run_demo --model data/models/laya-v1   # score the model on the demo notes
```

Until the partner's `StateClient` exists, `api.py` keeps tickets and bin profiles in memory (`InMemoryState`).

## Safety rules

See [Safety rules](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`.

| Rule | How we could break it | Test |
|---|---|---|
| No unanswered questions | `/send` accepts a draft with an open question, or a low-confidence field gets no question | `/send` returns `409 unanswered_questions` while any question is open; every field below the threshold gets a question |
| Fail closed | Laya down, an empty note or a malformed model answer produces an empty "no constraints" draft | `LayaParserUnavailable` becomes `503 laya_unavailable` and writes nothing; `StateUnavailable` on send leaves the draft unsent |
| Allergens, not contacts | Anything but allergen units in a ticket or bin profile | Every emitted ticket and profile validates against its schema; a typo in `DEMO_BINS` fails at startup |
| Late changes recheck everything | Dropping an allergen on amend, or keeping the old revision number | An amendment writes `revision + 1`; an allergen leaves only through an answered "No"; a parse that forgets an allergen can't drop it |

Team rules on top: **never drop a mention** (an allergen at any confidence above 0.15 becomes a constraint or a question) and **exceptions never narrow the ticket**.

## Tests

- Tests live in `kitchen/orders/tests/`. Run `uv run pytest kitchen/orders`.
- They use a dict-backed state and a fixed clock (`tests/conftest.py`), `FakeLayaParser`, and a scripted stand-in for the Laya model, so no test needs torch or a vendor.
- `test_question_wording_matches_training` pins the exact question wording the model was trained on. If it fails, retrain before changing it.

## Watch out for

- Laya is English-only and reads at most 512 tokens. The checkpoint is near chance until fine-tuned; `FakeLayaParser` keeps tests independent of it.
- Ambiguous notes ("no nuts": peanuts too?): ask, don't guess. A bare "nuts" asks about both tree nuts and peanuts.
- Speech-to-text errors ("says me" for sesame): the server confirms the transcript.
