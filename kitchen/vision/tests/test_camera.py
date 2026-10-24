"""Tests for the frame sources: RGB frames, frame ids from 0, and strictly increasing t_ms, also across restarts.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.camera, a tiny MJPG video written by OpenCV, a stand-in picamera2 module, FakeClock
Emits: nothing
Rules: time runs forward (strictly increasing t_ms, even across source restarts); a source that can't open fails
"""

import sys
import types

import cv2
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from kitchen.shared.fakes import FakeClock
from kitchen.vision import camera
from kitchen.vision.camera import PI_CAMERA_SIZE, PiCameraSource, VideoSource, WebcamSource, forward_t_ms

FPS = 25
FRAME_COUNT = 6


@pytest.fixture
def video_path(tmp_path):
    """A 6-frame 32x24 MJPG .avi at 25 FPS whose frames are pure blue, with brightness rising per frame."""
    path = tmp_path / "tiny.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (32, 24))
    for i in range(FRAME_COUNT):
        bgr = np.zeros((24, 32, 3), dtype=np.uint8)
        bgr[:, :, 0] = 40 * i  # OpenCV is BGR, so channel 0 is blue
        writer.write(bgr)
    writer.release()
    return path


def assert_strictly_increasing(t_values):
    assert all(later > earlier for earlier, later in zip(t_values, t_values[1:], strict=False))


@pytest.mark.safety
def test_video_t_ms_strictly_increasing_from_file_timestamps(video_path):
    frames = list(VideoSource(video_path, FakeClock(start_ms=500)))

    assert [frame.frame_id for frame in frames] == list(range(FRAME_COUNT))
    assert [frame.t_ms for frame in frames] == [500 + 40 * i for i in range(FRAME_COUNT)]
    assert_strictly_increasing([frame.t_ms for frame in frames])


@pytest.mark.safety
def test_video_restart_continues_after_last_t_ms(video_path):
    clock = FakeClock()
    first = VideoSource(video_path, clock)
    first_frames = list(first)

    # The clock hasn't moved, so a naive restart would repeat t_ms 0..200.
    second_frames = list(VideoSource(video_path, clock, after_t_ms=first.last_t_ms))

    all_t = [frame.t_ms for frame in first_frames + second_frames]
    assert_strictly_increasing(all_t)
    assert second_frames[0].t_ms == first.last_t_ms + 1
    assert second_frames[0].frame_id == 0
    # The file's frame spacing is kept after the restart.
    assert second_frames[1].t_ms - second_frames[0].t_ms == 40


def test_video_replay_is_deterministic_for_a_given_clock(video_path):
    run_a = [(frame.t_ms, frame.rgb.tobytes()) for frame in VideoSource(video_path, FakeClock(start_ms=7))]
    run_b = [(frame.t_ms, frame.rgb.tobytes()) for frame in VideoSource(video_path, FakeClock(start_ms=7))]
    assert run_a == run_b


def test_video_frames_are_rgb_uint8(video_path):
    frame = list(VideoSource(video_path, FakeClock()))[-1]
    assert frame.rgb.dtype == np.uint8
    assert frame.rgb.shape == (24, 32, 3)
    red, green, blue = frame.rgb[12, 16]
    assert blue > 150 and red < 30 and green < 30


def test_missing_video_raises_clear_error(tmp_path):
    with pytest.raises(OSError, match="not found"):
        VideoSource(tmp_path / "missing.avi", FakeClock())


def test_unreadable_video_raises_clear_error(tmp_path):
    path = tmp_path / "junk.avi"
    path.write_bytes(b"not a video")
    with pytest.raises(OSError, match="can't open"):
        VideoSource(path, FakeClock())


class FakeCapture:
    """Stands in for cv2.VideoCapture on a webcam: delivers `count` frames, then reports the stream ended."""

    def __init__(self, index, count=4, opened=True):
        self.count = count
        self.opened = opened

    def isOpened(self):  # noqa: N802 (OpenCV's name)
        return self.opened

    def read(self):
        if self.count == 0:
            return False, None
        self.count -= 1
        return True, np.zeros((4, 6, 3), dtype=np.uint8)

    def release(self):
        pass


@pytest.mark.safety
def test_webcam_t_ms_strictly_increasing_when_clock_stalls(monkeypatch):
    monkeypatch.setattr(camera.cv2, "VideoCapture", FakeCapture)
    clock = FakeClock(start_ms=100)  # never advanced: every frame reads the same clock time

    first = WebcamSource(0, clock)
    first_frames = list(first)
    second_frames = list(WebcamSource(0, clock, after_t_ms=first.last_t_ms))

    assert [frame.t_ms for frame in first_frames] == [100, 101, 102, 103]
    assert [frame.frame_id for frame in second_frames] == [0, 1, 2, 3]
    assert_strictly_increasing([frame.t_ms for frame in first_frames + second_frames])


class SteppingCapture(FakeCapture):
    """A FakeCapture whose every read() moves the clock on first, by the next step, like time passing between frames."""

    def __init__(self, clock, steps_ms):
        super().__init__(0, count=len(steps_ms))
        self.clock = clock
        self.steps_ms = list(steps_ms)

    def read(self):
        if self.steps_ms:
            self.clock.advance(self.steps_ms.pop(0))
        return super().read()


@pytest.mark.safety
def test_webcam_stamps_each_frame_with_the_clock_as_it_arrives(monkeypatch):
    clock = FakeClock(start_ms=100)
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda index: SteppingCapture(clock, [33, 34, 0, 5]))

    frames = list(WebcamSource(0, clock))

    # Clock readings 133, 167, 167, 172: each frame keeps its own reading, only the stalled one moves to last + 1.
    assert [frame.t_ms for frame in frames] == [133, 167, 168, 172]


def test_webcam_that_cant_open_raises(monkeypatch):
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda index: FakeCapture(index, opened=False))
    with pytest.raises(OSError, match="webcam 3"):
        WebcamSource(3, FakeClock())


@pytest.mark.safety
@given(st.lists(st.integers(min_value=0, max_value=10_000), max_size=50), st.integers(min_value=-1, max_value=10_000))
def test_forward_t_ms_never_repeats_or_reverses(raw_t_values, after_t_ms):
    last = after_t_ms
    stamped = []
    for raw in raw_t_values:
        last = forward_t_ms(raw, last)
        stamped.append(last)

    assert_strictly_increasing([after_t_ms, *stamped])

    # A timestamp already in order is kept as is; any other is moved just past the previous one.
    previous_values = [after_t_ms, *stamped][: len(stamped)]
    for t, raw, previous in zip(stamped, raw_t_values, previous_values, strict=True):
        assert t == raw if raw > previous else t == previous + 1


class FakePicamera2:
    """Stands in for picamera2.Picamera2: `frames` B, G, R frames, then a failed capture. Records what it was asked."""

    def __init__(self, clock=None, steps_ms=(), frames=3, fail_to_open=False):
        if fail_to_open:
            raise IndexError("list index out of range")  # what picamera2 raises when no camera is attached
        self.clock = clock
        self.steps_ms = list(steps_ms)
        self.frames = frames
        self.camera_controls = {"AfMode": (0, 2, 0)}
        self.config = None
        self.controls = {}
        self.started = self.closed = False

    def create_video_configuration(self, main):
        return {"main": main}

    def configure(self, config):
        self.config = config

    def set_controls(self, controls):
        self.controls.update(controls)

    def start(self):
        self.started = True

    def capture_array(self):
        if self.frames == 0:
            raise RuntimeError("camera frontend has timed out")
        self.frames -= 1
        if self.steps_ms:
            self.clock.advance(self.steps_ms.pop(0))
        bgr = np.zeros((4, 6, 3), dtype=np.uint8)
        bgr[..., 2] = 200  # red, in B, G, R order
        return bgr

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def fake_picamera2(monkeypatch, **camera_args):
    """Install a stand-in picamera2 module; returns a list that collects every camera it makes."""
    made = []

    def make():
        made.append(FakePicamera2(**camera_args))
        return made[-1]

    monkeypatch.setitem(sys.modules, "picamera2", types.SimpleNamespace(Picamera2=make))
    return made


@pytest.mark.safety
def test_pi_camera_stamps_frames_as_they_arrive_and_never_repeats_a_t_ms(monkeypatch):
    clock = FakeClock(start_ms=100)
    fake_picamera2(monkeypatch, clock=clock, steps_ms=[33, 0, 34])

    source = PiCameraSource(clock, after_t_ms=120)
    frames = []
    with pytest.raises(RuntimeError, match="timed out"):  # the fourth capture fails, and the stream stops
        for frame in source:
            frames.append(frame)

    assert [frame.frame_id for frame in frames] == [0, 1, 2]
    assert [frame.t_ms for frame in frames] == [133, 134, 167]  # the stalled reading moves to last + 1
    assert_strictly_increasing([120] + [frame.t_ms for frame in frames])


def test_pi_camera_frames_are_rgb_and_the_camera_is_set_up_and_closed(monkeypatch):
    made = fake_picamera2(monkeypatch, frames=1)

    source = PiCameraSource(FakeClock())
    frames = []
    with pytest.raises(RuntimeError):
        for frame in source:
            frames.append(frame)

    (camera_made,) = made
    assert camera_made.config == {"main": {"size": PI_CAMERA_SIZE, "format": "RGB888"}}
    assert camera_made.controls == {"AfMode": 2}  # continuous autofocus
    assert frames[0].rgb.dtype == np.uint8
    assert frames[0].rgb[0, 0].tolist() == [200, 0, 0]  # B, G, R from the camera, R, G, B out
    assert camera_made.closed and not camera_made.started


def test_pi_camera_without_picamera2_raises_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "picamera2", None)  # a None entry makes the import raise ImportError
    with pytest.raises(OSError, match="picamera2 isn't available"):
        PiCameraSource(FakeClock())


def test_pi_camera_that_isnt_attached_raises(monkeypatch):
    fake_picamera2(monkeypatch, fail_to_open=True)
    with pytest.raises(OSError, match="can't open the Pi camera"):
        PiCameraSource(FakeClock())
