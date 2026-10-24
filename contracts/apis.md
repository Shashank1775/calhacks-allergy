# APIs and interfaces

Signatures here are the contract. Types named after a schema (`Ticket`, `Entity`, ...) are plain dicts that validate against that schema in [`schemas/`](schemas/) (check one with `kitchen.shared.models.validate`). Every interface has a **fake**, named `Fake<Thing>`, so any part can be tested without the real thing.

| Interface | Real implementation | Fake | Fake owner |
|---|---|---|---|
| [`StateClient`](#stateclient) | **partner** (`database/` + client) | `FakeStateClient` | platform |
| [`EntityStore`](#entitystore) | **partner** (`kitchen/entities/`) | `FakeEntityStore` | platform |
| [`ProvenanceGraph`](#provenancegraph) | **partner** (`kitchen/graph/`) | `FakeProvenanceGraph` | platform |
| [`InteractionClassifier`](#interactionclassifier) | **partner** (zone rules in `kitchen/interactions/`, ConvLSTM in `kitchen/lstm/`) | `FakeClassifier` | platform |
| [Change mask and clip builder](#change-mask-and-clips) | platform (`kitchen/shared/change_mask.py`, `kitchen/shared/clips.py`) | none needed: pure functions | platform |
| [`LayaParser`](#layaparser) | orders | `FakeLayaParser` | orders |
| [`LayaPlateCheck`](#layaplatecheck) | verdict | `FakeLayaCheck` | verdict |
| Verdict feed (`verdict` rows) | verdict | `FakeVerdictFeed` (replays scenario verdicts into `FakeStateClient`) | verdict |
| [`Transcriber`](#transcriber-and-speaker) | orders | `FakeTranscriber` | orders |
| [`Speaker`](#transcriber-and-speaker) | outputs | `FakeSpeaker` | outputs |
| [`GateDriver`](#gatedriver) | outputs | `FakeGate` | outputs |
| [`Clock`](#clock) | platform | `FakeClock` | platform |
| [Vision HTTP API](#vision-http-api-on-the-pi) | vision | `FakeVisionAPI` | vision |

Fakes for partner-owned parts are written by the platform team against this file. The partner's real code must pass the same contract tests the fakes pass (`kitchen/shared/contract_tests/`).

All Protocols live in `kitchen/shared/interfaces.py` (owner: platform).

## StateClient

Real: **partner**. Fake: `FakeStateClient` (platform). Every Python component talks to SpacetimeDB only through this.

```python
class StateClient(Protocol):
    # writes: each method has exactly one calling team (state-tables.md#writers); names match the module's reducers
    def put_ticket(self, ticket: Ticket) -> None: ...                                   # orders; an amendment replaces it with revision + 1
    def put_bin_profile(self, entity_id: str, profile: AllergenProfile) -> None: ...    # orders
    def set_zones(self, zones: list[Zone]) -> None: ...                                 # vision (calibration)
    def bind_plate(self, plate_id: str, ticket_id: str, t_ms: int) -> None: ...         # vision ("start plate")
    def register_entity(self, entity: Entity) -> None: ...                              # partner entity store, once per entity
    def move_entity(self, state: EntityState) -> None: ...                              # partner entity store and graph, ~5 Hz
    def record_interaction(self, event: InteractionEvent) -> None: ...                  # partner interactions
    def record_verdict(self, verdict: Verdict) -> None: ...                             # verdict

    # reads
    def get_ticket(self, ticket_id: str) -> Ticket | None: ...                          # the latest revision
    def get_entity(self, entity_id: str) -> Entity | None: ...                          # identity + latest state
    def list_entities(self, kind: str | None = None) -> list[Entity]: ...
    def list_zones(self) -> list[Zone]: ...
    def get_plate_binding(self, plate_id: str) -> PlateBinding | None: ...
    def list_interactions(self, since_t_ms: int = 0, until_t_ms: int | None = None) -> list[InteractionEvent]: ...
    def list_verdicts(self, ticket_id: str | None = None) -> list[Verdict]: ...

    # change feed
    def subscribe(self, table: str, on_change: Callable[[ChangeEvent], None]) -> Callable[[], None]: ...
```

- `Zone`, `PlateBinding`, `EntityState` and `ChangeEvent` are small dataclasses in `kitchen/shared/models.py` mirroring [state-tables.md](state-tables.md).
- **Triggers are table changes.** "A plate reaches the pass" means a new `entity_state` row for a plate whose `zone` is a zone of kind `pass`; the verdict service watches `subscribe("entity_state", ...)` for that transition. "A ticket was amended" means a new `ticket` revision (`subscribe("ticket", ...)`). "An edge landed late" means a new `interaction` row that a plate's latest verdict didn't account for (`subscribe("interaction", ...)`): one whose `t_start_ms` is at or before that verdict, or one touching the plate while it waits at the pass. The plate is judged again from its full history, with trigger `late_edge` (safety rule: late changes recheck everything). Interaction edges are written after they're classified, so the last contact before the pass can land after the plate was judged. The gate service watches `subscribe("verdict", ...)`.
- Every method raises `StateUnavailable` when the database can't be reached. On the verdict path that becomes HOLD (safety rule: fail closed).
- Writes are idempotent by primary key.
- **Fake:** dict-backed, same semantics including `subscribe` and append-only entity history, and can load a `scenario` file.
- How Python reaches SpacetimeDB is the partner's call (default: HTTP reducer calls plus polling).

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

- Never deletes an entity and never changes `first_seen_t_ms` or `origin` (safety rule: known-clean source).
- **Fake:** applies frames to a dict; `start_sync` writes `register_entity` and `move_entity` calls to whatever `StateClient` it gets.

## ProvenanceGraph

Real: **partner** (`kitchen/graph/`). Fake: `FakeProvenanceGraph` (platform). The graph builds itself from `StateClient` (entities, bin profiles, interactions); agents only query it.

```python
class ProvenanceGraph(Protocol):
    def reach(self, plate_id: str, as_of_t_ms: int) -> list[AllergenReach]: ...
        # exact reachability: every allergen unit with nonzero probability at the plate, each with its chain
    def unknown_ancestors(self, plate_id: str, as_of_t_ms: int) -> list[str]: ...
        # entity IDs in the plate's ancestry with origin "unknown" and no wash since
    def subgraph(self, plate_id: str, as_of_t_ms: int) -> PlateSubgraph: ...
        # the plate's time-forward ancestry: entities, what each carried at the start, and the edges, in time order;
        # what Laya's final plate check reads (schemas/plate_subgraph.schema.json)
    def version(self) -> str: ...
```

- Raises `GraphUnavailable` on any failure. The verdict policy turns that into HOLD (safety rule: fail closed).
- Must follow the time-runs-forward and known-clean-source safety rules, and pass every golden scenario in [`scenarios/`](scenarios/).
- **Fake:** answers from a scenario file's ground truth (`expected[].allergens_reached`, `chain_event_ids`). It does no graph math, so it never duplicates the partner's work. Its `subgraph()` returns the scenario's nodes and its events up to `as_of_t_ms`; extra off-path edges are allowed.

## InteractionClassifier

Real: **partner**: zone rules (always available) in `kitchen/interactions/`, and a ConvLSTM in `kitchen/lstm/`, adopted only if it beats zone rules on held-out clips (safety rule: rules stay as the fallback). Fake: `FakeClassifier` (platform): returns scripted labels, or errors or hangs on cue.

```python
@dataclass(frozen=True)
class PairWindow:
    a_id: str
    b_id: str
    features: Sequence[InteractionFeatures]   # vector features per step: what zone rules use
    clip: InteractionClip                     # metadata (schema interaction_clip) ...
    tensor: np.ndarray                        # ... and its [T, C, H, W] tensor: what the ConvLSTM uses

class InteractionClassifier(Protocol):
    name: str      # "zone_rules" | "lstm"
    version: str
    def classify(self, window: PairWindow) -> list[tuple[Action, float]]: ...
```

- Both inputs are allergen-blind by construction. The clip is also **object-blind**: box masks and motion only, no object pixels, classes or kinds (safety rule: blind classifier).
- The ConvLSTM's two judgments map onto actions: P(contact) → `transfer.contact`, P(material moved) → `transfer.scoop`. A contact with no visible movement is still a possible trace transfer; the graph and verdict decide how much it counts.
- `kitchen/interactions/classifier.py` wraps both: it uses the LSTM only if adopted and healthy, and falls back to zone rules otherwise.

## Change mask and clips

Owner: **platform** (`kitchen/shared/`). Used by **vision** (real frames, live), **sim** (rendered frames) and the **partner** (live pair windows in `kitchen/interactions/`, and training). One implementation, so the model sees the same thing in training and live.

### Change mask

Per frame, a single channel that is bright where pixels changed since the previous frame: a gradient where brighter means more movement.

```python
def letterbox_frame(rgb: np.ndarray, size: int) -> np.ndarray: ...          # in kitchen/shared/clips.py
    # shrink a camera frame into a size x size square by area averaging; pad the short side with gray 114

def change_mask(prev_rgb: np.ndarray, rgb: np.ndarray, prev_mask: np.ndarray | None, *,
                gain: float, blur_sigma_px: float, decay: float) -> np.ndarray: ...
    # both frames letterboxed to the clip size first (letterbox_frame)
    # d = max over R,G,B of |rgb - prev_rgb| / 255
    # m = clip(gain * gaussian_blur(d, blur_sigma_px), 0, 1)
    # if decay > 0: m = max(m, decay * prev_mask)   # optional motion trail
```

- **Always compute it at clip size** (128 × 128 after `letterbox_frame`), never at camera resolution and then shrink: gain and blur are tuned for clip-size frames, and full-resolution noise survives a shrink. The first frame of a stream has no previous frame, so its mask is all zeros.
- Vision computes it for every real frame and hands it over in-process alongside each `ObservationFrame`. Recordings store it next to the frame JSONL as `change.npz` (uint8, 0 to 255): **divide by 255** before giving it to the clip builder, which rejects integer masks.
- Sim renders abstract RGB frames (textured background, food blobs that move and smear, plain shapes for tools: never realistic spoons or forks), then runs the **same function**, so its masks have the same character as real ones.
- Parameters (`gain`, `blur_sigma_px`, `decay`) are recorded in every clip's metadata.

### Clip builder

```python
def build_clip(a_id: str, b_id: str, steps: Sequence[ClipStep], *, clip_id: str, source: str, tensor_file: str,
               change_mask_params: dict, H: int = 128, W: int = 128, view: str = "full",
               channels: Sequence[str] = ("box_a", "box_b", "change"), generator: dict | None = None,
               labels: dict | None = None, source_size: tuple[int, int] | None = None) -> tuple[InteractionClip, np.ndarray]: ...
    # ClipStep = (t_ms, box_a, box_b, change_mask, other_boxes); a missed box is None and draws as all zeros
    # returns the interaction_clip record and a uint8 [T, C, H, W] tensor (0 to 255)
```

- `box_a` and `box_b` channels are 1 inside each entity's box (normalized boxes drawn at H x W). `change` is the change mask. `dt_ms` carries elapsed time per step.
- **Live, pass `source_size`** = the camera frame's (width, height). Boxes are normalized to the camera frame, so the builder moves them through the same letterbox as the frames; without it, boxes from a 16:9 frame land on the wrong rows and miss their own change. Sim renders square frames and leaves it `None`.
- Only `view="full"` is built; `"pair"` raises until someone needs it.
- No object class, kind or appearance goes in.

## LayaParser

Owner: **orders**. Backed by our fine-tuned Laya model, which answers typed questions with calibrated probabilities. The adapter asks it whether a note forbids each allergen, how severe, whether traces matter and whether an ingredient is still allowed, and turns the answers into a `ParseResult`. Bins don't go through Laya: their profiles are hard-coded for the demo (`kitchen/orders/bins.py`).

```python
class LayaParser(Protocol):
    def parse_note(self, note: str, *, source: Literal["typed", "voice"]) -> ParseResult: ...
```

- Raises `LayaParserUnavailable` (an empty note too). The Orders API then answers `503 laya_unavailable`. It never guesses.
- **Fake:** `FakeLayaParser`, a deterministic keyword parser covering the notes in the orders eval set.

## LayaPlateCheck

Owner: **verdict** (`kitchen/verdict/laya_check.py`). A second fine-tuned Laya model, separate from the ticket-note one, that makes the final plate check. It reads the plate's subgraph from the partner's graph and says, for each forbidden allergen, how likely it reached the plate.

```python
class LayaPlateCheck(Protocol):
    version: str   # the checkpoint's name, recorded as verdict.versions.laya
    def check(self, subgraph: PlateSubgraph, forbidden: list[str]) -> dict[str, float]: ...
        # P(reached the plate) for each forbidden allergen unit
```

- **Stricter only.** The verdict adds a `laya_flagged` hold reason for each forbidden allergen with P >= 1 − `pass_threshold`, the same flag rule it applies to the graph's numbers. Laya can never remove a hold reason or cause a PASS.
- It reads only the edges on a time-forward path to the plate (anything else is dropped first). Raises `LayaCheckUnavailable` on any failure, including more than 10 such edges (longer than anything it was trained on) or an edge whose entity has no node. The verdict then holds with `laya_unavailable` (safety rule: fail closed).
- **Fake:** `FakeLayaCheck`, which answers with the exact noisy-OR result over the subgraph (a perfect Laya).

## Transcriber and Speaker

Owners: **orders** (Transcriber, Cartesia Ink), **outputs** (Speaker, Cartesia Sonic).

```python
class Transcriber(Protocol):
    def transcribe(self, audio: bytes, *, sample_rate_hz: int) -> str: ...

class Speaker(Protocol):
    def say(self, text: str) -> None: ...   # non-blocking; drops stale lines rather than queueing them
```

- **Fakes:** `FakeTranscriber` returns canned transcripts; `FakeSpeaker` prints the line.

## GateDriver

Owner: **outputs**.

```python
class GateDriver(Protocol):
    def show(self, state: Literal["idle", "pass", "hold", "fault"], ticket_number: int | None = None) -> None: ...
```

- `fault`: arm closed, red light, buzzer pulsing. Used when the gate loses its verdict feed (safety rule: fail closed).
- **Fake:** `FakeGate` prints each state and records every call for tests.

## Clock

Owner: **platform**. Logic never reads the wall clock (replays must be deterministic).

```python
class Clock(Protocol):
    def now_ms(self) -> int: ...     # t_ms since shift start
```

- **Fake:** `FakeClock`, advanced by hand in tests. The live clock is `SystemClock`.

## HTTP conventions

Applies to the orders and vision HTTP APIs.

- JSON bodies. Errors are `{"error": {"code": str, "message": str}}`.
- Browser screens from `outputs` call these APIs directly, so each service sends CORS headers for the origins in its `<PREFIX>_CORS_ORIGINS` setting (default: the web dev server origin). There are no wildcard origins.
- Every service exposes `GET /health`.

## Orders HTTP API

Owner: **orders** (`kitchen/orders/api.py`). Consumer: **outputs** (tablet screen).

| Method and path | Body | Returns | Notes |
|---|---|---|---|
| `POST /transcribe` | audio (`audio/wav`) | `{"text": str}` | Cartesia Ink |
| `POST /parse` | `{"note": str, "source": "typed"\|"voice"}` | `ParseResult` | |
| `POST /drafts/{draft_id}/answers` | `{"question_id": str, "value": any}` | `ParseResult` | `sendable` flips once all questions are answered |
| `POST /drafts/{draft_id}/send` | `{"number": int}` | `Ticket` | `409 unanswered_questions` if not sendable (safety rule: no unanswered questions). `ticket_id` is `t<number>`; `409 number_in_use` if that number was already sent this shift. An amendment draft keeps its ticket's number, so `number` can be left out |
| `POST /tickets/{ticket_id}/amend` | `{"note": str}` | `ParseResult` | New draft for revision + 1: the original note plus the change, parsed again; same question flow; send triggers a recheck (safety rule: late changes recheck everything). `409 ticket_changed` on send if another amendment landed first |

## Vision HTTP API (on the Pi)

Owner: **vision** (`kitchen/vision/server.py`). Consumer: **outputs** ("start plate" button, camera overlays, gate health watch, zone editor).

| Method and path | Body | Returns | Notes |
|---|---|---|---|
| `POST /plates/start` | `{"ticket_id": str}` | `{"plate_id": str}` | Binds the plate at the plating spot. Otherwise `409 no_plate_at_spot`, `409 multiple_plates_at_spot` or `409 plate_already_bound`; never guesses |
| `GET /stream.mjpg` | — | MJPEG | Raw camera stream |
| `GET /health` | — | `{"fps": float, "detector": "onnx"\|"hailo", "last_frame_t_ms": int}` | `detector` is `onnx` until the Hailo is connected. The gate goes to `fault` if this goes stale |
| `GET /zones` | — | `{"zones": [{"zone_id": str, "kind": str, "polygon": [[x, y], ...]}, ...]}` | The zones in use now, in the zones file format (`kitchen/vision/zones.py`): `kind` as in the `zone` table, points normalized 0..1 like boxes, bin slots named `slot-<n>` |
| `PUT /zones` | `{"zones": [...]}`, as `GET /zones` returns | The zones now in use, as `GET /zones` | Replaces every zone. A body that isn't a valid zones file is `422 bad_zones` (the message says which zone and why), and nothing changes. Otherwise it saves the zones file (`500 internal_error` if it can't, and nothing changes), uses the new zones from the next frame, and replaces the `zone` table (`503 state_unavailable` if the database can't be written, and the old zones stay). Entities keep their IDs and origins: a zone change never renames a bin or makes anything clean |

Overlays are drawn client-side from each entity's latest `entity_state` (box and allergens).
