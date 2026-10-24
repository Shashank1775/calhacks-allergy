"""Tests for recording and replay: byte-identical round trips, schema-valid lines, kept t_ms, and refused recordings.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.record, synthetic FrameOutputs
Emits: nothing
Rules: time runs forward (strictly increasing t_ms, kept on replay); a recording that doesn't match its manifest is
       refused
"""

import hashlib
import json
import time

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays
from jsonschema import ValidationError

from kitchen.shared.fakes import FakeClock
from kitchen.shared.models import validate
from kitchen.vision.common import FrameOutput, VisionSettings
from kitchen.vision.record import (
    Recorder,
    ReplaySource,
    change_mask_params,
    mask_from_uint8,
    mask_to_uint8,
    read_manifest,
    read_recording,
)

SIZE = 16
MANIFEST = {
    "source": "video:data/recordings/prep.mp4",
    "zones_sha256": "0" * 64,
    "shift_start": "2026-10-24T17:00:00Z",
    "model_versions": {"det": "yolov8s", "pose": "yolov8s-pose"},
    "change_mask_params": change_mask_params(VisionSettings()),
}


def frame_output(frame_id, t_ms, seed):
    """A valid FrameOutput with a knife, a hand and a plate, and a random change mask in [0, 1]."""
    rng = np.random.default_rng(seed)
    detections = [
        {
            "track_id": 1,
            "entity_id": "tool-knife-1",
            "kind": "tool",
            "class_name": "knife",
            "box": [0.1, 0.2, 0.3, 0.4 + frame_id / 1000],
            "confidence": 0.87654321,
            "zone": "bin-slot-2",
            "origin": "shift_start",
        },
        {
            "track_id": 2,
            "entity_id": "hand-p1-left",
            "kind": "hand",
            "class_name": "wrist",
            "box": [0.5, 0.5, 0.58, 0.58],
            "confidence": 0.9,
            "zone": None,
            "person_id": "p1",
            "wrist": [0.54, 0.54, 0.95],
            "origin": "unknown",
        },
        {
            "track_id": 3,
            "entity_id": "plate-3",
            "kind": "plate",
            "class_name": "bowl",
            "box": [0.6, 0.6, 0.9, 0.9],
            "confidence": 1 / 3,
            "origin": "clean_stack",
        },
    ]
    observation = {
        "frame_id": frame_id,
        "t_ms": t_ms,
        "camera_id": "cam0",
        "fps_estimate": 29.97,
        "detections": detections,
    }
    mask = rng.random((SIZE, SIZE), dtype=np.float32)
    return FrameOutput(observation=observation, change_mask=mask, width=1280, height=720)


def sample_outputs(count=5):
    return [frame_output(i, 33 * i + 2, seed=i) for i in range(count)]


def record(outputs, out_dir, manifest=MANIFEST):
    with Recorder(out_dir, manifest) as recorder:
        for output in outputs:
            recorder.write(output)


def stored_masks(recording_dir):
    with np.load(recording_dir / "change.npz") as change_file:
        return change_file["change"]


def test_record_replay_record_roundtrip(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    record(sample_outputs(), first_dir)
    record(read_recording(first_dir), second_dir)

    assert (first_dir / "frames.jsonl").read_bytes() == (second_dir / "frames.jsonl").read_bytes()
    first_masks = stored_masks(first_dir)
    second_masks = stored_masks(second_dir)
    assert first_masks.dtype == np.uint8
    assert first_masks.shape == (5, SIZE, SIZE)
    np.testing.assert_array_equal(first_masks, second_masks)


def test_replay_roundtrip_gives_back_the_same_frames(tmp_path):
    outputs = sample_outputs()
    record(outputs, tmp_path)

    replayed = list(ReplaySource(tmp_path))

    assert [output.observation for output in replayed] == [output.observation for output in outputs]
    for original, again in zip(outputs, replayed, strict=True):
        assert again.change_mask.dtype == np.float32
        np.testing.assert_allclose(again.change_mask, original.change_mask, atol=0.5 / 255 + 1e-6)
        assert (again.width, again.height) == (1280, 720)


def test_every_recorded_line_validates_against_observation_frame(tmp_path):
    record(sample_outputs(), tmp_path)
    lines = (tmp_path / "frames.jsonl").read_text().splitlines()

    assert len(lines) == 5
    for line in lines:
        validate(json.loads(line), "observation_frame")


def test_lines_are_canonical_json(tmp_path):
    record(sample_outputs(1), tmp_path)
    line = (tmp_path / "frames.jsonl").read_text().splitlines()[0]
    observation = json.loads(line)
    assert line == json.dumps(observation, sort_keys=True, separators=(",", ":"))


def test_manifest_records_shape_params_hash_and_caller_fields(tmp_path):
    record(sample_outputs(), tmp_path)
    manifest = read_manifest(tmp_path)

    assert manifest["camera_id"] == "cam0"
    assert (manifest["width"], manifest["height"], manifest["clip_size"]) == (1280, 720, SIZE)
    assert manifest["frame_count"] == 5
    assert (manifest["first_t_ms"], manifest["last_t_ms"]) == (2, 134)
    assert manifest["change_mask_params"] == {"gain": 4.0, "blur_sigma_px": 1.5, "decay": 0.0}
    assert manifest["source"] == MANIFEST["source"]
    assert manifest["model_versions"] == MANIFEST["model_versions"]
    assert len(manifest["frames_sha256"]) == 64
    assert not (tmp_path / "change.u8.partial").exists()


def test_manifest_needs_change_mask_params_and_refuses_recorder_keys(tmp_path):
    with pytest.raises(ValueError, match="change_mask_params"):
        Recorder(tmp_path, {"source": "webcam:0"})
    with pytest.raises(ValueError, match="frame_count"):
        Recorder(tmp_path, MANIFEST | {"frame_count": 99})


def test_empty_recording_replays_nothing(tmp_path):
    record([], tmp_path)
    assert list(ReplaySource(tmp_path)) == []
    assert read_manifest(tmp_path)["frame_count"] == 0


@pytest.mark.safety
def test_recorder_refuses_t_ms_that_repeats_or_goes_back(tmp_path):
    with Recorder(tmp_path, MANIFEST) as recorder:
        recorder.write(frame_output(0, 100, seed=0))
        with pytest.raises(ValueError, match="time runs forward"):
            recorder.write(frame_output(1, 100, seed=1))
        with pytest.raises(ValueError, match="time runs forward"):
            recorder.write(frame_output(1, 99, seed=1))


@pytest.mark.safety
def test_replay_keeps_recorded_t_ms_regardless_of_clock(tmp_path):
    outputs = sample_outputs()
    record(outputs, tmp_path)

    replayed = list(ReplaySource(tmp_path, clock=FakeClock(start_ms=999_999)))

    assert [output.observation["t_ms"] for output in replayed] == [2, 35, 68, 101, 134]


@pytest.mark.safety
def test_replay_refuses_a_recording_with_t_ms_out_of_order(tmp_path):
    record(sample_outputs(2), tmp_path)
    frames_path = tmp_path / "frames.jsonl"
    first, second = frames_path.read_text().splitlines()
    swapped = f"{second}\n{first}\n".encode()
    frames_path.write_bytes(swapped)

    # Even with the hash made to match, the reversed time is refused.
    manifest = read_manifest(tmp_path)
    manifest["frames_sha256"] = hashlib.sha256(swapped).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="time runs forward"):
        list(read_recording(tmp_path))


def test_replay_refuses_frames_that_dont_match_the_manifest(tmp_path):
    record(sample_outputs(2), tmp_path)
    frames_path = tmp_path / "frames.jsonl"
    frames_path.write_bytes(frames_path.read_bytes().replace(b'"cam0"', b'"cam9"', 1))

    with pytest.raises(ValueError, match="frames_sha256"):
        list(read_recording(tmp_path))


def test_unclosed_recording_is_refused(tmp_path):
    recorder = Recorder(tmp_path, MANIFEST)
    recorder.write(frame_output(0, 0, seed=0))

    with pytest.raises(FileNotFoundError, match="never closed"):
        ReplaySource(tmp_path)
    recorder.close()


def test_invalid_observation_is_refused(tmp_path):
    output = frame_output(0, 0, seed=0)
    output.observation["detections"][0]["allergen"] = "peanut"

    with Recorder(tmp_path, MANIFEST) as recorder, pytest.raises(ValidationError, match="allergen"):
        recorder.write(output)


def test_pace_waits_for_real_time(tmp_path):
    outputs = [frame_output(i, 1000 + 30 * i, seed=i) for i in range(3)]
    record(outputs, tmp_path)

    started = time.monotonic()
    paced = list(ReplaySource(tmp_path, pace=True))
    elapsed_ms = (time.monotonic() - started) * 1000

    assert [output.observation["t_ms"] for output in paced] == [1000, 1030, 1060]
    assert elapsed_ms >= 55


@given(arrays(np.float32, (4, 4), elements=st.floats(0, 1, width=32)))
def test_stored_mask_roundtrip_is_stable(mask):
    stored = mask_to_uint8(mask)
    restored = mask_from_uint8(stored)

    assert restored.dtype == np.float32
    assert np.all((restored >= 0) & (restored <= 1))
    np.testing.assert_array_equal(mask_to_uint8(restored), stored)


def test_mask_outside_unit_range_is_refused():
    with pytest.raises(ValueError, match="outside"):
        mask_to_uint8(np.array([[1.5]], dtype=np.float32))
    with pytest.raises(ValueError, match="float"):
        mask_to_uint8(np.array([[255]], dtype=np.uint8))
