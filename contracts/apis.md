# APIs and interfaces

Signatures here are the contract. Types named after a schema (`Ticket`, `Entity`, ...) are the models generated from [`schemas/`](schemas/) into `kitchen/shared/models.py`. Every interface has a **fake**, so any part can be tested without the real thing.

| Interface | Real implementation | Fake | Fake owner |
|---|---|---|---|
| [`StateClient`](#stateclient) | **partner** (`database/` + client) | `InMemoryStateClient` | platform |
| [`EntityStore`](#entitystore) | **partner** (`kitchen/entities/`) | `FakeEntityStore` | platform |
| [`ProvenanceGraph`](#provenancegraph) | **partner** (`kitchen/graph/`) | `FakeProvenanceGraph` | platform |
| [`InteractionClassifier`](#interactionclassifier) | interactions (zone rules), **partner** (LSTM in `kitchen/lstm/`) | `ScriptedClassifier` | platform |
| [`JevClient`](#jevclient) | orders | `FakeJev` | orders |
| [`LayaClient`](#layaclient) | verdict (adapter), sim (the model) | `ExactLaya`, `AdversarialLaya` | verdict |
| Verdict feed (`verdict`, `verdict_step`, `entity_risk`, `mistake_pattern` rows) | verdict, partner graph | `ScriptedVerdictSource` (replays scenario verdicts into `InMemoryStateClient`) | verdict |
| [`Transcriber`](#transcriber-and-speaker) | orders | `TextFileTranscriber` | orders |
| [`Speaker`](#transcriber-and-speaker) | outputs | `ConsoleSpeaker` | outputs |
| [`GateDriver`](#gatedriver) | outputs | `ConsoleGate`, `MockGPIOGate` | outputs |
| [`Clock`](#clock) | platform | `FixedClock` | platform |

Fakes for partner-owned parts are written by the platform team against this file. The partner's real code must pass the same contract tests the fakes pass (`kitchen/shared/contract_tests/`).

All Protocols live in `kitchen/shared/interfaces.py` (owner: platform).

## StateClient

Real: **partner**. Fake: `InMemoryStateClient` (platform). Every Python component talks to SpacetimeDB only through this.

```python
class StateClient(Protocol):
    # shift (partner's shift CLI, run by a person at shift start)
    def start_shift(self, shift_id: str, wall_clock_start: datetime) -> None: ...

    # writes: each method has exactly one calling team (state-tables.md#writers)
    def put_ticket(self, ticket: Ticket) -> None: ...                                   # orders; amendment = same ticket_id, revision + 1
    def put_bin_profile(self, entity_id: str, profile: AllergenProfile) -> None: ...    # orders
    def open_chef_confirmation(self, entity_id: str, ingredient: str) -> str: ...       # orders; returns confirmation_id
    def answer_chef_confirmation(self, confirmation_id: str, allergen_unit_ids: list[str]) -> None: ...  # orders
    def set_zones(self, zones: list[Zone]) -> None: ...                                 # vision (calibration)
    def bind_plate(self, plate_id: str, ticket_id: str, t_ms: int) -> None: ...         # vision ("start ticket")
    def upsert_entities(self, entities: list[Entity]) -> None: ...                      # partner entity store, ~5 Hz
    def record_interactions(self, events: list[InteractionEvent]) -> None: ...          # interactions
    def put_entity_risk(self, entity_id: str, profile: AllergenProfile, t_ms: int) -> None: ...  # partner graph
    def record_verdict(self, verdict: Verdict) -> None: ...                             # verdict
    def put_mistake_pattern(self, pattern: MistakePattern) -> None: ...                 # verdict

    # reads
    def get_ticket(self, ticket_id: str, revision: int | None = None) -> Ticket | None: ...  # None = latest
    def get_entity(self, entity_id: str) -> Entity | None: ...
    def list_entities(self, kind: str | None = None) -> list[Entity]: ...
    def list_zones(self) -> list[Zone]: ...
    def get_plate_binding(self, plate_id: str) -> PlateBinding | None: ...
    def list_interactions(self, since_t_ms: int = 0, until_t_ms: int | None = None) -> list[InteractionEvent]: ...
    def list_verdicts(self, ticket_id: str | None = None) -> list[Verdict]: ...
    def list_open_chef_confirmations(self) -> list[ChefConfirmation]: ...
    def list_mistake_patterns(self) -> list[MistakePattern]: ...

    # change feed
    def subscribe(self, table: str, on_change: Callable[[ChangeEvent], None]) -> Callable[[], None]: ...
```

- `Zone`, `PlateBinding`, `ChefConfirmation` and `ChangeEvent` are small dataclasses in `kitchen/shared/models.py` mirroring [state-tables.md](state-tables.md).
- **Triggers are table changes.** "A plate reaches the pass" means an `entity` row with `kind: plate` whose `zone` is a zone of kind `pass`; the verdict service watches `subscribe("entity", ...)` for that transition. "A ticket was amended" means a new `ticket` revision (`subscribe("ticket", ...)`). The gate service watches `subscribe("verdict", ...)`.
- Every method raises `StateUnavailable` when the database can't be reached. On the verdict path that becomes HOLD (invariant 1).
- Writes are idempotent by primary key.
- **Fake:** dict-backed, same semantics including `subscribe`, and can load a `scenario` file.
- **Open (OQ-1, partner decides):** how Python reaches SpacetimeDB.

## EntityStore

Real: **partner** (`kitchen/entities/`). Fake: `FakeEntityStore` (platform). Runs on the Pi. Vision feeds it every frame; it keeps every frame's entities in memory and syncs to SpacetimeDB about 5 times a second.

```python
class EntityStore(Protocol):
    def ingest(self, frame: ObservationFrame) -> None: ...      # vision calls this for every frame
    def get(self, entity_id: str) -> Entity | None: ...
    def snapshot(self) -> list[Entity]: ...                      # current view of every entity seen this shift
    def start_sync(self, state: StateClient, hz: float = 5.0) -> None: ...
    def stop_sync(self) -> None: ...
    def last_sync_t_ms(self) -> int | None: ...                # for health checks; None before the first successful sync
```

- Never deletes an entity and never changes `first_seen_t_ms` or `origin` (invariant 8).
- **Fake:** applies frames to a dict; `start_sync` writes snapshots to whatever `StateClient` it gets.

## ProvenanceGraph

Real: **partner** (`kitchen/graph/`). Fake: `FakeProvenanceGraph` (platform). The graph builds itself from `StateClient` (entities, bin profiles, interactions); agents only query it.

```python
class ProvenanceGraph(Protocol):
    def reach(self, plate_id: str, as_of_t_ms: int) -> list[AllergenReach]: ...
        # exact reachability: every allergen unit with nonzero probability at the plate, each with its chain
    def subgraph(self, plate_id: str, ticket_id: str, as_of_t_ms: int, forbidden: list[str]) -> LayaRequest: ...
        # the plate's time-forward ancestors, ready to send to Laya
    def unknown_ancestors(self, plate_id: str, as_of_t_ms: int) -> list[str]: ...
        # entity IDs in the plate's ancestry with origin "unknown" and no wash since (invariant 8)
    def version(self) -> str: ...
```

- Raises `GraphUnavailable` on any failure. The verdict policy turns that into HOLD (invariant 1).
- Must satisfy invariants 5 (time-forward) and 8 (unknown history), and pass every golden scenario in [`scenarios/`](scenarios/).
- **Fake:** answers from a scenario file's ground truth (`expected[].allergens_reached`, `chain_event_ids`). It does no graph math, so it never duplicates the partner's work.

## InteractionClassifier

Real: **interactions** (zone rules, always available) and **partner** (LSTM, adopted only if it beats zone rules on held-out clips, invariant 7). Fake: `ScriptedClassifier` (platform): returns scripted labels, or errors or hangs on cue.

```python
class InteractionClassifier(Protocol):
    name: str      # "zone_rules" | "lstm"
    version: str
    def classify(self, window: Sequence[InteractionFeatures]) -> list[tuple[Action, float]]: ...
```

- Input is allergen-blind by construction (invariant 6).
- `kitchen/interactions/classifier.py` wraps both: it uses the LSTM only if adopted and healthy, and falls back to zone rules otherwise.

## JevClient

Owner: **orders**.

```python
class JevClient(Protocol):
    def parse_note(self, note: str, *, source: Literal["typed", "voice"]) -> ParseResult: ...
    def profile_bin(self, *, label_text: str | None = None, image_jpeg: bytes | None = None,
                    prep_sheet_text: str | None = None) -> AllergenProfile: ...
```

- Raises `JevUnavailable`. With no cached result, the tablet falls back to manual entry. It never guesses.
- **Fake:** `FakeJev`, a deterministic keyword parser covering the notes in the orders eval set.

## LayaClient

Owner: **verdict** (adapter, timeouts, validation) and **sim** (the model, its training data and evaluation).

```python
class LayaClient(Protocol):
    def analyze(self, request: LayaRequest, *, timeout_ms: int) -> LayaResponse: ...
```

- Raises `LayaUnavailable` on error or timeout. In `full` mode the verdict is then HOLD with reason `model_unavailable`.
- **Fakes:** `ExactLaya` answers with `ProvenanceGraph.reach`. `AdversarialLaya` gives random, late, empty or malformed answers that must never loosen a verdict.

## Transcriber and Speaker

Owners: **orders** (Transcriber, Cartesia Ink), **outputs** (Speaker, Cartesia Sonic).

```python
class Transcriber(Protocol):
    def transcribe(self, audio: bytes, *, sample_rate_hz: int) -> str: ...

class Speaker(Protocol):
    def say(self, text: str) -> None: ...   # non-blocking; drops stale lines rather than queueing them
```

- **Fakes:** `TextFileTranscriber` returns canned transcripts; `ConsoleSpeaker` prints the line.

## GateDriver

Owner: **outputs**.

```python
class GateDriver(Protocol):
    def show(self, state: Literal["idle", "pass", "hold", "fault"], ticket_number: int | None = None) -> None: ...
```

- `fault`: arm closed, red light, buzzer pulsing. Used when the gate loses its verdict feed (invariant 1).
- **Fakes:** `ConsoleGate`, and `MockGPIOGate`, which records calls for tests.

## Clock

Owner: **platform**. Logic never reads the wall clock (replays must be deterministic).

```python
class Clock(Protocol):
    def now_ms(self) -> int: ...     # t_ms since shift start
```

- **Fake:** `FixedClock`, advanced by hand in tests.

## HTTP conventions

Applies to the orders and vision HTTP APIs.

- JSON bodies. Errors are `{"error": {"code": str, "message": str}}`.
- Browser screens from `outputs` call these APIs directly, so each service sends CORS headers for the origins in its `<PREFIX>_CORS_ORIGINS` setting (default: the web dev server origin). There are no wildcard origins.
- Every service exposes `GET /v1/health`.

## Orders HTTP API

Owner: **orders** (`kitchen/orders/api.py`). Consumer: **outputs** (tablet and chef screens).

| Method and path | Body | Returns | Notes |
|---|---|---|---|
| `POST /v1/transcribe` | audio (`audio/wav`) | `{"text": str}` | Cartesia Ink |
| `POST /v1/parse` | `{"note": str, "source": "typed"\|"voice"}` | `ParseResult` | Cached by normalized note |
| `POST /v1/drafts/{draft_id}/answers` | `{"question_id": str, "value": any}` | `ParseResult` | `sendable` flips once all questions are answered |
| `POST /v1/drafts/{draft_id}/send` | `{"number": int}` | `Ticket` | `409 unanswered_questions` if not sendable (invariant 3) |
| `POST /v1/tickets/{ticket_id}/amend` | `{"note": str}` | `ParseResult` | New draft at revision + 1; same question flow; send triggers a recheck (invariant 9) |
| `POST /v1/profiles/bins` | `{"entity_id": str, "label_text"?: str, "image_jpeg_b64"?: str, "prep_sheet_text"?: str}` | `AllergenProfile` | Shift start |
| `POST /v1/profiles/bins/{entity_id}/confirm` | `{"ingredient": str, "allergen_unit_ids": [str]}` | `AllergenProfile` | Chef confirms an unclear ingredient |
| `POST /v1/menu/import` | `{"url": str}` | `{"items": int}` | Optional, via an Apify Actor |

## Vision HTTP API (on the Pi)

Owner: **vision** (`kitchen/vision/server.py`). Consumer: **outputs** ("start ticket" button, camera overlays).

| Method and path | Body | Returns | Notes |
|---|---|---|---|
| `POST /v1/plates/start` | `{"ticket_id": str}` | `{"plate_id": str}` | Binds the plate at the plating spot. Otherwise `409 no_plate_at_spot`, `409 multiple_plates_at_spot` or `409 plate_already_bound`; never guesses |
| `GET /stream.mjpg` | — | MJPEG | Raw camera stream |
| `GET /v1/health` | — | `{"fps": float, "detector": "onnx"\|"hailo", "last_frame_t_ms": int}` | `detector` is `onnx` until the Hailo is connected. The gate goes to `fault` if this goes stale |

Overlays are drawn client-side from `entity.box` plus `entity_risk` (OQ-6).
