"""Frame sources: a video file, the laptop webcam or the Pi camera, each yielding RGB frames stamped with t_ms.
Replaying a recording is record.ReplaySource.

Owner: vision (agents)
Step:  vision
Reads: video files in data/, the webcam (OpenCV), the Pi camera (picamera2, on the Pi only), Clock
Emits: SourceFrame (frame_id from 0, t_ms, RGB uint8 [H, W, 3]) for detect.py
Rules: time runs forward: t_ms is strictly increasing, even across source restarts (after_t_ms); a source that can't
       open raises OSError, and a stream that ends just stops: no frame is ever invented

Timestamps:
- VideoSource uses the file's own timestamps (CAP_PROP_POS_MSEC) offset from the clock time when playback starts, so
  replaying a file is deterministic for a given clock.
- WebcamSource and PiCameraSource stamp each frame with clock.now_ms() when it arrives.
- Either way a timestamp that isn't after the previous one is bumped to previous + 1 (forward_t_ms).

Restarting a source: pass the old source's last_t_ms as after_t_ms, and the new one starts after it.
"""

import math
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np

from kitchen.shared.interfaces import Clock
from kitchen.vision.common import SourceFrame


def forward_t_ms(t_ms: int, last_t_ms: int) -> int:
    """Return `t_ms`, or `last_t_ms + 1` if it isn't after `last_t_ms`.

    Time runs forward (safety rule 5): a repeated or backwards timestamp would let a contamination path run out of
    order, so it is moved just past the last one instead of being repeated or reversed.
    """
    return max(t_ms, last_t_ms + 1)


def to_rgb(bgr: np.ndarray) -> np.ndarray:
    """Convert an OpenCV BGR frame to the RGB uint8 [H, W, 3] every SourceFrame carries."""
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def file_ms(capture: cv2.VideoCapture) -> int:
    """The timestamp of the frame just read, in whole ms from the file's start; 0 when the file gives none.

    A missing timestamp (NaN or negative) becomes 0 so forward_t_ms moves the frame just past the previous one.
    """
    position = capture.get(cv2.CAP_PROP_POS_MSEC)
    if not math.isfinite(position) or position < 0:
        return 0
    return round(position)


class VideoSource:
    """Frames from a video file, with t_ms from the file's timestamps, starting at the clock time playback begins."""

    def __init__(self, path: str | Path, clock: Clock, after_t_ms: int = -1):
        self.path = Path(path)
        self.clock = clock
        # The last t_ms handed out; a restarted source passes the previous source's value here.
        self.last_t_ms = after_t_ms

        if not self.path.is_file():
            raise OSError(f"video file not found: {self.path}")
        self.capture = cv2.VideoCapture(str(self.path))
        if not self.capture.isOpened():
            raise OSError(f"OpenCV can't open the video file: {self.path}")

    def __iter__(self) -> Iterator[SourceFrame]:
        """Yield every frame in the file once, then stop."""
        # Playback starts now, or just after the last t_ms if a restart's clock hasn't caught up, so the file's frame
        # spacing is kept instead of every frame being bumped by 1 ms.
        start_ms = max(self.clock.now_ms(), self.last_t_ms + 1)
        frame_id = 0
        try:
            while True:
                ok, bgr = self.capture.read()
                if not ok:
                    return
                t_ms = forward_t_ms(start_ms + file_ms(self.capture), self.last_t_ms)
                self.last_t_ms = t_ms
                yield SourceFrame(frame_id=frame_id, t_ms=t_ms, rgb=to_rgb(bgr))
                frame_id += 1
        finally:
            self.capture.release()


class WebcamSource:
    """Frames from a webcam by OpenCV device index, each stamped with clock.now_ms() as it arrives."""

    def __init__(self, index: int, clock: Clock, after_t_ms: int = -1):
        self.index = index
        self.clock = clock
        self.last_t_ms = after_t_ms

        self.capture = cv2.VideoCapture(index)
        if not self.capture.isOpened():
            raise OSError(f"OpenCV can't open webcam {index}")

    def __iter__(self) -> Iterator[SourceFrame]:
        """Yield frames until the camera stops delivering them, then stop."""
        frame_id = 0
        try:
            while True:
                ok, bgr = self.capture.read()
                if not ok:
                    return
                t_ms = forward_t_ms(self.clock.now_ms(), self.last_t_ms)
                self.last_t_ms = t_ms
                yield SourceFrame(frame_id=frame_id, t_ms=t_ms, rgb=to_rgb(bgr))
                frame_id += 1
        finally:
            self.capture.release()


# The Pi camera's frame size: 16:9, the Camera Module 3's whole field of view (binned on the sensor). A full-size still
# from rpicam-still (4608 x 2592) is 16:9 over the same view, so zones calibrated on it line up with these frames.
PI_CAMERA_SIZE = (1280, 720)

# libcamera's AfModeEnum.Continuous. picamera2 starts the Camera Module 3 in manual focus; continuous keeps the counter
# sharp without importing libcamera here.
CONTINUOUS_AUTOFOCUS = 2


class PiCameraSource:
    """Frames from the Raspberry Pi camera through picamera2, each stamped with clock.now_ms() as it arrives.

    OpenCV can't open the Pi 5's camera, which goes through libcamera, so this source uses picamera2. It exists only for
    the Pi's system Python (pi_setup.sh makes the venv see it), so it's imported here, not at the top of the module.
    A capture that fails raises and closes the camera: frames stop, so /health goes stale and the gate faults.
    """

    def __init__(self, clock: Clock, after_t_ms: int = -1, size: tuple[int, int] = PI_CAMERA_SIZE):
        self.clock = clock
        self.last_t_ms = after_t_ms

        try:
            from picamera2 import Picamera2
        except ImportError as error:
            raise OSError("picamera2 isn't available: the Pi camera only works on the Pi (see pi_setup.sh)") from error
        try:
            self.camera = Picamera2()
            # picamera2's "RGB888" is laid out B, G, R in memory, like an OpenCV frame, so to_rgb converts it.
            self.camera.configure(self.camera.create_video_configuration(main={"size": size, "format": "RGB888"}))
            if "AfMode" in self.camera.camera_controls:
                self.camera.set_controls({"AfMode": CONTINUOUS_AUTOFOCUS})
            self.camera.start()
        except Exception as error:
            raise OSError(f"can't open the Pi camera: {error}") from error

    def __iter__(self) -> Iterator[SourceFrame]:
        """Yield frames until a capture fails (which raises), then stop and close the camera."""
        frame_id = 0
        try:
            while True:
                bgr = self.camera.capture_array()
                t_ms = forward_t_ms(self.clock.now_ms(), self.last_t_ms)
                self.last_t_ms = t_ms
                yield SourceFrame(frame_id=frame_id, t_ms=t_ms, rgb=to_rgb(bgr))
                frame_id += 1
        finally:
            self.camera.stop()
            self.camera.close()
