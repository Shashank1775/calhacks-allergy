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
| `hailo.py` | `HailoSession`: a drop-in for `onnxruntime.InferenceSession` that runs a HEF with HailoRT on the Pi and returns the same ONNX-shaped output, so `detect.py` keeps one post-processing path. Also the on-Pi device check. Imports `hailo_platform` only when the device is opened |
| `bundle.py` | Builds the deploy bundle on the laptop: ONNX copies, calibration frames, parity frames, manifest, and the HEFs (precompiled for stock weights) or `compile.sh` (custom weights) |
| `parity.py` | Runs the bundle's parity frames through ONNX (laptop) or the HEFs (Pi) and compares the detections |
| `pi_setup.sh` | Installs HailoRT on the Pi, makes the venv, and runs the device and parity checks |
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

**One processing path.** Both backends take the same letterboxed float `[1, 3, 640, 640]` tensor (0 to 1) and return the same Ultralytics output (`[1, 84, 8400]` detection, `[1, 56, 8400]` pose). `HailoSession` converts on the way in (uint8 NHWC) and on the way out: the detection HEF ends in HailoRT's NMS, so its boxes become output columns; the pose HEF returns nine raw heads, decoded on the host with Ultralytics' math. `detect.py` only chooses how to open the sessions:

```python
if settings.detector == "hailo":
    from kitchen.vision.hailo import open_sessions
    sessions = open_sessions(bundle_dir)          # {"det": HailoSession, "pose": HailoSession}, one device
else:
    sessions = {name: onnxruntime.InferenceSession(str(bundle_dir / f"{name}.onnx")) for name in ("det", "pose")}
outputs = sessions["det"].run(None, {"images": tensor})  # same call, same output, either backend
```

**1. Export ONNX (laptop).** Fixed 640×640, opset 11 so the Hailo Model Zoo recipes accept custom weights too:

```bash
uvx --from ultralytics yolo export model=yolov8s.pt format=onnx imgsz=640 opset=11
uvx --from ultralytics yolo export model=yolov8s-pose.pt format=onnx imgsz=640 opset=11
```

**2. Build the bundle (laptop).** `--weights stock` downloads Model Zoo v2.18.0's precompiled `yolov8s.hef` and `yolov8s_pose.hef` (it refuses ONNX files that aren't stock-shaped). `--weights custom` writes `compile.sh` instead, which needs Hailo's Dataflow Compiler on an x86-64 Linux machine (not the Mac or the Pi):

```bash
uv run python -m kitchen.vision.bundle --name kitchen-v1 --weights stock \
    --det-onnx yolov8s.onnx --pose-onnx yolov8s-pose.onnx --video data/recordings/<prep>.mp4
uv run python -m kitchen.vision.parity run --bundle data/models/kitchen-v1 --backend onnx
```

**3. Set up the Pi (owner, once).** Raspberry Pi OS 64-bit Trixie. `install` puts on `hailo-all` (HailoRT 4.23.0), holds it, and makes a venv that can see the system's `hailo_platform`:

```bash
bash kitchen/vision/pi_setup.sh install && sudo reboot
```

**4. Upload and check (owner).** Copy the bundle, run the device check (FPS for det, pose and both; we need 30 or more) and the parity run, then compare on the laptop:

```bash
rsync -a data/models/kitchen-v1/ <user>@raspberrypi.local:<repo>/data/models/kitchen-v1/
bash kitchen/vision/pi_setup.sh check data/models/kitchen-v1          # on the Pi
scp <user>@raspberrypi.local:<repo>/data/models/kitchen-v1/parity/hailo.npz data/models/kitchen-v1/parity/
uv run python -m kitchen.vision.parity compare data/models/kitchen-v1/parity/onnx.npz data/models/kitchen-v1/parity/hailo.npz
```

`compare` exits 1 unless the HEF finds at least 90% of the ONNX boxes (and vice versa) with mean IoU 0.8 or more and mean score difference 0.1 or less. Then set `VISION_DETECTOR=hailo` and check `/health` reports `detector: hailo`.

Unverified until the device is connected: the HailoRT calls in `HailoNetwork` (written against HailoRT 4.23's `InferModel` API and tested only on a mock), and that the pose HEF applies the score sigmoid on the chip, as Hailo's own decoder assumes. The device check and parity run settle both.

## Watch out for

- Hands occlude tools, so ByteTrack switches IDs and causes unknown-history holds. Safe, but it hurts the demo: mount the camera overhead and tune the track buffer.
- A wrist is not the contact point. The hand box extends from the wrist toward the forearm.
- COCO `knife`, `spoon` and `fork` are weak from overhead. Fix the camera angle first; fine-tune only if needed.
- Bins are identified by slot. Swapping bins between slots mid-shift is unsupported.
- If det + pose can't hold 30 FPS on the Pi: run pose every Nth frame (flagged), then fall back to `yolov8n` for detection.
