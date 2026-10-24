"""Main loop and CLI: source -> detect -> track -> zones -> ObservationFrame + change mask -> consumers.

Owner: vision (agents)
Step:  vision
Reads: camera.py, detect.py, tracking.py, zones.py, kitchen/shared/change_mask.py, Clock
Emits: observation_frame and its change mask to EntityStore.ingest (partner), kitchen/interactions and record.py
Rules: the frame loop never blocks on a consumer; time runs forward

FrameLoop.step() runs one frame through the pipeline and hands the FrameOutput to every consumer (the entity store,
the interactions step, a JSONL writer, a recorder) and to the Vision API's status. A detector error stops the loop
(fail closed: frames stop, /health goes stale, the gate faults). A consumer error is logged at ERROR, and the frame
still reaches the other consumers and the status, so one broken consumer can't freeze the camera feed.

    uv run python -m kitchen.vision.run --video data/recordings/prep.mp4 --zones zones.json --out frames.jsonl
    uv run python -m kitchen.vision.run --webcam 0 --zones zones.json --serve       # live, with the Vision HTTP API
    .venv/bin/python -m kitchen.vision.run --picamera --zones zones.json --serve    # on the Pi, from its camera

In-process (the local runner's live mode): build a FrameLoop with build_frame_loop() and pass consumers such as
entity_store_consumer(store); the loop needs nothing else from this module.
"""

import argparse
import hashlib
import json
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from pathlib import Path

import numpy as np

from kitchen.shared.change_mask import change_mask
from kitchen.shared.clips import LETTERBOX_PAD, letterbox_layout
from kitchen.shared.clock import FakeClock, SystemClock
from kitchen.shared.config import load_settings, log_settings
from kitchen.vision.common import FrameOutput, SourceFrame, VisionSettings, ZoneLookup

logger = logging.getLogger(__name__)

Consumer = Callable[[FrameOutput], None]


class FrameLoop:
    """One frame at a time through detection, tracking and zones, to an ObservationFrame and its change mask.

    `status` is the Vision API's VisionStatus (or None): it gets every output with its RGB frame, for /health and
    /stream.mjpg.
    """

    def __init__(
        self,
        settings: VisionSettings,
        detector,
        tracker,
        zone_of: ZoneLookup,
        consumers: list[Consumer],
        status=None,
    ):
        self.settings = settings
        self.detector = detector
        self.tracker = tracker
        self.zone_of = zone_of
        self.consumers = consumers
        self.status = status
        self._previous_letterboxed: np.ndarray | None = None
        self._previous_mask: np.ndarray | None = None
        self._previous_t_ms: int | None = None
        self._fps: float | None = None

    def step(self, frame: SourceFrame) -> FrameOutput:
        """Run one frame and hand its output to every consumer and the status. Returns the output.

        Raises on a frame that doesn't come after the last one (time runs forward) and on any detector error, before
        anything is handed on: no partial or empty frame is ever emitted in place of a real one.
        """
        if self._previous_t_ms is not None and frame.t_ms <= self._previous_t_ms:
            raise ValueError(f"frame t_ms {frame.t_ms} does not follow {self._previous_t_ms} (time runs forward)")
        height, width = frame.rgb.shape[:2]
        objects, people = self.detector.detect(frame)
        detections = self.tracker.update(objects, people, frame.t_ms, self.zone_of, width, height)

        observation = {
            "frame_id": frame.frame_id,
            "t_ms": frame.t_ms,
            "camera_id": self.settings.camera_id,
            "detections": detections,
        }
        fps = self._update_fps(frame.t_ms)
        if fps is not None:
            observation["fps_estimate"] = round(fps, 2)
        output = FrameOutput(observation, self._change_mask(frame.rgb), width, height)

        for consumer in self.consumers:
            try:
                consumer(output)
            except Exception:  # one failing consumer must not starve the others or stop the camera
                logger.exception("frame %s: consumer %r failed", frame.frame_id, consumer)
        if self.status is not None:
            self.status.update(output, frame.rgb)
        return output

    def run(self, source: Iterable[SourceFrame], stop: threading.Event | None = None) -> int:
        """Run every frame from the source until it ends or `stop` is set. Returns how many frames ran."""
        count = 0
        for frame in source:
            if stop is not None and stop.is_set():
                break
            self.step(frame)
            count += 1
        return count

    def _change_mask(self, rgb: np.ndarray) -> np.ndarray:
        """The shared change mask on the frame letterboxed to clip_size, as kitchen/shared/clips.py builds clips.

        The first frame has nothing to compare with, so its mask is all zeros (no movement seen yet).
        """
        letterboxed = letterbox_for_change_mask(rgb, self.settings.clip_size)
        if self._previous_letterboxed is None:
            mask = np.zeros(letterboxed.shape[:2], dtype=np.float32)
        else:
            mask = change_mask(
                self._previous_letterboxed,
                letterboxed,
                self._previous_mask,
                gain=self.settings.change_gain,
                blur_sigma_px=self.settings.change_blur_sigma_px,
                decay=self.settings.change_decay,
            ).astype(np.float32)
        self._previous_letterboxed, self._previous_mask = letterboxed, mask
        return mask

    def _update_fps(self, t_ms: int) -> float | None:
        """Frames per second, smoothed over recent frames, measured on the frames' own t_ms."""
        if self._previous_t_ms is not None:
            instant = 1000.0 / max(1, t_ms - self._previous_t_ms)
            self._fps = instant if self._fps is None else 0.9 * self._fps + 0.1 * instant
        self._previous_t_ms = t_ms
        return self._fps


def letterbox_for_change_mask(rgb: np.ndarray, size: int) -> np.ndarray:
    """The frame shrunk into a size x size square by area averaging, with the short side padded, in exactly the layout
    of clips.py's letterbox_frame (letterbox_layout, LETTERBOX_PAD), so the mask lines up with letterboxed clip boxes.

    OpenCV's area filter gives the same pixels as letterbox_frame when the frame shrinks by a whole factor (1280 x 720
    or 1920 x 1080 to 128) at about a tenth of the time, which the 30 FPS frame loop needs.
    """
    import cv2

    height, width = rgb.shape[:2]
    content_width, content_height, left, top = letterbox_layout(width, height, size)
    square = np.full((size, size, 3), LETTERBOX_PAD, dtype=np.uint8)
    square[top : top + content_height, left : left + content_width] = cv2.resize(
        rgb, (content_width, content_height), interpolation=cv2.INTER_AREA
    )
    return square


def build_frame_loop(
    settings: VisionSettings, zones: list, consumers: list[Consumer], detector=None, status=None
) -> FrameLoop:
    """A FrameLoop with this repo's detector, tracker and zone lookup. Opens the detector unless one is given."""
    from kitchen.vision.detect import open_detector
    from kitchen.vision.tracking import Tracker
    from kitchen.vision.zones import zone_lookup

    detector = detector or open_detector(settings)
    return FrameLoop(settings, detector, Tracker(settings), zone_lookup(zones), consumers, status)


def entity_store_consumer(store) -> Consumer:
    """A consumer that feeds every ObservationFrame to an EntityStore (contracts/apis.md: vision calls ingest())."""

    def ingest(output: FrameOutput) -> None:
        store.ingest(output.observation)

    return ingest


class JsonlWriter:
    """A consumer that writes each ObservationFrame as one canonical JSON line, so two runs can be diffed."""

    def __init__(self, path: Path):
        self._handle = open(path, "w")  # noqa: SIM115 (closed by close())

    def __call__(self, output: FrameOutput) -> None:
        self._handle.write(json.dumps(output.observation, sort_keys=True, separators=(",", ":")) + "\n")

    def close(self) -> None:
        """Flush and close the file."""
        self._handle.close()


def as_live(source: Iterable[SourceFrame], clock) -> Iterator[SourceFrame]:
    """Play a video like a live camera: each frame waits until its place in the video is due, then is stamped with
    the clock when it is processed, as a webcam frame is.

    Serving needs this. "Start plate" only binds from a fresh frame (latest t_ms close to the clock), and a video's
    own timestamps drift from the clock as soon as processing is slower or faster than the video. Offline runs keep
    the video's timestamps instead, so two runs on one file give identical output.
    """
    from kitchen.vision.camera import forward_t_ms

    start_ms = first_video_ms = None
    last_t_ms = -1
    for frame in source:
        if start_ms is None:
            start_ms, first_video_ms = clock.now_ms(), frame.t_ms
        wait_ms = start_ms + (frame.t_ms - first_video_ms) - clock.now_ms()
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        last_t_ms = forward_t_ms(clock.now_ms(), last_t_ms)
        yield replace(frame, t_ms=last_t_ms)


def file_sha256(path: Path) -> str:
    """Hex sha256 of a file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close_all(closers: list[Callable[[], None]]) -> None:
    """Run each closer once, emptying the list, so a later call (from `finally`) closes nothing twice."""
    while closers:
        closers.pop(0)()


def wait_until_stopped() -> None:
    """Block until Ctrl-C or SIGTERM, which main() turns into KeyboardInterrupt."""
    threading.Event().wait()


def serve_in_background(app, port: int):
    """Serve a FastAPI app with uvicorn on a daemon thread. Returns the uvicorn Server, so the caller can stop it."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def source_name(args) -> str:
    """The frame source as the recording manifest names it: the video's path, webcam:<index> or picamera."""
    if args.video:
        return str(args.video)
    if args.picamera:
        return "picamera"
    return f"webcam:{args.webcam}"


def main(argv: list[str] | None = None) -> int:
    """CLI; see the module docstring. Exits 1 if the frame loop stops on an error."""
    parser = argparse.ArgumentParser(description="Run vision: a video, webcam or the Pi camera to ObservationFrames.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--video", type=Path, help="a video file")
    source_group.add_argument("--webcam", type=int, help="a webcam index, e.g. 0")
    source_group.add_argument("--picamera", action="store_true", help="the Raspberry Pi camera (picamera2, on the Pi)")
    parser.add_argument("--zones", type=Path, help="zones.json (see kitchen/vision/zones.py)")
    parser.add_argument("--out", type=Path, help="write every ObservationFrame to this JSONL file")
    parser.add_argument(
        "--record", type=Path, help="record frames.jsonl, change.npz and manifest.json to this new folder"
    )
    parser.add_argument("--serve", action="store_true", help="serve the Vision HTTP API (port from VISION_PORT)")
    parser.add_argument("--live", action="store_true", help="play a video like a live camera (on with --serve)")
    parser.add_argument("--max-frames", type=int, help="stop after this many frames")
    args = parser.parse_args(argv)

    from kitchen.shared.fakes import FakeStateClient
    from kitchen.vision.camera import PiCameraSource, VideoSource, WebcamSource
    from kitchen.vision.plates import start_plate
    from kitchen.vision.record import Recorder, change_mask_params
    from kitchen.vision.server import VisionStatus, create_app, parse_cors_origins
    from kitchen.vision.zones import load_zones, push_zones

    settings = load_settings(VisionSettings, "VISION_")
    log_settings(settings)
    zones = load_zones(args.zones) if args.zones else []
    if not zones:
        logger.warning(
            "no zones file: every detection's zone is null, so there are no bins, plating spot or clean stack"
        )
    # Standalone, zones and plate bindings live in this process's FakeStateClient. In the local runner's live mode the
    # loop is built in-process instead, on the shift's own StateClient.
    state = FakeStateClient()
    push_zones(state, zones)

    loop = build_frame_loop(settings, zones, consumers=[])
    # The clock starts once the detector has loaded, which can take seconds: load time must not decide which objects
    # are seen by shift_start_ms. An offline video keeps its own time from 0 instead, so one file always gives the same
    # output. Live (a webcam, the Pi camera, --serve or --live), frames are stamped as they are processed.
    live = args.webcam is not None or args.picamera or args.serve or args.live
    clock = SystemClock() if live else FakeClock(0)
    status = VisionStatus(loop.detector.backend, settings.track_lost_ms) if args.serve else None
    loop.status = status
    closers = []
    if args.out:
        writer = JsonlWriter(args.out)
        loop.consumers.append(writer)
        closers.append(writer.close)
    if args.record:
        manifest = {
            "source": source_name(args),
            "zones_sha256": file_sha256(args.zones) if args.zones else None,
            "shift_start_ms": settings.shift_start_ms,
            "detector": loop.detector.backend,
            "bundle_dir": settings.bundle_dir,
            "change_mask_params": change_mask_params(settings),
        }
        recorder = Recorder(args.record, manifest)
        loop.consumers.append(recorder.write)
        closers.append(recorder.close)

    server = None
    if args.serve:

        def bind(ticket_id: str) -> str:
            return start_plate(
                state,
                status.recent_observations(),
                zones,
                ticket_id,
                clock.now_ms(),
                settings.plate_bind_max_age_ms,
                settings.track_lost_ms,
            )

        app = create_app(status, bind, parse_cors_origins(settings.cors_origins))
        server = serve_in_background(app, settings.port)
        logger.info("vision API on http://localhost:%s", settings.port)

    if args.video:
        source = VideoSource(args.video, clock)
        if args.serve or args.live:
            source = as_live(source, clock)
    elif args.picamera:
        source = PiCameraSource(clock)
    else:
        source = WebcamSource(args.webcam, clock)
    if args.max_frames is not None:
        source = (frame for _, frame in zip(range(args.max_frames), source, strict=False))

    exit_code = 0
    # SIGTERM (a service manager stopping the process) stops it like Ctrl-C, so `finally` still closes the files.
    previous_sigterm = signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        count = loop.run(source)
        logger.info("source ended after %d frames", count)
        # The JSONL and the recording are complete as soon as the source ends, even if the API keeps serving.
        close_all(closers)
        if server is not None:
            logger.info("frames stopped; /health is now stale (the gate shows fault). Ctrl-C to quit")
            wait_until_stopped()
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("the frame loop stopped on an error")
        exit_code = 1
    finally:
        close_all(closers)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if server is not None:
            server.should_exit = True
    return exit_code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
