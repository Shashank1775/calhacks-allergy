# kitchen/vision: Camera, detection and tracking

**Owner:** team `vision` (agents). **The Hailo-8 isn't connected yet.** Everything runs locally on a laptop (video file or webcam, ONNX Runtime), and is built so the same model and code can be uploaded to the Hailo later (see [Local now, Hailo later](#local-now-hailo-later)).

## Pipeline step

Turn camera frames into tracked, stably identified bins, tools, hands and plates (`ObservationFrame`), and hand every frame to the partner's entity store and to `kitchen/interactions`.

## What happens here

- Read frames from a video file or laptop webcam now, and from the Pi camera (`picamera2`) later. Each frame is stamped with `t_ms` from the injected `Clock`.
- Detect knives, spoons, forks and bowls with pretrained YOLOv8, and wrists with YOLOv8 pose. Today this runs locally with ONNX Runtime. Later the same model runs on the Hailo-8 as a HEF, sharing the same pre- and post-processing code.
- Track with ByteTrack, then map each track to a stable entity ID (`bin-<slot>`, `tool-<class>-<n>`, `hand-p<k>-left|right`, `plate-<n>`) with an `origin`: `shift_start`, `clean_stack` or `unknown`.
- Give every detection a zone from the calibration file: bin slots, plating spot, wash, clean stack, pass.
- When the cook taps "start ticket", bind the plate at the plating spot to that ticket. Serve the Pi's HTTP API.
- Compute a **change mask** for every frame with the shared function (`kitchen/shared/change_mask.py`): a single channel that's brighter where more moved since the last frame. Hand it over in-process with each `ObservationFrame`; interactions uses it to build clips for the partner's ConvLSTM.
- Record frames to JSONL and change masks to `change.npz`, and replay both byte for byte.

## Files

| File | What it does |
|---|---|
| `camera.py` | Frame sources: video file, laptop webcam, JSONL replay, and later the Pi camera. Stamps `t_ms` from the `Clock` |
| `detect.py` | `Detector` interface; picks a backend with `VISION_DETECTOR=onnx\|hailo` (default `onnx`). Backends only run the network; everything around it is in `postprocess.py` |
| `postprocess.py` | Shared by every backend: letterbox resize, output decoding, NMS, keypoint decoding, class mapping. Local results predict Hailo results because this code is identical |
| `export.py` | Builds the **Hailo deploy bundle** from the chosen YOLOv8 weights: fixed-shape ONNX, calibration frames from our kitchen footage, `manifest.json`, and the HEF fetch or compile step |
| `hailo.py` | Hailo-8 backend: loads the bundle's HEF with HailoRT on the Pi. Imported only when `VISION_DETECTOR=hailo`; tests use a mock runtime locally (`hardware` for the real device) |
| `pose.py` | YOLOv8 pose wrists → hand detections with `person_id` and `wrist` |
| `track.py` | ByteTrack behind a `Tracker` interface |
| `identity.py` | Track → entity ID and `origin`. A track lost and re-acquired always becomes a new entity |
| `zones.py` | Zone file format, click-to-calibrate tool, point-in-polygon assignment, `StateClient.set_zones` at startup |
| `plates.py` | "Start ticket" binding: exactly one plate at the plating spot, or refuse |
| `record.py` | Records frames (`frames.jsonl`), change masks (`change.npz`) and a session manifest; deterministic replay |
| `server.py` | Vision HTTP API (`/v1/plates/start`, `/stream.mjpg`, `/v1/health`), and the scripted `FakeVisionAPI` for outputs |
| `run.py` | Main loop: source → detect + pose → track → identity + zones → `ObservationFrame` → entity store, interactions, recorder. Also the replay CLI |

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| in | Camera frames or video files | none | Pi camera, `data/` |
| in | Shift start, existing plate bindings | [`StateClient`](../../contracts/apis.md#stateclient) | partner database |
| out | `ObservationFrame`, every frame, in-process | [`observation_frame`](../../contracts/schemas/observation_frame.schema.json) | partner [`EntityStore.ingest`](../../contracts/apis.md#entitystore), `kitchen/interactions` |
| out | Change mask per frame, in-process | [change mask](../../contracts/apis.md#change-mask-and-clips) | `kitchen/interactions` (clips) |
| out | Frame JSONL plus `change.npz` recordings and the fixture pack | `observation_frame`, change mask | interactions, sim (calibration), partner tests |
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

Tests live in `kitchen/vision/tests/`; run them with `uv run pytest kitchen/vision`. Real-device Hailo tests carry the `hardware` marker and skip in CI. The Hailo backend's logic is tested locally against a mock runtime. Fixtures stay at 100 KB or less. Videos, ONNX files, HEFs and weights live in `data/`, with a `data-manifest.json` entry, never in git.

## Local now, Hailo later

The Hailo-8 isn't connected yet, so nothing in vision waits on it. Being ready to upload means:

1. **One model artifact.** Pick YOLOv8 detection and pose variants and an input size that the Hailo Model Zoo supports (vision-02 confirms which). Export them once to fixed-shape ONNX (`export.py`). Run that exact ONNX locally with ONNX Runtime.
2. **One processing path.** Letterbox, decoding, NMS and keypoints live in `postprocess.py`, used by both backends. The Hailo backend only replaces "run the network".
3. **A deploy bundle** in `data/models/<name>/`:
   - `model.onnx`;
   - `calib/`: a few hundred frames from our own kitchen recordings, for quantization;
   - `manifest.json`: classes, input size, normalization, thresholds, weight hashes, tool versions;
   - `model.hef`, once produced.
4. **Getting a HEF**, either way:
   - (a) use the precompiled Hailo Model Zoo HEF for the same variant, if one exists and COCO classes are enough;
   - (b) compile our ONNX with the Hailo Dataflow Compiler, which needs an x86-64 Linux machine, not the Mac (OQ-18).
5. **Parity check.** When the device is connected, the owner runs one recorded clip through ONNX (laptop) and HEF (Pi) and compares detections within a tolerance. Quantization drift shows up here, not in the demo.
6. **Upload.** Copy the bundle to the Pi, set `VISION_DETECTOR=hailo`, and check `/v1/health` reports `detector: hailo` and ≥ 30 FPS.

The 30 FPS budget applies on the Hailo. Locally, replay runs at whatever speed the laptop manages, and timing comes from recorded `t_ms`, so results don't depend on speed.

## Milestones

- **M0:** local ONNX replay → frame JSONL (vision-01). Change masks per frame and in recordings (vision-13). Hailo readiness spike, desk research with no device (vision-02). Zones and calibration (vision-03). Identity rules (vision-04). Fixture pack (vision-05).
- **M1:** feed the entity store every frame (vision-06), start-ticket binding (vision-07), health and stream (vision-08), recorder (vision-09), `FakeVisionAPI` (vision-10), Hailo backend against a mock runtime (vision-11), Hailo deploy bundle and parity script (vision-12). Nothing needs the device.
- **M2:** connect the Hailo and upload the bundle. Owner runs the parity check and confirms ≥ 30 FPS (`hardware`). `picamera2` source. Calibration on the real counter. A recorded live session replays deterministically. A stale health endpoint drives the gate to `fault`.
- **M3:** measure the ID-switch rate on owner recordings and reduce it. Fine-tune the detector only if COCO tools and bowls are too weak from overhead.
- **M4:** failure drills: camera unplugged, Hailo crash, network loss. Each ends in stale health and a gate `fault`, never a PASS. Lighting robustness. Offline operation.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Hailo not connected yet; unverified whether det+pose run together at 30 FPS, or whether Model Zoo HEFs exist for our variants | Local-first path never waits on it. vision-02 picks Model Zoo–supported variants. Fallbacks once connected: `n` models, lower resolution, pose every Nth frame (flagged) |
| Quantization changes detections on the Hailo | Calibration frames from our own footage; owner runs the ONNX-vs-HEF parity check before relying on the device |
| No x86-64 Linux machine for the Dataflow Compiler (OQ-18) | Prefer precompiled Model Zoo HEFs; compile only if we fine-tune |
| Hands occlude tools, so ByteTrack switches IDs and causes unknown-history holds | Safe, but it hurts the demo. Overhead mount, tune the track buffer, measure in M3 |
| A wrist is not the contact point | The hand box extends from the wrist toward the forearm. Calibrate on recordings |
| COCO `knife`, `spoon` and `fork` are weak from overhead | Camera angle first; fine-tune in M3 with weights outside git |
| A bin is swapped between slots mid-shift | Bins are identified by slot. Swaps are unsupported, and the README says so |
| Pi OS, Python and HailoRT compatibility (OQ-11) | vision-02 records the versions to install; the owner confirms when the device is connected |
| Ultralytics is AGPL-3.0 (OQ-16) | Owner decides before any release |
| Overlays drawn client-side or burned in (OQ-6) | Client-side by default |

Open questions: [AGENTS.md §11](../../AGENTS.md#11-open-questions).
