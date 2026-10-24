# kitchen/vision: Camera, detection and tracking

**Owner:** team `vision` (agents). Runs on the Pi with the Hailo-8, and everything here also runs on a laptop from a video file.

## Pipeline step

Turn camera frames into tracked, stably identified bins, tools, hands and plates (`ObservationFrame`), and hand every frame to the partner's entity store and to `kitchen/interactions`.

## What happens here

- Read frames from the Pi camera (`picamera2`) or a video file. Each frame is stamped with `t_ms` from the injected `Clock`.
- Detect knives, spoons, forks and bowls with pretrained YOLOv8, and wrists with YOLOv8 pose. This runs on the Hailo-8, with a CPU backend for laptops.
- Track with ByteTrack, then map each track to a stable entity ID (`bin-<slot>`, `tool-<class>-<n>`, `hand-p<k>-left|right`, `plate-<n>`) with an `origin`: `shift_start`, `clean_stack` or `unknown`.
- Give every detection a zone from the calibration file: bin slots, plating spot, wash, clean stack, pass.
- When the cook taps "start ticket", bind the plate at the plating spot to that ticket. Serve the Pi's HTTP API.
- Record frames to JSONL, and replay them byte for byte.

## Files

| File | What it does |
|---|---|
| `camera.py` | Frame sources: Pi camera, video file, JSONL replay. Stamps `t_ms` from the `Clock` |
| `detect.py` | `Detector` interface with a CPU backend (`ultralytics`) and a Hailo-8 backend, picked by `VISION_DETECTOR=cpu\|hailo` |
| `pose.py` | YOLOv8 pose wrists → hand detections with `person_id` and `wrist` |
| `track.py` | ByteTrack behind a `Tracker` interface |
| `identity.py` | Track → entity ID and `origin`. A track lost and re-acquired always becomes a new entity |
| `zones.py` | Zone file format, click-to-calibrate tool, point-in-polygon assignment, `StateClient.set_zones` at startup |
| `plates.py` | "Start ticket" binding: exactly one plate at the plating spot, or refuse |
| `record.py` | Records frames plus a session manifest; deterministic replay |
| `server.py` | Vision HTTP API (`/v1/plates/start`, `/stream.mjpg`, `/v1/health`), and the scripted `FakeVisionAPI` for outputs |
| `run.py` | Main loop: source → detect + pose → track → identity + zones → `ObservationFrame` → entity store, interactions, recorder. Also the replay CLI |

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| in | Camera frames or video files | none | Pi camera, `data/` |
| in | Shift start, existing plate bindings | [`StateClient`](../../contracts/apis.md#stateclient) | partner database |
| out | `ObservationFrame`, every frame, in-process | [`observation_frame`](../../contracts/schemas/observation_frame.schema.json) | partner [`EntityStore.ingest`](../../contracts/apis.md#entitystore), `kitchen/interactions` |
| out | Frame JSONL recordings and the fixture pack | `observation_frame` | interactions, sim, partner tests |
| out | Zones | `StateClient.set_zones` → `zone` table | everyone |
| out | Plate binding | `StateClient.bind_plate` → `plate_binding` table | verdict, partner graph, outputs |
| out | Vision HTTP API | [apis.md](../../contracts/apis.md#vision-http-api-on-the-pi) | outputs (start-ticket button, overlays, gate health watch) |

## Safety rules that apply

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`.

- **Invariant 8: clean only from a known-clean source.** A track lost beyond ByteTrack's buffer and re-acquired gets a **new** entity ID, with `origin: unknown` and `first_seen_t_ms` set to when it reappeared. Entity IDs are never reused within a shift (property test). A person re-acquired by pose gets new hand IDs. `origin: clean_stack` only when the entity is first seen leaving a `clean_stack` zone. No re-identification or track merging without an RFC and the safety reviewer.
- **Invariant 1: fail closed.** `/v1/plates/start` refuses when the spot has 0 plates or 2+ plates, or when the plate is already bound to another ticket. Detections are never interpolated or invented. `/v1/health.last_frame_t_ms` stops advancing when frames stop, so the gate can go to `fault`.
- **Invariant 5: time runs forward.** Frame `t_ms` comes from the injected `Clock` and is strictly increasing, including across source restarts. Replay keeps the recorded `t_ms`.

## Fakes

- **We use:** `InMemoryStateClient`, `FakeEntityStore` and `FixedClock` (platform, in `kitchen/shared/fakes/`).
- **We ship:** the recorded frame fixture pack (`kitchen/vision/tests/fixtures/pack/`), the JSONL replay source, a sample `zones.json`, and `FakeVisionAPI`.

## Tests

Tests live in `kitchen/vision/tests/`; run them with `uv run pytest kitchen/vision`. Hailo tests carry the `hardware` marker and skip in CI. Fixtures stay at 100 KB or less. Videos, HEFs and weights live in `data/`, with a `data-manifest.json` entry, never in git.

## Milestones

- **M0:** CPU replay → frame JSONL (vision-01). Hailo det+pose feasibility measured (vision-02, `hardware`). Zones and calibration (vision-03). Identity rules (vision-04). Fixture pack (vision-05).
- **M1:** feed the entity store every frame (vision-06), start-ticket binding (vision-07), health and stream (vision-08), recorder (vision-09), `FakeVisionAPI` (vision-10), Hailo backend behind `Detector` (vision-11, `hardware`).
- **M2:** Hailo live at ≥ 30 FPS, verified by the owner (`hardware`). `picamera2` source. Calibration on the real counter. A recorded live session replays deterministically. A stale health endpoint drives the gate to `fault`.
- **M3:** measure the ID-switch rate on owner recordings and reduce it. Fine-tune the detector only if COCO tools and bowls are too weak from overhead.
- **M4:** failure drills: camera unplugged, Hailo crash, network loss. Each ends in stale health and a gate `fault`, never a PASS. Lighting robustness. Offline operation.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Unverified whether the Hailo-8 can run det+pose together at 30 FPS, or whether a pose HEF exists | vision-02 first. Fallbacks in order: `n` models, lower resolution, pose every Nth frame (flagged), CPU |
| Hands occlude tools, so ByteTrack switches IDs and causes unknown-history holds | Safe, but it hurts the demo. Overhead mount, tune the track buffer, measure in M3 |
| A wrist is not the contact point | The hand box extends from the wrist toward the forearm. Calibrate on recordings |
| COCO `knife`, `spoon` and `fork` are weak from overhead | Camera angle first; fine-tune in M3 with weights outside git |
| A bin is swapped between slots mid-shift | Bins are identified by slot. Swaps are unsupported, and the README says so |
| Pi OS, Python and HailoRT compatibility (OQ-11) | vision-02 records the exact versions |
| Ultralytics is AGPL-3.0 (OQ-16) | Owner decides before any release |
| Overlays drawn client-side or burned in (OQ-6) | Client-side by default |

Open questions: [AGENTS.md §11](../../AGENTS.md#11-open-questions).
