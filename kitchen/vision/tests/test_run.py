"""Tests for the frame loop and the run CLI, on a fake detector and a tiny synthetic video.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.run; FakeEntityStore and FakeClock (platform)
Emits: nothing
Rules: fail closed (a detector error stops the loop, never an empty frame; a consumer error never starves the
       others); time runs forward (strictly increasing t_ms); deterministic output for one input
"""

import json

import cv2
import numpy as np
import pytest

from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import FakeEntityStore
from kitchen.shared.models import validate
from kitchen.vision import run as vision_run
from kitchen.vision.common import Detection, SourceFrame, VisionSettings
from kitchen.vision.run import FrameLoop, as_live, build_frame_loop, entity_store_consumer
from kitchen.vision.server import VisionStatus

SETTINGS = VisionSettings(shift_start_ms=3000)
KNIFE = Detection("knife", (0.4, 0.4, 0.5, 0.5), 0.9)


class FakeDetector:
    """Returns the same detections for every frame, or raises when told to."""

    backend = "onnx"

    def __init__(self, objects=(KNIFE,), error=None):
        self.objects = list(objects)
        self.error = error

    def detect(self, frame):
        if self.error is not None:
            raise self.error
        return list(self.objects), []


def frame(frame_id, t_ms, value=0):
    """A small RGB frame, all one gray level, so the change mask sees motion only when the level changes."""
    return SourceFrame(frame_id, t_ms, np.full((96, 128, 3), value, dtype=np.uint8))


def make_loop(detector=None, consumers=None, status=None):
    return build_frame_loop(SETTINGS, [], consumers or [], detector=detector or FakeDetector(), status=status)


def test_a_frame_becomes_a_valid_observation_frame_with_a_change_mask():
    output = make_loop().step(frame(0, 100))

    validate(output.observation, "observation_frame")
    assert output.observation["detections"][0]["entity_id"] == "tool-knife-1"
    assert (output.width, output.height) == (128, 96)
    assert output.change_mask.shape == (SETTINGS.clip_size, SETTINGS.clip_size)
    assert output.change_mask.dtype == np.float32


def test_the_change_mask_is_zero_first_and_lights_up_when_pixels_change():
    loop = make_loop()
    first = loop.step(frame(0, 100, value=0)).change_mask
    still = loop.step(frame(1, 133, value=0)).change_mask
    moved = loop.step(frame(2, 166, value=200)).change_mask

    assert not first.any()
    assert not still.any()
    assert moved.max() > 0.5


@pytest.mark.safety
def test_time_runs_forward_a_repeated_or_earlier_frame_is_refused():
    loop = make_loop()
    loop.step(frame(0, 100))

    with pytest.raises(ValueError, match="time runs forward"):
        loop.step(frame(1, 100))
    with pytest.raises(ValueError, match="time runs forward"):
        loop.step(frame(2, 50))


@pytest.mark.safety
def test_a_detector_error_stops_the_loop_and_nothing_is_handed_on():
    """Fail closed: no frame reaches a consumer or the status, so /health goes stale and the gate faults."""
    seen = []
    status = VisionStatus("onnx")
    loop = make_loop(FakeDetector(error=RuntimeError("device stuck")), [seen.append], status)

    with pytest.raises(RuntimeError, match="device stuck"):
        loop.run([frame(0, 100), frame(1, 133)])

    assert seen == []
    assert status.health()["last_frame_t_ms"] is None


@pytest.mark.safety
def test_a_failing_consumer_never_starves_the_others_or_the_status():
    seen = []
    status = VisionStatus("onnx")

    def broken(output):
        raise OSError("disk full")

    loop = make_loop(consumers=[broken, seen.append], status=status)
    loop.run([frame(0, 100), frame(1, 133)])

    assert [output.observation["frame_id"] for output in seen] == [0, 1]
    assert status.health()["last_frame_t_ms"] == 133


def test_entity_store_consumer_feeds_every_frame_to_the_store():
    store = FakeEntityStore(FakeClock())
    make_loop(consumers=[entity_store_consumer(store)]).run([frame(0, 100), frame(1, 133)])

    assert [entity["entity_id"] for entity in store.snapshot()] == ["tool-knife-1"]


def test_fps_estimate_comes_from_the_frames_own_times():
    loop = make_loop()
    outputs = [loop.step(frame(index, 100 + index * 50)) for index in range(5)]

    assert "fps_estimate" not in outputs[0].observation
    assert outputs[-1].observation["fps_estimate"] == pytest.approx(20.0)


def test_as_live_restamps_with_the_clock_and_keeps_time_moving_forward():
    clock = FakeClock(start_ms=5000)
    stamped = list(as_live([frame(0, 0), frame(1, 33), frame(2, 66)], clock))

    # The fake clock doesn't move, so every frame is due at once and each is bumped just past the last.
    assert [f.t_ms for f in stamped] == [5000, 5001, 5002]


def write_video(path, frames=12):
    """A tiny MJPG video whose brightness steps every frame, so the change mask has something to see."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (128, 96))
    for index in range(frames):
        writer.write(np.full((96, 128, 3), (index * 20) % 256, dtype=np.uint8))
    writer.release()


@pytest.mark.safety
def test_the_cli_writes_valid_strictly_increasing_frames_and_two_runs_match(tmp_path, monkeypatch):
    """Issue #14: --video and --out write one valid ObservationFrame per frame, t_ms strictly increasing, and the
    same input gives identical JSONL (deterministic under a fixed clock)."""
    video = tmp_path / "prep.avi"
    write_video(video)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())
    monkeypatch.setattr(vision_run, "SystemClock", lambda: FakeClock(start_ms=0))

    outputs = []
    for name in ("first.jsonl", "second.jsonl"):
        assert vision_run.main(["--video", str(video), "--out", str(tmp_path / name)]) == 0
        outputs.append((tmp_path / name).read_text())

    lines = [json.loads(line) for line in outputs[0].splitlines()]
    assert len(lines) == 12
    for line in lines:
        validate(line, "observation_frame")
    assert all(b["t_ms"] > a["t_ms"] for a, b in zip(lines, lines[1:], strict=False))
    assert outputs[0] == outputs[1]


def test_the_cli_records_a_replayable_recording(tmp_path, monkeypatch):
    from kitchen.vision.record import read_recording

    video = tmp_path / "prep.avi"
    write_video(video, frames=5)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())
    monkeypatch.setattr(vision_run, "SystemClock", lambda: FakeClock(start_ms=0))

    assert vision_run.main(["--video", str(video), "--record", str(tmp_path / "rec")]) == 0

    replayed = list(read_recording(tmp_path / "rec"))
    assert [output.observation["frame_id"] for output in replayed] == [0, 1, 2, 3, 4]
    assert replayed[-1].change_mask.max() > 0


def test_frame_loop_needs_nothing_but_its_parts():
    """The local runner builds the loop in-process; FrameLoop takes its parts directly."""
    loop = FrameLoop(SETTINGS, FakeDetector(), make_loop().tracker, lambda point: None, [])

    assert loop.step(frame(0, 100)).observation["detections"][0]["zone"] is None


def test_the_record_cli_is_the_run_cli_with_record(tmp_path, monkeypatch):
    from kitchen.vision import record

    video = tmp_path / "prep.avi"
    write_video(video, frames=3)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())
    monkeypatch.setattr(vision_run, "SystemClock", lambda: FakeClock(start_ms=0))

    assert record.main(["--source", str(video), "--out", str(tmp_path / "rec")]) == 0
    assert record.read_manifest(tmp_path / "rec")["frame_count"] == 3
