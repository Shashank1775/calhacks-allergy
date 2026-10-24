# APIs and interfaces

Signatures here are the contract. Types named after a schema (`Ticket`, `Entity`, ...) are the models generated from [`schemas/`](schemas/). Every interface has a fake owned by the producing team; the "Fake" line says what it must do.

## Python interfaces

All Python interfaces live in `libs/py-common` (owner: platform) as `typing.Protocol` classes. Implementations live with the owning team.

### StateClient (Python interface)

Owner: **state** (real implementation), **platform** (in-memory fake). Every Python component talks to SpacetimeDB only through this interface.

```python
class StateClient(Protocol):
    # shift (state's shift CLI, run by the owner)
    def start_shift(self, shift_id: str, wall_clock_start: datetime) -> None: ...

    # writes: each method is called by exactly one team (see state-tables.md#writers)
    def put_ticket(self, ticket: Ticket) -> None: ...                       # intake; amendment = same ticket_id, revision + 1
    def put_bin_profile(self, entity_id: str, profile: AllergenProfile) -> None: ...   # intake
    def open_chef_confirmation(self, entity_id: str, ingredient: str) -> str: ...      # intake; returns confirmation_id
    def answer_chef_confirmation(self, confirmation_id: str, allergen_unit_ids: list[str]) -> None: ...  # intake
    def set_zones(self, zones: list[Zone]) -> None: ...                     # perception (calibration)
    def upsert_entities(self, entities: list[Entity]) -> None: ...          # perception, batched at ~5 Hz
    def bind_plate(self, plate_id: str, ticket_id: str, t_ms: int) -> None: ...        # perception (cook taps "start ticket")
    def record_interactions(self, events: list[InteractionEvent]) -> None: ...          # interactions
    def put_entity_risk(self, entity_id: str, profile: AllergenProfile, t_ms: int) -> None: ...  # provenance
    def record_verdict(self, verdict: Verdict) -> None: ...                 # provenance
    def put_mistake_pattern(self, pattern: MistakePattern) -> None: ...     # provenance

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

- `Zone`, `PlateBinding` and `ChefConfirmation` are small dataclasses in `py_common` that mirror their tables in [state-tables.md](state-tables.md). They have no JSON Schema yet because no stream carries them across a process boundary.
- Every method raises `StateUnavailable` when the database cannot be reached. Code on the verdict path turns that into HOLD (invariant 1).
- Writes are idempotent by primary key, so retries are safe.
- **Fake:** `InMemoryStateClient`, a dict-backed store with the same semantics, including `subscribe`. It can load a `scenario` file.
- **Open:** how Python reaches SpacetimeDB (HTTP reducer calls, a bridge process, or a community SDK) is the state team's first ADR (OQ-1).

### JevClient

Owner: **intake**.

```python
class JevClient(Protocol):
    def parse_note(self, note: str, *, source: Literal["typed", "voice"]) -> ParseResult: ...
    def profile_bin(self, *, label_text: str | None = None, image_jpeg: bytes | None = None,
                    prep_sheet_text: str | None = None) -> AllergenProfile: ...
```

- Raises `JevUnavailable`. With no cached result, the UI falls back to manual entry. It never guesses.
- **Fake:** `FakeJev`, a deterministic keyword parser that covers the notes in the intake eval set.

### LayaClient

Owner: **provenance** (client and runtime integration), **sim** (the model, its training and its evaluation).

```python
class LayaClient(Protocol):
    def analyze(self, request: LayaRequest, *, timeout_ms: int) -> LayaResponse: ...
```

- Raises `LayaUnavailable` on error or timeout. In `full` mode the verdict is then HOLD with reason `model_unavailable`.
- **Fake:** `ExactLaya`, which answers with exact reachability, plus `AdversarialLaya` for tests: random, late, or malformed answers that must never loosen a verdict.

### Transcriber (Cartesia Ink) and Speaker (Cartesia Sonic)

Owners: **intake** (Transcriber), **experience** (Speaker).

```python
class Transcriber(Protocol):
    def transcribe(self, audio: bytes, *, sample_rate_hz: int) -> str: ...

class Speaker(Protocol):
    def say(self, text: str) -> None: ...   # non-blocking; drops stale lines rather than queueing them
```

- **Fakes:** `TextFileTranscriber` returns canned transcripts; `ConsoleSpeaker` prints the line.

### GateDriver

Owner: **experience**.

```python
class GateDriver(Protocol):
    def show(self, state: Literal["idle", "pass", "hold", "fault"], ticket_number: int | None = None) -> None: ...
```

- `fault` means the gate stays closed with the red light on and the buzzer pulsing. It is used when the gate loses contact with the verdict feed (invariant 1).
- **Fake:** `ConsoleGate`, plus a GPIO-free `MockGPIOGate` that records calls for tests.

### Classifier

Owner: **interactions**.

```python
class InteractionClassifier(Protocol):
    name: str      # "zone_rules" | "lstm"
    version: str
    def classify(self, window: Sequence[InteractionFeatures]) -> list[tuple[Action, float]]: ...
```

- Input is allergen-blind by construction (invariant 6).
- Zone rules are the always-available fallback (invariant 7).

## HTTP conventions

Applies to the intake and perception APIs.

- JSON bodies. Errors are `{"error": {"code": str, "message": str}}`.
- Browser apps from `experience` call these APIs directly, so each service sends CORS headers for the origins in its `<PREFIX>_CORS_ORIGINS` setting (default: the experience dev server origin). There are no wildcard origins.
- Every service exposes `GET /v1/health`.

## Intake HTTP API

Owner: **intake**. Consumer: **experience** (server tablet and chef screens).

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

## Perception HTTP API (on the Pi)

Owner: **perception**. Consumer: **experience** (cook's "start ticket" button, camera overlays).

| Method and path | Body | Returns | Notes |
|---|---|---|---|
| `POST /v1/plates/start` | `{"ticket_id": str}` | `{"plate_id": str}` | Binds the plate currently at the plating spot and writes `plate_binding`. Otherwise `409 no_plate_at_spot`, `409 multiple_plates_at_spot` or `409 plate_already_bound`; never guesses |
| `GET /stream.mjpg` | — | MJPEG | Raw camera stream |
| `GET /v1/health` | — | `{"fps": float, "hailo": bool, "last_frame_t_ms": int}` | The gate goes to `fault` if this goes stale |

Overlays are drawn client-side from `entity.box` plus `entity_risk`. Whether the Pi should instead burn overlays into the stream is OQ-6.
