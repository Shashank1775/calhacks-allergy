# kitchen/vision: Camera, detection and tracking

**Owner:** team `vision` (agents).

Turns camera frames into tracked, stably identified bins, tools, hands and plates (`ObservationFrame`), and hands every frame, with its change mask, to the partner's entity store and interactions step. **The Hailo-8 isn't connected yet**, so everything runs locally on a laptop (video file or webcam, ONNX Runtime) and stays ready to upload.

## Running it

```bash
uv run python -m kitchen.vision.run --video data/recordings/prep.mp4 --zones zones.json --out frames.jsonl
uv run python -m kitchen.vision.run --webcam 0 --zones zones.json --serve        # live, with the Vision HTTP API
uv run python -m kitchen.vision.record --source camera:0 --zones zones.json --out data/recordings/run1
uv run python -m kitchen.vision.zones calibrate --image still.jpg --out zones.json  # click each zone's corners
uv run python -m kitchen.vision.server --fake --port 8002                          # the API on a test pattern
curl http://localhost:8002/health                     # {"fps": ..., "detector": "onnx", "last_frame_t_ms": ...}
curl --max-time 3 -o stream.bin http://localhost:8002/stream.mjpg                 # a few seconds of MJPEG
```

Settings are `VISION_*` environment variables (`kitchen/vision/common.py`, `VisionSettings`), logged at startup. The ones you'll touch: `VISION_BUNDLE_DIR` (the model bundle, default `data/models/stock-yolov8s`), `VISION_DETECTOR` (`onnx` or `hailo`), `VISION_PORT` (8002) and `VISION_CORS_ORIGINS` (the web app's origin, `http://localhost:5173`).

Without `--serve`, a video plays as fast as it can with its own timestamps, so two runs give identical output. With `--serve` (or `--live`) it plays like a live camera: each frame waits for its place in the video, then is stamped with the clock, so "start plate" sees fresh frames.

On a Mac, ONNX Runtime's CoreML provider runs detection and pose about ten times faster than the CPU (`VISION_ONNX_PROVIDERS`, CoreML then CPU by default). The whole frame loop takes about 40 ms per 1280×720 frame there, against about 300 ms on the CPU alone.

YOLOv8 (Ultralytics) is AGPL-3.0, which is fine for a non-distributed demo (AGENTS.md, Open questions).

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
| `common.py` | What the vision modules hand each other (`Detection`, `Person`, `SourceFrame`, `FrameOutput`, `BindRefused`) and `VisionSettings`, the one home for vision's thresholds and safety constants |
| `tracking.py` | ByteTrack-style tracking (two-stage IoU association, a lost buffer, track IDs never reused), the hand boxes, and each frame's observation detections |
| `identity.py` | Track → entity ID and `origin`. A track lost and re-acquired always becomes a new entity, except a bin, which is identified by its slot |
| `zones.py` | Zones file format and validator, the click-to-calibrate tool, zone assignment (smallest zone wins, never a guess), `StateClient.set_zones` |
| `plates.py` | "Start plate": binds the one plate at the plating spot to a ticket, or refuses with the contract's 409 code |
| `record.py` | Records `frames.jsonl`, `change.npz` and a manifest; deterministic replay; the record CLI |
| `server.py` | Vision HTTP API (`/plates/start`, `/stream.mjpg`, `/health`) and `FakeVisionAPI` for outputs |
| `run.py` | Main loop: source → detect → track → zones → `ObservationFrame` + change mask → consumers and the API's status. Also the run CLI and `build_frame_loop()` for running in-process |

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

- **Known-clean source.**
  - A track lost beyond the tracker's buffer and re-acquired gets a **new** entity ID, with `origin: unknown` and `first_seen_t_ms` set to when it reappeared. Entity IDs are never reused within a shift (property test). A person re-acquired by pose gets new hand IDs. No re-identification or track merging.
  - `origin: clean_stack` only for a tool or plate first seen in a `clean_stack` zone. A hand never comes out of a stack, so a hand first seen after shift start is `unknown` wherever it is.
  - A clean stack can't vouch for something that vanished inside it. When a tool or plate track ends in a `clean_stack` zone, the next new track of that class first seen there is `unknown`, once per loss (a dirty knife put back in the rack and hidden by a hand must not come back clean).
  - The one ID exception: a bowl first seen in bin slot `slot-<n>` is `bin-<n>`, and is `bin-<n>` again if re-acquired there, because orders keys bin profiles by `bin-<n>`. Swapping bins between slots mid-shift is unsupported.
- **Fail closed.**
  - `/plates/start` refuses when the spot has 0 plates or 2+ plates, when the plate is already bound to another ticket, or when the latest frame is more than `plate_bind_max_age_ms` (500 ms) old, so a frozen camera can't bind a stale plate.
  - Detections are never interpolated or invented: a lost track emits nothing, a wrist below `wrist_min_confidence` is no hand, a point in no zone has zone `null`, and NaN or inf in a network's output raises instead of decoding to an empty kitchen.
  - A detector error stops the frame loop, so `/health.last_frame_t_ms` stops advancing and the gate goes to `fault`. A consumer's error is logged and never starves the other consumers or the API.
- **Time runs forward.** Frame `t_ms` comes from the injected `Clock` and is strictly increasing, including across source restarts; the frame loop refuses a frame that doesn't follow the last. Replay keeps the recorded `t_ms`.

## Tests

- Tests live in `kitchen/vision/tests/`. Run `uv run pytest kitchen/vision`.
- They use `FakeStateClient`, `FakeEntityStore` and `FakeClock` (platform), fake detector sessions and tiny synthetic videos. The Hailo backend is tested against a mock runtime; real-device tests carry the `hardware` marker.
- `VISION_TEST_BUNDLE=data/models/stock-yolov8s uv run pytest kitchen/vision -m slow` runs the real YOLOv8s ONNX models (skipped when unset).
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
- A wrist is not the contact point. The hand box is a square (`VISION_HAND_BOX_SIZE`) placed beyond the wrist, away from the elbow, and shifted back inside the frame at the edges.
- COCO pose is trained on people seen from the front. From straight overhead it may find few wrists: measure it on the first real recording before the demo, and tune `VISION_WRIST_MIN_CONFIDENCE` only upward if it invents hands.
- Bowls are both bins and plates (COCO has no plate class): the bin slots decide which is which, so the zones file has to be right.
- COCO `knife`, `spoon` and `fork` are weak from overhead. Fix the camera angle first; fine-tune only if needed.
- Bins are identified by slot. Swapping bins between slots mid-shift is unsupported.
- If det + pose can't hold 30 FPS on the Pi: run pose every Nth frame (flagged), then fall back to `yolov8n` for detection.
