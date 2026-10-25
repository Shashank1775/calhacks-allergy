# kitchen/vision: Camera, detection and tracking

**Owner:** team `vision` (agents).

Turns camera frames into tracked, stably identified bins, tools, hands and plates (`ObservationFrame`), and hands every frame, with its change mask, to the partner's entity store and interactions step. **The Hailo-8L isn't connected yet**, so everything runs locally on a laptop (video file or webcam, ONNX Runtime) and stays ready to upload.

## Running it

```bash
uv run python -m kitchen.vision.run --video data/recordings/prep.mp4 --out frames.jsonl   # the default zones
uv run python -m kitchen.vision.run --webcam 0 --serve                           # live, with the Vision HTTP API
.venv/bin/python -m kitchen.vision.run --picamera --serve                        # on the Pi, from its camera
uv run python -m kitchen.vision.run --video data/recordings/prep.mp4 --zones zones.json --out frames.jsonl
uv run python -m kitchen.vision.record --source camera:0 --zones zones.json --out data/recordings/run1
uv run python -m kitchen.vision.zones calibrate --image still.jpg --out zones.json  # click each zone's corners
uv run python -m kitchen.vision.server --fake --port 8002                          # the API on a test pattern
curl http://localhost:8002/health                     # {"fps": ..., "detector": "onnx", "last_frame_t_ms": ...}
curl --max-time 3 -o stream.bin http://localhost:8002/stream.mjpg                 # a few seconds of MJPEG
curl http://localhost:8002/zones                                                   # the zones in use
curl -X PUT http://localhost:8002/zones -H 'Content-Type: application/json' -d @zones.json   # replace them
```

Settings are `VISION_*` environment variables (`kitchen/vision/common.py`, `VisionSettings`), logged at startup. The ones you'll touch: `VISION_BUNDLE_DIR` (the model bundle: default `data/models/stock-yolov8s` on the laptop, `data/models/stock-yolov8s-hailo8l` on the Pi), `VISION_DETECTOR` (`onnx` or `hailo`), `VISION_PORT` (8002) and `VISION_CORS_ORIGINS` (the web app's origin, `http://localhost:5173`).

Without `--serve`, a video plays as fast as it can with its own timestamps from 0, so two runs give byte-identical output and the first `VISION_SHIFT_START_MS` (3 s) of the video is shift start. With `--serve` (or `--live`) it plays like a live camera: each frame waits for its place in the video, then is stamped with the clock, so "start plate" sees fresh frames. Live, the clock starts once the detector has loaded, so a slow model load can't eat the shift-start window. The JSONL and recording are complete as soon as the source ends; with `--serve` the API stays up (its `/health` going stale) until Ctrl-C or SIGTERM.

On a Mac, ONNX Runtime's CoreML provider runs detection and pose about ten times faster than the CPU (`VISION_ONNX_PROVIDERS`, CoreML then CPU by default). The whole frame loop takes about 40 ms per 1280×720 frame there, against about 300 ms on the CPU alone.

YOLOv8 (Ultralytics) is AGPL-3.0, which is fine for a non-distributed demo (AGENTS.md, Open questions).

## Zones

Every test, the dummy kitchen and a fresh checkout share one layout, `default_zones.json`. Points are normalized to the camera frame, with x running left to right and y top to bottom:

```
x 0          0.2         0.4         0.6         0.8          1
  +------++------++------++------++------+ +--------+          y 0.04
  |slot-1||slot-2||slot-3||slot-4||slot-5| |  rack  |
  | bin-1|| bin-2|| bin-3|| bin-4|| bin-5| | (clean |
  +------++------++------++------++------+ | stack) |          y 0.30
                                           +--------+          y 0.36
                    +---------+            +--------+          y 0.40
                    | plating |            | plate- |
                    |  spot   |            | stack  |
                    |         |            | (clean)|
  +------+          +---------+            +--------+          y 0.72 / 0.74
  | wash |                       +-----------------+           y 0.78
  |      |                       |      pass       |
  +------+                       +-----------------+           y 0.98 / 0.99
```

To match the props to it, point the camera straight down so the counter fills the frame. Put the five bins in slots 1 to 5 across the top of the picture, in `DEMO_BINS` order (`kitchen/orders/bins.py`): `bin-1` in `slot-1`, and so on. The utensil rack goes top right, the clean plates below it, the plating spot in the middle, the wash bottom left and the pass bottom right.

**Which file a run uses.** An explicit `--zones` path wins. Without one, `run.py` and the local runner's `--live` use `data/zones.json` if it exists (zones saved from the web app; `data/` is gitignored), else `default_zones.json`. The startup log names the file and its sha256. A bad file stops the run at startup: it never falls back to the default, which may not match the counter.

**Changing zones while running.** `PUT /zones` on the Vision API (`zones.LiveZones`) does these steps in order:
1. It checks the whole layout. Anything bad is `422 bad_zones` and changes nothing.
2. It saves the file. That's `data/zones.json`, or the `--zones` path if one was given. A failed write is a `500` and changes nothing.
3. It swaps the frame loop's lookup.
4. It replaces the `zone` table, so the gate, the verdict service and the screens see the new pass. If the table can't be written, the lookup and the file go back to the old layout.

`GET /zones` returns the zones in use.

## What happens here

- Read frames from a video file, a webcam or the Pi camera (picamera2, since OpenCV can't open it on a Pi 5). Each frame gets `t_ms` from the injected `Clock`.
- Detect knives, spoons, forks and bowls with YOLOv8s, and wrists with YOLOv8s-pose. Today this runs with ONNX Runtime; later the same model runs on the Hailo-8L, sharing the same pre- and post-processing code.
- Track with ByteTrack, then map each track to a stable entity ID (`bin-<slot>`, `tool-<class>-<n>`, `hand-p<k>-left|right`, `plate-<n>`) with an `origin`: `shift_start`, `clean_stack` or `unknown`.
- Give every detection a zone from the zones file: bin slots, plating spot, wash, clean stack, pass.
- When the cook taps "start plate", bind the plate at the plating spot to that ticket.
- Compute a **change mask** for every frame with the shared function (`kitchen/shared/change_mask.py`) and hand it over with each `ObservationFrame`; the partner's interactions step builds ConvLSTM clips from it.
- Record frames to JSONL and change masks to `change.npz`, and replay both byte for byte.

## Files

| File | What it does |
|---|---|
| `camera.py` | Frame sources: video file, webcam, and the Pi camera (`PiCameraSource`: picamera2 at 1280×720, continuous autofocus); JSONL replay is in `record.py` |
| `detect.py` | YOLOv8s detection and pose with ONNX Runtime, plus the pre- and post-processing every backend shares. `VISION_DETECTOR=onnx\|hailo` picks the backend (default `onnx`) |
| `hailo.py` | `HailoSession`: a drop-in for `onnxruntime.InferenceSession` that runs a HEF with HailoRT on the Pi and returns the same ONNX-shaped output, so `detect.py` keeps one post-processing path. Also the on-Pi device check. Imports `hailo_platform` only when the device is opened |
| `bundle.py` | Builds the deploy bundle on the laptop: ONNX copies, calibration frames, parity frames, manifest, and the HEFs (precompiled for stock weights) or `compile.sh` (custom weights) |
| `parity.py` | Runs the bundle's parity frames through ONNX (laptop) or the HEFs (Pi) and compares the detections |
| `pi_setup.sh` | Installs HailoRT on the Pi, makes the venv, and runs the device and parity checks |
| `common.py` | What the vision modules hand each other (`Detection`, `Person`, `SourceFrame`, `FrameOutput`, `BindRefused`) and `VisionSettings`, the one home for vision's thresholds and safety constants |
| `tracking.py` | ByteTrack-style tracking (two-stage IoU association, a lost buffer, track IDs never reused), the hand boxes, and each frame's observation detections. `VISION_TRACKER=motion` switches on the experimental motion tracker (below) |
| `compare_trackers.py` | Runs one video's detections through both trackers and prints IDs, frames tracked and unknown share per class, side by side |
| `identity.py` | Track → entity ID and `origin`. A track lost and re-acquired always becomes a new entity, except a bin, which is identified by its slot |
| `zones.py` | Zones file format and validator, which zones file a run uses, the click-to-calibrate tool, zone assignment (smallest zone wins, never a guess), `StateClient.set_zones`, and `LiveZones` (`PUT /zones` while running) |
| `default_zones.json` | The committed default layout ([Zones](#zones)) |
| `plates.py` | "Start plate": binds the one plate at the plating spot to a ticket, or refuses with the contract's 409 code |
| `record.py` | Records `frames.jsonl`, `change.npz` and a manifest; deterministic replay; the record CLI |
| `server.py` | Vision HTTP API (`/plates/start`, `/stream.mjpg`, `/health`, `GET` and `PUT /zones`) and `FakeVisionAPI` for outputs |
| `run.py` | Main loop: source → detect → track → zones → `ObservationFrame` + change mask → consumers and the API's status. Also the run CLI and `build_frame_loop()` for running in-process |

## The experimental motion tracker

The default tracker (`VISION_TRACKER=iou`) matches each detection to a track's last box. A utensil moving more than its own width between frames then gets a new ID, with unknown history, every frame. `VISION_TRACKER=motion` is a candidate fix, **off by default** until real overhead footage shows it helps. It changes how tracks are matched, never what is emitted (a box is always the detection's own):

- **Prediction.** A track seen in the last frame or two is matched at its predicted box: its last box moved by its velocity.
- **Recent first.** Those recent tracks pick first. A track lost for longer only gets the detections left over.
- **Second chance for movers.** A recent track that is moving out of its own box may take the one detection within one box diagonal of where it should be. It takes it only when no other track could claim that detection and no other detection is in its reach. A still object the detector missed for a frame never reaches for a neighbour, so a knife put down beside it can't take its identity.
- **Motion hand-off.** A new track started next to a track that just went missing takes over that track's *motion*, never its identity. A utensil grabbed at full speed then breaks its track once, not every frame.
- **No re-matching a lost mover.** A track lost while moving never matches again: its object isn't where it was lost, so a knife carried onto that spot is a different one. A still object, such as a knife covered by a hand, is still matched where it lay.
- **No re-matching across a crossing.** A lost track never takes a detection that overlaps a knife matched this frame, or that a moving track could also claim. That covers two knives crossing, or swapping places.
- **Stricter identity guards.** The ID-switch guard and the clean-stack loss count cover all tool classes together, so a knife the detector relabels "spoon" can't start clean. And a pose left/right wrist swap starts both hands over as new, unknown hands.

Anything ambiguous starts a new track with unknown history: a HOLD, never a guessed identity.

The simulation measured the repo's tracker on synthetic boxes with jitter, misses and score noise, at 10, 15 and 30 fps:

| Simulated case | `iou` | `motion` |
|---|---|---|
| IDs per 5 s, moving objects (0.5–1.5 frame-widths/s) | 24.7 | 13.7 |
| IDs per 5 s, a knife carried at 0.5 frame-widths/s, 15 fps | 58 | 6 |
| IDs per 5 s, still objects, or three knives side by side in a rack | 1.0 | 1.0 |
| A clean ID ends up on the other knife: head-on / offset / perpendicular crossing | 6% / 0% / 0% | 0% / 0% / 0% |
| A clean ID ends up on the other knife: two knives swap places | 75% | 27% |

Motion mode is never worse than `iou` in any crossing or takeover condition. It does use more IDs in some very fast (≥ 1 frame-width/s) back-and-forth motions, which means extra unknowns, in the HOLD direction.

To decide whether to switch it on, record the overhead shot list, then compare the two trackers on it:

```bash
uv run python -m kitchen.vision.compare_trackers --video data/recordings/overhead.mp4 --zones zones.json
uv run python -m kitchen.vision.compare_trackers --video data/recordings/overhead.mp4 --fps 15   # a slower camera
```

Fewer entity IDs per minute for the same objects means fewer identity breaks. Switch it on with `VISION_TRACKER=motion` (it is logged at startup). It can't fix two things:
- **Identical utensils swapped while hidden.** A knife put down on the spot another knife left less than a second earlier still takes over that knife's track, in both modes.
- **Missed detections.** Knives the detector doesn't see can't be tracked; that's for fine-tuning.

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
  - A track lost beyond the tracker's buffer (`track_lost_ms`, 1 s, measured in time so a slow frame rate or a camera stall can't stretch it) and re-acquired gets a **new** entity ID, with `origin: unknown` and `first_seen_t_ms` set to when it reappeared. Entity IDs are never reused within a shift (property test). A person re-acquired by pose gets new hand IDs, and a wrist unseen for longer than the buffer comes back as a new hand, `hand-p<k>-<side>-<n>`, with `origin: unknown`. No re-identification or track merging.
  - `origin: clean_stack` only for a tool or plate first seen in a `clean_stack` zone. A hand never comes out of a stack, so a hand first seen after shift start is `unknown` wherever it is.
  - An ID switch never starts clean. A new track that starts in a frame where a track of its class went unmatched may be that object under a new ID (a dirty knife carried fast into the rack, or covered there and uncovered to one side), so it is `unknown`, not `clean_stack`, and not `shift_start` either. The same goes for the hands of a new pose track while another is lost.
  - A clean stack can't vouch for something that vanished inside it. When a tool or plate track ends in a `clean_stack` zone, the next new track of that class first seen there is `unknown`, once per loss.
  - The one ID exception: a bowl first seen in bin slot `slot-<n>` is `bin-<n>`, and is `bin-<n>` again if re-acquired there, because orders keys bin profiles by `bin-<n>`. Swapping bins between slots mid-shift is unsupported.
  - A zone change (`PUT /zones`) never renames an entity or changes its origin. Origin is decided only at first sighting, so an object already tracked can't become clean because a clean stack was drawn around it. A bowl tracked as `bin-<n>` stays `bin-<n>` wherever the slots are drawn now. Only a bowl lost and re-acquired is named by the slot it reappears in, so redraw the slots before the bins are set out, not mid-shift.
- **Fail closed.**
  - `/plates/start` refuses when the spot has 0 plates or 2+ plates, when the plate is already bound to another ticket, or when the latest frame is more than `plate_bind_max_age_ms` (500 ms) old, so a frozen camera can't bind a stale plate. A plate last seen at the spot within the last `track_lost_ms` counts as still there, so a second plate the detector missed for a frame still refuses the bind.
  - Open `/stream.mjpg` clients wait for frames on their own few threads (`STREAM_THREADS`), so any number of them can't stall `/health` (the gate's staleness check) or `/plates/start`.
  - Detections are never interpolated or invented: a lost track emits nothing, a wrist below `wrist_min_confidence` is no hand, a point in no zone has zone `null`, and NaN or inf in a network's output raises instead of decoding to an empty kitchen.
  - Bad zones never half-apply. `PUT /zones` refuses a layout with any bad zone before anything changes, and the zones file is written whole or not at all. A change applies from the next frame: each frame reads the lookup once, so no frame is split between two layouts.
  - A detector error stops the frame loop, so `/health.last_frame_t_ms` stops advancing and the gate goes to `fault`. A consumer's error is logged and never starves the other consumers or the API.
- **Time runs forward.** Frame `t_ms` comes from the injected `Clock` and is strictly increasing, including across source restarts; the frame loop refuses a frame that doesn't follow the last. Replay keeps the recorded `t_ms`.

## Tests

- Tests live in `kitchen/vision/tests/`. Run `uv run pytest kitchen/vision`.
- They use `FakeStateClient`, `FakeEntityStore` and `FakeClock` (platform), fake detector sessions and tiny synthetic videos. The Hailo backend is tested against a mock runtime; real-device tests carry the `hardware` marker.
- `VISION_TEST_BUNDLE=data/models/stock-yolov8s uv run pytest kitchen/vision -m slow` runs the real YOLOv8s ONNX models (skipped when unset).
- We ship the recorded frame fixture pack (`kitchen/vision/tests/fixtures/pack/`, 100 KB or less), the JSONL replay source, a sample `zones.json`, the default layout (`default_zones.json`), and `FakeVisionAPI` (its `/zones` starts on the default layout and keeps a good `PUT` in memory).
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

**2. Get the bundle (laptop).** The board is a **Hailo-8L**, and Hailo-8 HEFs won't load on it. The ready-built `stock-yolov8s-hailo8l` bundle is on the [`vision-model-v1` release](https://github.com/Shashank1775/calhacks-allergy/releases/tag/vision-model-v1): `gh release download vision-model-v1 -p stock-yolov8s-hailo8l.zip -D data/models && unzip data/models/stock-yolov8s-hailo8l.zip -d data/models`. Only the HEFs are chip-specific; ONNX is the same for both chips, so the laptop keeps using `data/models/stock-yolov8s`. To build the bundle yourself instead: `--weights stock` downloads Model Zoo v2.18.0's precompiled `yolov8s.hef` and `yolov8s_pose.hef` (it refuses ONNX files that aren't stock-shaped). `--weights custom` writes `compile.sh` instead, which needs Hailo's Dataflow Compiler on an x86-64 Linux machine (not the Mac or the Pi):

```bash
uv run python -m kitchen.vision.bundle --name stock-yolov8s-hailo8l --weights stock \
    --det-onnx yolov8s.onnx --pose-onnx yolov8s-pose.onnx --video data/recordings/<prep>.mp4
uv run python -m kitchen.vision.parity run --bundle data/models/stock-yolov8s-hailo8l --backend onnx
```

**3. Set up the Pi (owner, once).** Raspberry Pi OS 64-bit Trixie. `install` puts on `hailo-all` (HailoRT 4.23.0), holds it, and makes a venv that can see the system's `hailo_platform` and `picamera2`, with the `server` and `ml` extras (the Vision API and the local runner's live mode need them):

```bash
bash kitchen/vision/pi_setup.sh install && sudo reboot
```

**4. Upload and check (owner).** Copy the bundle, run the device check (FPS for det, pose and both; we need 30 or more) and the parity run, then compare on the laptop:

```bash
rsync -a data/models/stock-yolov8s-hailo8l/ <user>@raspberrypi.local:<repo>/data/models/stock-yolov8s-hailo8l/
bash kitchen/vision/pi_setup.sh check data/models/stock-yolov8s-hailo8l          # on the Pi
scp <user>@raspberrypi.local:<repo>/data/models/stock-yolov8s-hailo8l/parity/hailo.npz data/models/stock-yolov8s-hailo8l/parity/
uv run python -m kitchen.vision.parity compare data/models/stock-yolov8s-hailo8l/parity/onnx.npz data/models/stock-yolov8s-hailo8l/parity/hailo.npz
```

`compare` exits 1 unless the HEF finds at least 90% of the ONNX boxes (and vice versa) with mean IoU 0.8 or more and mean score difference 0.1 or less. Then set `VISION_DETECTOR=hailo` and check `/health` reports `detector: hailo`.

**5. Run the kitchen live (owner).** Set the props out on the [default layout](#zones), or designate the zones in the web app (saved to `data/zones.json` on the Pi), or calibrate them on a still from the mounted camera (the calibration tool needs a screen, so click on the laptop). Then run the local runner's live mode on the Pi. The web app on the laptop points `WEB_STATE_URL` and `WEB_ORDERS_URL` at `http://<pi-ip>:8001` and `WEB_VISION_URL` at `http://<pi-ip>:8002`; both CORS settings list the address the browser opens the web app on.

```bash
rpicam-still -o still.jpg                                                   # on the Pi: 16:9, the same view as the frames
scp <user>@raspberrypi.local:<repo>/still.jpg . && uv run python -m kitchen.vision.zones calibrate --image still.jpg --out zones.json
scp zones.json <user>@raspberrypi.local:<repo>/
VISION_DETECTOR=hailo VISION_BUNDLE_DIR=data/models/stock-yolov8s-hailo8l \
  ORDERS_CORS_ORIGINS=http://localhost:5173 VISION_CORS_ORIGINS=http://localhost:5173 \
  .venv/bin/python tools/run_local.py --live --picamera --zones zones.json      # on the Pi
```

Unverified until the device is connected: `PiCameraSource` (tested against a stand-in `picamera2`), the HailoRT calls in `HailoNetwork` (written against HailoRT 4.23's `InferModel` API and tested only on a mock), and that the pose HEF applies the score sigmoid on the chip, as Hailo's own decoder assumes. The device check and parity run settle the HailoRT parts; step 5 settles the camera.

## Watch out for

- Hands occlude tools, so ByteTrack switches IDs and causes unknown-history holds. Safe, but it hurts the demo: mount the camera overhead and tune the track buffer. The ID-switch guard makes this stricter: a plate taken from the stack while any bowl (a bin too) is occluded, or a wrist hidden for over a second, comes back `unknown` and holds until washed.
- What no tracker catches: an object hidden for longer than `track_lost_ms` (a knife covered by a hand the whole way) while it is carried into a clean stack comes back `clean_stack`. So can an object swapped for another at the same spot while hidden, which then takes over the hidden one's track. Keep the clean stack where hands don't linger.
- A wrist is not the contact point. The hand box is a square (`VISION_HAND_BOX_SIZE`) placed beyond the wrist, away from the elbow, and shifted back inside the frame at the edges.
- COCO pose is trained on people seen from the front. From straight overhead it may find few wrists: measure it on the first real recording before the demo, and tune `VISION_WRIST_MIN_CONFIDENCE` only upward if it invents hands.
- Bowls are both bins and plates (COCO has no plate class): the bin slots decide which is which, so the zones file has to be right.
- COCO `knife`, `spoon` and `fork` are weak from overhead. Fix the camera angle first; fine-tune only if needed.
- Bins are identified by slot. Swapping bins between slots mid-shift is unsupported.
- If det + pose can't hold 30 FPS on the Pi: run pose every Nth frame (flagged), then fall back to `yolov8n` for detection.
