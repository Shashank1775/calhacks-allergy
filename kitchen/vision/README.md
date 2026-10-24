# kitchen/vision: Camera, detection and tracking

**Owner:** team `vision` (agents).

Turns camera frames into tracked, stably identified bins, tools, hands and plates (`ObservationFrame`), and hands every frame, with its change mask, to the partner's entity store and interactions step. **The Hailo-8 isn't connected yet**, so everything runs locally on a laptop (video file or webcam, ONNX Runtime) and stays ready to upload.

## What happens here

- Read frames from a video file or webcam now, and from the Pi camera later. Each frame gets `t_ms` from the injected `Clock`.
- Detect knives, spoons, forks and bowls with YOLOv8s, and wrists with YOLOv8s-pose. Today this runs with ONNX Runtime; later the same model runs on the Hailo-8, sharing the same pre- and post-processing code.
- Track with ByteTrack, then map each track to a stable entity ID (`bin-<slot>`, `tool-<class>-<n>`, `hand-p<k>-left|right`, `plate-<n>`) with an `origin`: `shift_start`, `clean_stack` or `unknown`.
- Give every detection a zone from the zones file: bin slots, plating spot, wash, clean stack, pass.
- When the cook taps "start plate", bind the plate at the plating spot to that ticket.
- Compute a **change mask** for every frame with the shared function (`kitchen/shared/change_mask.py`) and hand it over with each `ObservationFrame`; the partner's interactions step builds ConvLSTM clips from it.
- Record frames to JSONL and change masks to `change.npz`, and replay both byte for byte.

## Files

| File | What it does |
|---|---|
| `camera.py` | Frame sources: video file, webcam, JSONL replay, and later the Pi camera |
| `detect.py` | YOLOv8s detection and pose with ONNX Runtime, plus the pre- and post-processing every backend shares. `VISION_DETECTOR=onnx\|hailo` picks the backend (default `onnx`) |
| `hailo.py` | Builds the Hailo deploy bundle, runs the HEF with HailoRT on the Pi, and checks ONNX-vs-HEF parity. Imported only when `VISION_DETECTOR=hailo` |
| `tracking.py` | ByteTrack, then track → entity ID and `origin`. A track lost and re-acquired always becomes a new entity |
| `zones.py` | Zones file format, click-to-calibrate tool, zone assignment, `StateClient.set_zones`, and "start plate" binding |
| `record.py` | Records `frames.jsonl`, `change.npz` and a manifest; deterministic replay |
| `server.py` | Vision HTTP API (`/plates/start`, `/stream.mjpg`, `/health`) and `FakeVisionAPI` for outputs |
| `run.py` | Main loop: source → detect → track → zones → `ObservationFrame` + change mask → consumers. Also the replay CLI |

## In and out

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Camera frames or video files | none | Pi camera, `data/` |
| Out | `ObservationFrame` and change mask, every frame, in-process | [`observation_frame`](../../contracts/schemas/observation_frame.schema.json), [change mask](../../contracts/apis.md#change-mask-and-clips) | partner [`EntityStore.ingest`](../../contracts/apis.md#entitystore), partner `kitchen/interactions` |
| Out | Recordings and the fixture pack | `observation_frame`, change mask | partner, sim |
| Out | Zones | `StateClient.set_zones` → `zone` table | everyone |
| Out | Plate binding | `StateClient.bind_plate` → `plate_binding` table | verdict, partner graph, outputs |
| Out | Vision HTTP API | [apis.md](../../contracts/apis.md#vision-http-api-on-the-pi) | outputs (start-plate button, overlays, gate health watch) |

## Safety rules

See [Safety rules](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`.

- **Known-clean source.** A track lost beyond ByteTrack's buffer and re-acquired gets a **new** entity ID, with `origin: unknown` and `first_seen_t_ms` set to when it reappeared. Entity IDs are never reused within a shift (property test). A person re-acquired by pose gets new hand IDs. `origin: clean_stack` only when the entity is first seen leaving a `clean_stack` zone. No re-identification or track merging.
- **Fail closed.** `/plates/start` refuses when the spot has 0 plates or 2+ plates, or when the plate is already bound to another ticket. Detections are never interpolated or invented. `/health.last_frame_t_ms` stops advancing when frames stop, so the gate can go to `fault`.
- **Time runs forward.** Frame `t_ms` comes from the injected `Clock` and is strictly increasing, including across source restarts. Replay keeps the recorded `t_ms`.

## Tests

- Tests live in `kitchen/vision/tests/`. Run `uv run pytest kitchen/vision`.
- They use `FakeStateClient`, `FakeEntityStore` and `FakeClock` (platform). The Hailo backend is tested against a mock runtime; real-device tests carry the `hardware` marker.
- We ship the recorded frame fixture pack (`kitchen/vision/tests/fixtures/pack/`, 100 KB or less), the JSONL replay source, a sample `zones.json`, and `FakeVisionAPI`.
- Videos, ONNX files, HEFs and weights live in `data/` with a `data-manifest.json` entry, never in git.

## Local now, Hailo later

Details and versions are in the [Hailo models decision](../../docs/decisions/hailo-models.md).

1. **One model artifact.** `yolov8s.pt` and `yolov8s-pose.pt` at a fixed 640×640 input, exported once to fixed-shape ONNX and run locally with ONNX Runtime.
2. **One processing path** in `detect.py`, shared by both backends. The Hailo backend only replaces "run the network" (the detection HEF also runs NMS, with the same thresholds).
3. **A deploy bundle** in `data/models/<name>/`: `model.onnx`, `calib/` (a few hundred frames from our kitchen recordings), `manifest.json` (classes, input size, thresholds, hashes, versions) and `model.hef`.
4. **Precompiled HEFs** from Hailo Model Zoo v2.18.0, which pairs with HailoRT 4.23.0 on Pi OS Trixie.
5. **Parity check.** When the device is connected, the owner runs one recorded clip through ONNX (laptop) and HEF (Pi) and compares detections.
6. **Upload.** Copy the bundle to the Pi, set `VISION_DETECTOR=hailo`, and check `/health` reports `detector: hailo` and at least 30 FPS.

## Watch out for

- Hands occlude tools, so ByteTrack switches IDs and causes unknown-history holds. Safe, but it hurts the demo: mount the camera overhead and tune the track buffer.
- A wrist is not the contact point. The hand box extends from the wrist toward the forearm.
- COCO `knife`, `spoon` and `fork` are weak from overhead. Fix the camera angle first; fine-tune only if needed.
- Bins are identified by slot. Swapping bins between slots mid-shift is unsupported.
- If det + pose can't hold 30 FPS on the Pi: run pose every Nth frame (flagged), then fall back to `yolov8n` for detection.
