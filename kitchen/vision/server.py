"""The Pi's HTTP API (POST /plates/start, POST /frames, GET /stream.mjpg, GET /health, GET and PUT /zones) and
FakeVisionAPI.

Owner: vision (agents)
Step:  vision
Reads: the latest FrameOutput and RGB frame (VisionStatus, updated by the frame loop), a start_plate callable
       (plates.py binding), get_zones and put_zones callables (zones.LiveZones), a push_frame callable
       (camera.PushedFrames, when the web app sends the frames), VISION_PORT, VISION_DETECTOR and VISION_CORS_ORIGINS
       (CLI only)
Emits: the Vision HTTP API (contracts/apis.md), with the standard error body and CORS for VISION_CORS_ORIGINS only
Rules: fail closed: /health last_frame_t_ms stops advancing when frames stop, so the gate can go to fault;
       /plates/start never guesses, every refusal is a 409 with the contract's code;
       PUT /zones changes nothing unless the whole layout is valid and saved (422 bad_zones, 500, 503);
       POST /frames never takes the browser's time and never queues: vision stamps the frame, and only the newest
       waits; when pushes stop, frames stop and /health goes stale;
       endpoints never block the frame loop: update() only swaps references, JPEGs are encoded in the HTTP thread;
       /stream.mjpg?boxes=true is display only: it draws the detector's own boxes for each frame, never entity IDs or
       risk, and nothing on the verdict path reads it;
       open streams never stall /health or /plates/start: they wait for frames on their own few threads

    uv run python -m kitchen.vision.server --fake --port 8002     # FakeVisionAPI: test pattern, no camera or models
    curl localhost:8002/health                                     # {"fps": ..., "detector": ..., "last_frame_t_ms": ...}
    curl -o - localhost:8002/stream.mjpg | head -c 2000            # multipart MJPEG parts
    curl -o - 'localhost:8002/stream.mjpg?boxes=true' | head -c 2000   # the same, with the detector's boxes drawn
    curl -X POST localhost:8002/plates/start -H 'Content-Type: application/json' -d '{"ticket_id": "t7"}'
    curl localhost:8002/zones                                      # {"zones": [{"zone_id", "kind", "polygon"}, ...]}
    curl -X PUT localhost:8002/zones -H 'Content-Type: application/json' -d @kitchen/vision/default_zones.json
    curl -X POST localhost:8002/frames -H 'Content-Type: image/jpeg' --data-binary @frame.jpg   # 204 (--from-web)
"""

import argparse
import json
import logging
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable

import anyio
import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from kitchen.shared.clock import SystemClock
from kitchen.shared.config import load_settings
from kitchen.shared.interfaces import Clock, StateUnavailable
from kitchen.shared.models import Zone, validate
from kitchen.vision.common import BadFrame, BindRefused, Detection, FrameOutput, VisionSettings
from kitchen.vision.zones import DEFAULT_ZONES_PATH, ZoneFileError, load_zones, parse_zones, zone_record

MJPEG_BOUNDARY = "frame"

# How much each new frame-to-frame interval moves the fps estimate (an exponential moving average).
FPS_SMOOTHING = 0.1

# A stream with no new frame for this long ends, so a client of a stalled camera sees the stream stop instead of a
# frozen picture that looks live, and a disconnected client's thread is freed.
STREAM_IDLE_S = 5.0

# How many threads all open streams share to wait for frames and encode them. Streams never use the pool that runs
# /health and /plates/start, so a room full of open streams can't stall the gate's health check or "start plate".
STREAM_THREADS = 8

# Box colors (RGB) for the fast camera view, by detector class. Neutral on purpose: these boxes say what the detector
# sees, not allergen risk, so none of them is the red the entity view uses for a risky box.
BOX_COLORS = {"knife": (255, 170, 0), "spoon": (0, 200, 255), "fork": (190, 120, 255), "bowl": (90, 220, 120)}
OTHER_BOX_COLOR = (255, 255, 255)

# The contract error code for each HTTP error FastAPI raises itself (unknown path, wrong method).
HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}

logger = logging.getLogger("kitchen.vision.server")


class VisionStatus:
    """The latest frame and health, shared between the frame loop (one writer) and the HTTP handlers (readers).

    update() only swaps references under a short lock, so a slow HTTP client can never hold up the frame loop. The
    RGB array is stored by reference: the frame loop must hand over a fresh array each frame and never write into
    one it has already passed here. The detector's boxes for the frame are kept with it, for the fast camera view.
    """

    def __init__(self, detector: str, history_ms: int = VisionSettings.track_lost_ms):
        self.detector = detector
        self.history_ms = history_ms
        self._new_frame = threading.Condition()
        self._observation = None
        self._history: deque = deque()  # observations within history_ms of the newest, oldest first
        self._rgb = None
        self._objects: list[Detection] = []  # the detector's own boxes for the latest frame
        self._frame_number = 0  # counts update() calls; the stream uses it, since frame_id may restart with a source
        self._fps = 0.0

        # Encoding has its own lock so the frame loop never waits on it, and so each frame is encoded at most once per
        # view (plain, or with boxes) however many clients are streaming.
        self._encode_lock = threading.Lock()
        self._jpegs: dict[bool, tuple[int, bytes]] = {}  # with boxes? -> (frame number, JPEG bytes)

    def update(self, output: FrameOutput, rgb: np.ndarray, objects: list[Detection] = ()) -> None:
        """Record one frame from the frame loop: its observation, its RGB pixels, the detector's boxes for it, and the
        new fps estimate."""
        observation = output.observation
        with self._new_frame:
            if self._observation is not None:
                interval_ms = observation["t_ms"] - self._observation["t_ms"]
                self._fps = smoothed_fps(self._fps, interval_ms)
            self._observation = observation
            self._history.append(observation)
            while self._history[0]["t_ms"] < observation["t_ms"] - self.history_ms:
                self._history.popleft()
            self._rgb = rgb
            self._objects = list(objects)
            self._frame_number += 1
            self._new_frame.notify_all()

    def latest_observation(self) -> dict | None:
        """The most recent ObservationFrame, or None before the first frame."""
        with self._new_frame:
            return self._observation

    def recent_observations(self) -> list[dict]:
        """The ObservationFrames within history_ms of the newest, oldest first (empty before the first frame), so
        "start plate" can count a plate the detector missed in the latest frame."""
        with self._new_frame:
            return list(self._history)

    def health(self) -> dict:
        """The /health body: exactly fps, detector and last_frame_t_ms (contracts/apis.md).

        last_frame_t_ms is the newest frame's own t_ms, never the time now, so it stops advancing the moment frames
        stop and the gate's staleness check can fire (safety rule: fail closed). None before the first frame.
        """
        with self._new_frame:
            last_frame_t_ms = None if self._observation is None else self._observation["t_ms"]
            return {"fps": round(self._fps, 1), "detector": self.detector, "last_frame_t_ms": last_frame_t_ms}

    def next_jpeg(self, after: int, timeout_s: float, boxes: bool = False) -> tuple[int, bytes] | None:
        """Wait for a frame newer than frame number `after` and return (its frame number, its JPEG bytes), with the
        detector's boxes for that frame drawn on it when `boxes` is set.

        Returns None if no newer frame arrives within timeout_s. Pass 0 for the first call and the returned frame
        number after that. A client that falls behind skips straight to the latest frame.
        """
        with self._new_frame:
            has_newer = self._new_frame.wait_for(lambda: self._frame_number > after, timeout=timeout_s)
            if not has_newer:
                return None
            frame_number = self._frame_number
            rgb = self._rgb
            objects = self._objects

        with self._encode_lock:
            cached_number, jpeg = self._jpegs.get(boxes, (0, b""))
            if cached_number != frame_number:
                jpeg = encode_jpeg(draw_boxes(rgb, objects) if boxes else rgb)
                self._jpegs[boxes] = (frame_number, jpeg)
            return frame_number, jpeg


def smoothed_fps(fps: float, interval_ms: int) -> float:
    """Move the fps estimate toward 1000 / interval_ms. A non-positive interval says nothing about the rate."""
    if interval_ms <= 0:
        return fps
    instant_fps = 1000.0 / interval_ms
    if fps == 0.0:
        return instant_fps
    return (1 - FPS_SMOOTHING) * fps + FPS_SMOOTHING * instant_fps


def draw_boxes(rgb: np.ndarray, objects: list[Detection]) -> np.ndarray:
    """A copy of the RGB frame with each detection's box and "class confidence" label drawn on it.

    The boxes are the detector's own, for this very frame, so they move with the picture. Display only: no entity IDs,
    no allergen risk.
    """
    import cv2  # the ml extra, as in encode_jpeg

    out = rgb.copy()
    height, width = out.shape[:2]
    thickness = max(2, round(min(width, height) / 240))
    font_scale = max(0.5, min(width, height) / 900)
    for detection in objects:
        x1, y1, x2, y2 = detection.box
        top_left = (round(x1 * width), round(y1 * height))
        bottom_right = (round(x2 * width), round(y2 * height))
        color = BOX_COLORS.get(detection.class_name, OTHER_BOX_COLOR)
        cv2.rectangle(out, top_left, bottom_right, color, thickness)

        label = f"{detection.class_name} {detection.confidence:.2f}"
        (text_width, text_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        # The label sits on a filled strip just above the box, or just inside it at the top edge of the frame.
        label_y = top_left[1] if top_left[1] > text_height + baseline else top_left[1] + text_height + baseline
        cv2.rectangle(
            out, (top_left[0], label_y - text_height - baseline), (top_left[0] + text_width, label_y), color, -1
        )
        cv2.putText(
            out,
            label,
            (top_left[0], label_y - baseline),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )
    return out


def encode_jpeg(rgb: np.ndarray) -> bytes:
    """JPEG bytes of an RGB uint8 frame."""
    import cv2  # the ml extra; imported here so the API module loads without it until a stream is asked for

    ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError(f"could not encode a {rgb.shape} frame as JPEG")
    return jpeg.tobytes()


async def mjpeg_parts(
    status: VisionStatus, idle_s: float, threads: anyio.CapacityLimiter, boxes: bool = False
) -> AsyncIterator[bytes]:
    """One multipart MJPEG part per new frame, with the detector's boxes drawn when `boxes` is set, ending once no new
    frame has come for idle_s.

    The wait for each frame runs on one of `threads`, never on the event loop (it blocks) or on the default pool.
    """
    frame_number = 0
    while True:
        frame = await anyio.to_thread.run_sync(status.next_jpeg, frame_number, idle_s, boxes, limiter=threads)
        if frame is None:
            return
        frame_number, jpeg = frame
        header = f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
        yield header.encode() + jpeg + b"\r\n"


def parse_cors_origins(text: str) -> list[str]:
    """Split VISION_CORS_ORIGINS ("a,b") into the list create_app takes."""
    return [origin.strip() for origin in text.split(",") if origin.strip()]


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """The standard error body from contracts/apis.md (HTTP conventions)."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


class StartPlateRequest(BaseModel):
    """Body of POST /plates/start.

    Extra fields are refused, not ignored: a client that sends a plate_id believes it chose the plate, and the API
    never binds a plate other than the one it sees. A blank ticket_id is refused so a plate is never locked to a
    ticket that cannot exist.
    """

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(pattern=r"\S")


def zones_body(zones: list[Zone]) -> dict:
    """The GET and PUT /zones body: {"zones": [...]}, in the zones file format."""
    return {"zones": [zone_record(zone) for zone in zones]}


def create_app(
    status: VisionStatus,
    start_plate: Callable[[str], str],
    get_zones: Callable[[], list[Zone]],
    put_zones: Callable[[object], list[Zone]],
    cors_origins: list[str],
    stream_idle_s: float = STREAM_IDLE_S,
    push_frame: Callable[[bytes], None] | None = None,
) -> FastAPI:
    """The Vision HTTP API over a VisionStatus, a start_plate(ticket_id) -> plate_id binding function, the zones in
    use (get_zones) and their replacement (put_zones(parsed JSON) -> the zones now in use; zones.LiveZones), and, when
    the web app sends the frames, push_frame(JPEG bytes) (camera.PushedFrames; None when vision reads its own camera).

    start_plate raises BindRefused (409 with its code) or StateUnavailable (503); any other error is a 500
    internal_error. The API never picks a plate itself. put_zones raises ZoneFileError (422 bad_zones) or
    StateUnavailable (503); any other error, such as the zones file not being writable, is a 500 internal_error.
    push_frame raises BadFrame (422 bad_frame); without it, POST /frames is a 409 not_web_source. Every error uses the
    contract's error body, and CORS is sent only for cors_origins.
    """
    if "*" in cors_origins:
        # contracts/apis.md HTTP conventions: browsers may call this API only from the listed screens.
        raise ValueError("CORS origins must be listed; a wildcard is not allowed")

    app = FastAPI(title="Vision API")
    stream_threads = anyio.CapacityLimiter(STREAM_THREADS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(BindRefused)
    def bind_refused(request, exc):
        return error_response(409, exc.code, str(exc))

    @app.exception_handler(StateUnavailable)
    def state_unavailable(request, exc):
        return error_response(503, "state_unavailable", str(exc))

    @app.exception_handler(ZoneFileError)
    def bad_zones(request, exc):
        return error_response(422, "bad_zones", str(exc))

    @app.exception_handler(RequestValidationError)
    def bad_request(request, exc):
        return error_response(400, "bad_request", str(exc.errors()))

    @app.exception_handler(HTTPException)
    def http_error(request, exc):
        return error_response(exc.status_code, HTTP_ERROR_CODES.get(exc.status_code, "http_error"), str(exc.detail))

    @app.post("/plates/start")
    def plates_start(body: StartPlateRequest):
        try:
            plate_id = start_plate(body.ticket_id)
        except (BindRefused, StateUnavailable):
            raise  # their handlers answer 409 and 503 with the contract code
        except Exception:
            # Any other failure binds nothing and is answered here, inside the CORS middleware, so the tablet can
            # still read the standard error body instead of an opaque network failure.
            logger.exception("start_plate failed for ticket %s", body.ticket_id)
            return error_response(500, "internal_error", "could not start the plate")
        return {"plate_id": plate_id}

    @app.get("/health")
    def health():
        return status.health()

    @app.get("/zones")
    def zones_get():
        return zones_body(get_zones())

    def replace_zones(data: object):
        try:
            zones = put_zones(data)
        except (ZoneFileError, StateUnavailable):
            raise  # their handlers answer 422 and 503 with the contract code
        except Exception as error:
            # The zones file couldn't be written, or a bug: put_zones has changed nothing. Answered here, inside the
            # CORS middleware, so the zone editor can read why.
            logger.exception("PUT /zones failed")
            return error_response(500, "internal_error", f"the zones were not changed: {error}")
        return zones_body(zones)

    @app.put("/zones")
    async def zones_put(request: Request):
        # The body is read raw, so a body that isn't JSON at all is a bad zones file (422) like any other.
        try:
            data = json.loads(await request.body())
        except ValueError:
            return error_response(422, "bad_zones", "the body is not JSON")
        # Writing the file and swapping the lookup block, so they run off the event loop.
        return await anyio.to_thread.run_sync(replace_zones, data)

    def take_frame(jpeg_bytes: bytes):
        try:
            push_frame(jpeg_bytes)
        except BadFrame as error:
            return error_response(422, "bad_frame", str(error))
        except Exception:
            # A bug: no frame was taken. Answered here, inside the CORS middleware, so the web app can read it.
            logger.exception("POST /frames failed")
            return error_response(500, "internal_error", "the frame was not taken")
        return Response(status_code=204)

    @app.post("/frames")
    async def frames_post(request: Request):
        if push_frame is None:
            return error_response(
                409, "not_web_source", "vision reads its own camera or video; run it with --from-web to send frames"
            )
        # The body is read raw: it is the JPEG itself. Decoding takes milliseconds, so it runs off the event loop.
        jpeg_bytes = await request.body()
        return await anyio.to_thread.run_sync(take_frame, jpeg_bytes)

    @app.get("/stream.mjpg")
    def stream(boxes: bool = False):
        # boxes=true is the fast camera view: each frame with the detector's own boxes for it, drawn here.
        media_type = f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}"
        return StreamingResponse(mjpeg_parts(status, stream_idle_s, stream_threads, boxes), media_type=media_type)

    return app


# ---------- FakeVisionAPI: the same API with a test pattern and an in-memory binding, for outputs ----------


class FakeSceneRequest(BaseModel):
    """Body of the fake-only POST /fake/scene: what to change; a field left out stays as it is."""

    plates_at_spot: list[str] | None = None
    frames_running: bool | None = None


class FakeVisionAPI:
    """The Vision HTTP API without a camera or models, so the web app can be built against it.

    `app` serves the contract endpoints. Frames are a moving test pattern pushed by push_frame() (the CLI calls it
    on a timer). /plates/start follows the real refusal rules against `plates_at_spot` and an in-memory binding.
    GET /zones starts on the committed default layout, and PUT /zones checks a layout exactly as the real one does
    (422 bad_zones, nothing changed) and keeps it in memory: no file, no zone table. The fake-only POST /fake/scene
    sets the plates at the spot and stops or restarts frames, so outputs can exercise every 409 and the gate's
    stale-health fault.
    """

    def __init__(
        self,
        plates_at_spot: list[str] | None = None,
        detector: str = "onnx",
        cors_origins: list[str] | None = None,
        clock: Clock | None = None,
        zones: list[Zone] | None = None,
    ):
        self.plates_at_spot = ["plate-1"] if plates_at_spot is None else list(plates_at_spot)
        self.bindings: dict[str, str] = {}  # plate_id -> ticket_id
        self.zones = load_zones(DEFAULT_ZONES_PATH) if zones is None else list(zones)
        self.frames_running = True
        self.status = VisionStatus(detector)
        self.clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._frame_id = 0

        origins = [VisionSettings.cors_origins] if cors_origins is None else cors_origins
        self.app = create_app(self.status, self.start_plate, self.get_zones, self.put_zones, origins)

        @self.app.post("/fake/scene")
        def fake_scene(body: FakeSceneRequest):
            return self.set_scene(body.plates_at_spot, body.frames_running)

    def start_plate(self, ticket_id: str) -> str:
        """Bind the one plate at the spot to ticket_id, refusing exactly as the real binding does (never guessing)."""
        with self._lock:
            if not self.plates_at_spot:
                raise BindRefused("no_plate_at_spot", "no plate at the plating spot")
            if len(self.plates_at_spot) > 1:
                raise BindRefused("multiple_plates_at_spot", f"{len(self.plates_at_spot)} plates at the plating spot")
            plate_id = self.plates_at_spot[0]
            bound_to = self.bindings.get(plate_id)
            if bound_to is not None and bound_to != ticket_id:
                raise BindRefused("plate_already_bound", f"{plate_id} is already bound to {bound_to}")
            self.bindings[plate_id] = ticket_id
            return plate_id

    def get_zones(self) -> list[Zone]:
        """The fake's zones in use."""
        with self._lock:
            return list(self.zones)

    def put_zones(self, data: object) -> list[Zone]:
        """Replace the fake's zones with the zones file `data`, or raise ZoneFileError and keep the old ones."""
        zones = parse_zones(data)
        with self._lock:
            self.zones = zones
            return list(zones)

    def set_scene(self, plates_at_spot: list[str] | None, frames_running: bool | None) -> dict:
        """Change the plates at the spot and whether frames flow; returns the scene as it now is."""
        with self._lock:
            if plates_at_spot is not None:
                self.plates_at_spot = list(plates_at_spot)
            if frames_running is not None:
                self.frames_running = frames_running
            return {"plates_at_spot": list(self.plates_at_spot), "frames_running": self.frames_running}

    def push_frame(self) -> bool:
        """Hand the status one test-pattern frame, unless frames are stopped. Returns whether a frame was pushed."""
        with self._lock:
            if not self.frames_running:
                return False
            frame_id = self._frame_id
            self._frame_id += 1

        observation = validate(
            {"frame_id": frame_id, "t_ms": self.clock.now_ms(), "camera_id": "fake", "detections": []},
            "observation_frame",
        )
        rgb = pattern_frame(frame_id)
        change_mask = np.zeros((VisionSettings.clip_size, VisionSettings.clip_size), dtype=np.float32)
        output = FrameOutput(observation=observation, change_mask=change_mask, width=rgb.shape[1], height=rgb.shape[0])
        self.status.update(output, rgb)
        return True


def pattern_frame(frame_id: int, width: int = 320, height: int = 240) -> np.ndarray:
    """A gray RGB frame with a white bar that moves one step per frame, so a frozen stream is easy to spot."""
    rgb = np.full((height, width, 3), 64, dtype=np.uint8)
    bar_x = (frame_id * 8) % width
    rgb[:, bar_x : bar_x + 8] = 255
    return rgb


def run_fake_frames(fake: FakeVisionAPI, interval_s: float, stop: threading.Event) -> None:
    """Push a test-pattern frame every interval_s until stop is set (the fake's frame loop, in its own thread)."""
    while not stop.is_set():
        fake.push_frame()
        time.sleep(interval_s)


def main() -> None:
    """CLI: serve FakeVisionAPI (--fake). The real API is served by the frame loop in kitchen.vision.run."""
    parser = argparse.ArgumentParser(description="Serve the Vision HTTP API.")
    parser.add_argument("--fake", action="store_true", help="serve FakeVisionAPI: a test pattern, no camera")
    parser.add_argument("--port", type=int, help="port (default: VISION_PORT)")
    parser.add_argument("--plates", default="plate-1", help="comma-separated plates at the plating spot")
    parser.add_argument("--fps", type=float, default=10.0, help="test-pattern frames per second")
    args = parser.parse_args()
    if not args.fake:
        parser.error("only --fake is served from here; the real API runs with the frame loop (kitchen.vision.run)")

    import uvicorn

    settings = load_settings(VisionSettings, "VISION_")
    plates = [plate.strip() for plate in args.plates.split(",") if plate.strip()]
    fake = FakeVisionAPI(plates, settings.detector, parse_cors_origins(settings.cors_origins))

    stop = threading.Event()
    threading.Thread(target=run_fake_frames, args=(fake, 1.0 / args.fps, stop), daemon=True).start()
    try:
        uvicorn.run(fake.app, host="0.0.0.0", port=args.port or settings.port)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
