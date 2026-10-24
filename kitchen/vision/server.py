"""The Pi's HTTP API (POST /plates/start, GET /stream.mjpg, GET /health) and FakeVisionAPI for outputs.

Owner: vision (agents)
Step:  vision
Reads: the latest FrameOutput and RGB frame (VisionStatus, updated by the frame loop), a start_plate callable
       (zones.py binding), VISION_PORT, VISION_DETECTOR and VISION_CORS_ORIGINS (CLI only)
Emits: the Vision HTTP API (contracts/apis.md), with the standard error body and CORS for VISION_CORS_ORIGINS only
Rules: fail closed: /health last_frame_t_ms stops advancing when frames stop, so the gate can go to fault;
       /plates/start never guesses, every refusal is a 409 with the contract's code;
       endpoints never block the frame loop: update() only swaps references, JPEGs are encoded in the HTTP thread

    uv run python -m kitchen.vision.server --fake --port 8002     # FakeVisionAPI: test pattern, no camera or models
    curl localhost:8002/health                                     # {"fps": ..., "detector": ..., "last_frame_t_ms": ...}
    curl -o - localhost:8002/stream.mjpg | head -c 2000            # multipart MJPEG parts
    curl -X POST localhost:8002/plates/start -H 'Content-Type: application/json' -d '{"ticket_id": "t7"}'
"""

import argparse
import threading
import time
from collections.abc import Callable, Iterator

import numpy as np
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from kitchen.shared.clock import SystemClock
from kitchen.shared.config import load_settings
from kitchen.shared.interfaces import Clock, StateUnavailable
from kitchen.shared.models import validate
from kitchen.vision.common import BindRefused, FrameOutput, VisionSettings

MJPEG_BOUNDARY = "frame"

# How much each new frame-to-frame interval moves the fps estimate (an exponential moving average).
FPS_SMOOTHING = 0.1

# A stream with no new frame for this long ends, so a client of a stalled camera sees the stream stop instead of a
# frozen picture that looks live, and a disconnected client's thread is freed.
STREAM_IDLE_S = 5.0


class VisionStatus:
    """The latest frame and health, shared between the frame loop (one writer) and the HTTP handlers (readers).

    update() only swaps references under a short lock, so a slow HTTP client can never hold up the frame loop. The
    RGB array is stored by reference: the frame loop must hand over a fresh array each frame and never write into
    one it has already passed here.
    """

    def __init__(self, detector: str):
        self.detector = detector
        self._new_frame = threading.Condition()
        self._observation = None
        self._rgb = None
        self._frame_number = 0  # counts update() calls; the stream uses it, since frame_id may restart with a source
        self._fps = 0.0

        # Encoding has its own lock so the frame loop never waits on it, and so each frame is encoded at most once
        # however many clients are streaming.
        self._encode_lock = threading.Lock()
        self._jpeg_frame_number = 0
        self._jpeg = b""

    def update(self, output: FrameOutput, rgb: np.ndarray) -> None:
        """Record one frame from the frame loop: its observation, its RGB pixels and the new fps estimate."""
        observation = output.observation
        with self._new_frame:
            if self._observation is not None:
                interval_ms = observation["t_ms"] - self._observation["t_ms"]
                self._fps = smoothed_fps(self._fps, interval_ms)
            self._observation = observation
            self._rgb = rgb
            self._frame_number += 1
            self._new_frame.notify_all()

    def latest_observation(self) -> dict | None:
        """The most recent ObservationFrame, or None before the first frame."""
        with self._new_frame:
            return self._observation

    def health(self) -> dict:
        """The /health body: exactly fps, detector and last_frame_t_ms (contracts/apis.md).

        last_frame_t_ms is the newest frame's own t_ms, never the time now, so it stops advancing the moment frames
        stop and the gate's staleness check can fire (safety rule: fail closed). None before the first frame.
        """
        with self._new_frame:
            last_frame_t_ms = None if self._observation is None else self._observation["t_ms"]
            return {"fps": round(self._fps, 1), "detector": self.detector, "last_frame_t_ms": last_frame_t_ms}

    def next_jpeg(self, after: int, timeout_s: float) -> tuple[int, bytes] | None:
        """Wait for a frame newer than frame number `after` and return (its frame number, its JPEG bytes).

        Returns None if no newer frame arrives within timeout_s. Pass 0 for the first call and the returned frame
        number after that. A client that falls behind skips straight to the latest frame.
        """
        with self._new_frame:
            has_newer = self._new_frame.wait_for(lambda: self._frame_number > after, timeout=timeout_s)
            if not has_newer:
                return None
            frame_number = self._frame_number
            rgb = self._rgb

        with self._encode_lock:
            if self._jpeg_frame_number != frame_number:
                self._jpeg = encode_jpeg(rgb)
                self._jpeg_frame_number = frame_number
            return frame_number, self._jpeg


def smoothed_fps(fps: float, interval_ms: int) -> float:
    """Move the fps estimate toward 1000 / interval_ms. A non-positive interval says nothing about the rate."""
    if interval_ms <= 0:
        return fps
    instant_fps = 1000.0 / interval_ms
    if fps == 0.0:
        return instant_fps
    return (1 - FPS_SMOOTHING) * fps + FPS_SMOOTHING * instant_fps


def encode_jpeg(rgb: np.ndarray) -> bytes:
    """JPEG bytes of an RGB uint8 frame."""
    import cv2  # the ml extra; imported here so the API module loads without it until a stream is asked for

    ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError(f"could not encode a {rgb.shape} frame as JPEG")
    return jpeg.tobytes()


def mjpeg_parts(status: VisionStatus, idle_s: float) -> Iterator[bytes]:
    """One multipart MJPEG part per new frame, ending once no new frame has come for idle_s."""
    frame_number = 0
    while True:
        frame = status.next_jpeg(frame_number, idle_s)
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
    """Body of POST /plates/start."""

    ticket_id: str = Field(min_length=1)


def create_app(
    status: VisionStatus,
    start_plate: Callable[[str], str],
    cors_origins: list[str],
    stream_idle_s: float = STREAM_IDLE_S,
) -> FastAPI:
    """The Vision HTTP API over a VisionStatus and a start_plate(ticket_id) -> plate_id binding function.

    start_plate raises BindRefused (409 with its code) or StateUnavailable (503); the API never picks a plate
    itself. CORS is sent only for cors_origins.
    """
    if "*" in cors_origins:
        # contracts/apis.md HTTP conventions: browsers may call this API only from the listed screens.
        raise ValueError("CORS origins must be listed; a wildcard is not allowed")

    app = FastAPI(title="Vision API")
    app.add_middleware(
        CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"]
    )

    @app.exception_handler(BindRefused)
    def bind_refused(request, exc):
        return error_response(409, exc.code, str(exc))

    @app.exception_handler(StateUnavailable)
    def state_unavailable(request, exc):
        return error_response(503, "state_unavailable", str(exc))

    @app.exception_handler(RequestValidationError)
    def bad_request(request, exc):
        return error_response(400, "bad_request", str(exc.errors()))

    @app.post("/plates/start")
    def plates_start(body: StartPlateRequest):
        return {"plate_id": start_plate(body.ticket_id)}

    @app.get("/health")
    def health():
        return status.health()

    @app.get("/stream.mjpg")
    def stream():
        media_type = f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}"
        return StreamingResponse(mjpeg_parts(status, stream_idle_s), media_type=media_type)

    return app


# ---------- FakeVisionAPI: the same API with a test pattern and an in-memory binding, for outputs ----------


class FakeSceneRequest(BaseModel):
    """Body of the fake-only POST /fake/scene: what to change; a field left out stays as it is."""

    plates_at_spot: list[str] | None = None
    frames_running: bool | None = None


class FakeVisionAPI:
    """The Vision HTTP API without a camera or models, so the web app can be built against it.

    `app` serves the three contract endpoints. Frames are a moving test pattern pushed by push_frame() (the CLI
    calls it on a timer). /plates/start follows the real refusal rules against `plates_at_spot` and an in-memory
    binding. The fake-only POST /fake/scene sets the plates at the spot and stops or restarts frames, so outputs
    can exercise every 409 and the gate's stale-health fault.
    """

    def __init__(
        self,
        plates_at_spot: list[str] | None = None,
        detector: str = "onnx",
        cors_origins: list[str] | None = None,
        clock: Clock | None = None,
    ):
        self.plates_at_spot = ["plate-1"] if plates_at_spot is None else list(plates_at_spot)
        self.bindings: dict[str, str] = {}  # plate_id -> ticket_id
        self.frames_running = True
        self.status = VisionStatus(detector)
        self.clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._frame_id = 0

        origins = [VisionSettings.cors_origins] if cors_origins is None else cors_origins
        self.app = create_app(self.status, self.start_plate, origins)

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
