# Team charter: `perception`

> Owned by the perception Manager. Changes go through the Director. Task-level detail lives in issues labeled `team:perception`.

## Mission

Turn the camera feed into a stream of tracked, stably identified bins, tools, hands and plates, each with a zone and a box, and keep SpacetimeDB's `entity` table current at about 5 Hz. We never let an entity silently inherit history that belongs to a different physical object.

## Owns

- Paths: `components/perception/` (see [OWNERS](../../OWNERS)).
- Pipeline stage 3 in [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#3-perception-on-the-pi--team-perception): detection, pose, tracking, entity identity, the in-memory entity store and the ≈5 Hz sync.
- Writer of the SpacetimeDB tables `entity`, `zone` and `plate_binding` ([state-tables.md](../../contracts/state-tables.md#writers)).
- Zone and slot calibration: bin slots, plating spot, wash station, pass.
- The [Perception HTTP API](../../contracts/apis.md#perception-http-api-on-the-pi): `POST /v1/plates/start`, `GET /stream.mjpg`, `GET /v1/health`.
- Recording frames to JSONL and replaying them deterministically.
- Two detector backends behind one interface: Hailo-8 on the Pi, and CPU on a laptop.

## Does not own

- Classifying interactions or deciding what counts as contact: **interactions**.
- Bin allergen profiles (`bin_profile`): **intake**. Live propagated risk (`entity_risk`): **provenance**.
- The "start ticket" button, overlay drawing and the expo board: **experience** (overlay location is OQ-6).
- The pass-gate driver, even though it runs on the same Pi: **experience**.
- The SpacetimeDB module and the real `StateClient`: **state**.

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| [`observation_frame`](../../contracts/schemas/observation_frame.schema.json), in-process on the Pi and as JSONL | interactions, sim (realism checks) | Recorded frame JSONL fixture pack, plus a replay source that reads it |
| [`entity`](../../contracts/schemas/entity.schema.json) rows via `StateClient.upsert_entities` | everyone | Entity rows inside the frame fixture pack; `InMemoryStateClient` comes from platform |
| `zone` rows via `set_zones` | interactions (zone kind), experience | Sample zone calibration file |
| `plate_binding` via `StateClient.bind_plate` | provenance, experience | — |
| Perception HTTP API | experience | `FakePerceptionAPI`: scripted `/v1/plates/start`, a fake `/v1/health`, and a looping test-pattern `/stream.mjpg` |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| `StateClient` | state | `InMemoryStateClient` (platform) |
| `Clock` and generated models (`py_common`) | platform | `FakeClock` |
| Shift start (`start_shift`) | state | `InMemoryStateClient` |

## Invariants we guard

| Invariant | How our code can break it | Guard (pytest, marked `safety`) |
|---|---|---|
| 8, clean at shift start only | Reusing an entity ID after ByteTrack loses and re-acquires a track, so a contaminated tool looks like a known clean one (or the reverse) | A re-acquired track always gets a **new** `entity_id` with `first_seen_t_ms` set to when it reappeared. IDs are never reused within a shift (property test). A person re-acquired by pose gets new hand IDs. No silent merging, ever |
| 1, fail closed | Fabricating detections, guessing a plate binding, or reporting healthy while frames are stale | `/v1/plates/start` refuses with 0 or 2+ plates at the spot. `/v1/health.last_frame_t_ms` stops advancing when frames stop, so the gate can go to `fault`. No interpolated detections are ever emitted |
| 5, time runs forward | Non-monotonic `t_ms` across frames or after a source restart | Frame `t_ms` comes from the injected `Clock` and is strictly increasing (test). Replay preserves recorded `t_ms` |

## Stack and layout

- Python, pinned to the Pi's system Python ([CONVENTIONS.md](../../docs/agents/CONVENTIONS.md)).
- Capture uses `picamera2` on the Pi, or a video file anywhere.
- Detection and pose: pretrained YOLOv8 (COCO classes `knife`, `spoon`, `fork`, `bowl`, plus pose wrists, keypoints 9 and 10). The Hailo backend uses HailoRT/Hailo apps with compiled HEFs. The CPU backend uses `ultralytics`.
- ByteTrack: the `supervision` or `ultralytics` implementation, behind our own `Tracker` interface.
- **Unverified until perception-02 reports back:** whether HEFs exist for YOLOv8 detection *and* pose on the Hailo-8, and what FPS both models reach together on one Hailo-8 with the Pi camera. Fallbacks, in order: smaller models (`n` variants), lower input resolution, pose every Nth frame with wrist interpolation flagged as such, then CPU.
- Licensing: `ultralytics` is AGPL-3.0. This is fine for a private prototype, but the owner must decide before the repo goes public (raise it as `status:needs-human` when perception-01 lands).
- Layout under `src/perception/`: `sources/` (video file, picamera2, JSONL replay), `detect/` (`Detector` interface, `cpu_ultralytics`, `hailo`), `track/`, `identity/` (entity IDs, slots, plates), `zones/`, `store/` (in-memory entity store), `sync/` (5 Hz `StateClient` writer), `api/` (HTTP), `record/`.
- Test command: `uv run --package perception pytest components/perception`.

## Deliverables by milestone

| Milestone | We deliver |
|---|---|
| M0 | Replay a video on a laptop CPU into valid `ObservationFrame` JSONL (perception-01). Hailo det+pose feasibility answered with measured FPS (spike, `hardware`). Entity identity rules with safety tests. Zone calibration format and tool, plus an RFC adding the missing `zone` schema and `StateClient.set_zones`. Recorded frame fixture pack used by interactions |
| M1 | In-memory entity store and 5 Hz sync through `StateClient` (fake, then real). `POST /v1/plates/start` that never guesses. `/v1/health` and `/stream.mjpg`. Recorder with byte-identical replay. `FakePerceptionAPI` for experience |
| M2 | Hailo backend live at ≥ 30 FPS, verified by the owner (`hardware`). `picamera2` source. Calibration on the real counter. A recorded live session that replays deterministically. A stale health endpoint drives the gate to `fault` |
| M3 | ID-switch rate measured on owner recordings and reduced (camera placement, track buffer tuning). Detector fine-tune or class remap only if COCO tools and bowls prove too weak overhead |
| M4 | Failure drills: camera unplugged, Hailo crash, network loss. Each ends in a stale health endpoint and a gate `fault`, never a PASS. Lighting robustness pass. Offline operation |

## Working with other teams

- **interactions:** frame JSONL shape, zone kinds, how hands are represented, and replay fixtures. They start on hand-written frames and switch to our pack.
- **state:** `upsert_entities` batch size and rate, and the `zone` and `plate_binding` reducers. Real sync waits on state-03.
- **experience:** the start-ticket button calls our API. Overlays read `entity.box` (OQ-6), and the gate watches our health.
- **provenance:** consumes our identity semantics, including `entity.origin` (`shift_start`, `clean_stack` or `unknown`, per invariant 8). Agree with them on edge cases at shift start.
- **Owner:** all `hardware` verification, camera mounting, and recording real sessions (OQ-11, OQ-12).

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Hailo-8 cannot run det+pose together at 30 FPS, or no pose HEF exists | perception-02 spike first. Fallbacks listed above. Treat the budget in ARCHITECTURE as a target until measured |
| ByteTrack loses tools when hands occlude them, so new IDs mean unknown-history holds (false holds) | Acceptable under fail-closed, but it hurts the demo. Use an overhead mount, tune the track buffer, and measure the switch rate in M3. Any re-identification scheme needs an RFC and the safety reviewer; never a silent merge |
| Hand identity depends on pose person tracking; a person re-acquired means new hand IDs | Same policy as tools. Track hands as `hand-p<k>-left/right` tied to the pose track |
| A wrist keypoint is not the contact point | The hand box extends from the wrist toward the forearm direction. Calibrate on recordings |
| COCO `knife`/`spoon`/`fork` detection is weak from overhead | Camera angle first. Fine-tune in M3 with a `data-manifest.json`, no weights in git |
| A bin is swapped between slots mid-shift | Bins are identified by slot. Swaps are out of scope for the demo; raise them with the owner if they matter |
| Python access to SpacetimeDB is undecided (OQ-1) | Code only against `StateClient`. Sync is buffered and retried |
| Pi OS and Python version vs Hailo package compatibility (OQ-11) | perception-02 records exact OS, HailoRT and Python versions |

## Definition of done (team additions)

- Every emitted frame validates against `observation_frame`. Contract tests run on the fixture pack.
- Any change to identity, binding or health logic carries `safety-critical` and has the safety reviewer's sign-off.
- PRs state hardware status. Nothing is called "≥ 30 FPS" without an owner-posted measurement.
- No video, HEF or weights in git. Artifacts are listed in `data-manifest.json`.

## Staffing

- M0: 2 Workers (CPU replay plus identity; Hailo spike plus calibration).
- M1: 3 Workers (store/sync, HTTP API plus fake API, recorder).
- M2: 2 Workers plus owner time on the device.
- Identity and binding logic suits one careful Worker. Endpoints, recorder and the fake API parallelize well.
