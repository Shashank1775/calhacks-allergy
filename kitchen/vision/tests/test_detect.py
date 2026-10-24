"""Tests for the detector: letterbox, decoding, class filter, NMS, keypoints, sessions and opening a bundle.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.detect
Emits: nothing
Rules: fail closed: a missing bundle, a wrong-shaped or non-finite output or a session error raises, never an empty
       result (safety tests); no test needs onnxruntime, hailo_platform or model files except the slow one, which skips without
       VISION_TEST_BUNDLE
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from kitchen.vision.common import SourceFrame, VisionSettings
from kitchen.vision.detect import (
    DET_CHANNELS,
    POSE_CHANNELS,
    Detector,
    decode_objects,
    decode_people,
    nms,
    open_detector,
    to_input_tensor,
)

SETTINGS = VisionSettings()
SIZE = SETTINGS.input_size  # 640
ANCHORS = 32

# A 1280x720 frame at 640: scale 0.5, so the frame fills 640x360 of the square, with 140 rows of padding above it.
FRAME = (1280, 720)
CONTENT_W, CONTENT_H, LEFT, TOP = 640, 360, 0, 140

KNIFE, SPOON, BOWL, PERSON, CUP = 43, 44, 45, 0, 41


def to_input_columns(box):
    """A box normalized to the 1280x720 frame as center x, center y, width, height in input pixels."""
    x1, y1, x2, y2 = box
    left, right = LEFT + x1 * CONTENT_W, LEFT + x2 * CONTENT_W
    top, bottom = TOP + y1 * CONTENT_H, TOP + y2 * CONTENT_H
    return [(left + right) / 2, (top + bottom) / 2, right - left, bottom - top]


def det_output(objects):
    """A [1, 84, ANCHORS] output with one column per (box, {class_id: score}); every other column is zero."""
    out = np.zeros((1, DET_CHANNELS, ANCHORS), dtype=np.float32)
    for column, (box, scores) in enumerate(objects):
        out[0, :4, column] = to_input_columns(box)
        for class_id, score in scores.items():
            out[0, 4 + class_id, column] = score
    return out


def pose_output(people):
    """A [1, 56, ANCHORS] output with one column per (box, score, {keypoint index: (x, y, visibility)})."""
    out = np.zeros((1, POSE_CHANNELS, ANCHORS), dtype=np.float32)
    for column, (box, score, keypoints) in enumerate(people):
        out[0, :4, column] = to_input_columns(box)
        out[0, 4, column] = score
        for index, (x, y, visibility) in keypoints.items():
            out[0, 5 + 3 * index : 8 + 3 * index, column] = [LEFT + x * CONTENT_W, TOP + y * CONTENT_H, visibility]
    return out


def test_object_box_maps_back_through_the_letterbox_of_a_wide_frame():
    output = det_output([((0.25, 0.5, 0.5, 0.75), {KNIFE: 0.9})])
    (knife,) = decode_objects(output, FRAME, SETTINGS)
    assert knife.class_name == "knife"
    assert knife.box == pytest.approx((0.25, 0.5, 0.5, 0.75), abs=1e-6)
    assert knife.confidence == pytest.approx(0.9)


def test_object_box_maps_back_through_the_letterbox_of_a_tall_frame():
    # 720x1280 at 640: the frame fills 360x640 with 140 columns of padding on the left.
    output = np.zeros((1, DET_CHANNELS, ANCHORS), dtype=np.float32)
    output[0, :4, 0] = [140 + 0.5 * 360, 0.5 * 640, 0.5 * 360, 0.25 * 640]
    output[0, 4 + BOWL, 0] = 0.8
    (bowl,) = decode_objects(output, (720, 1280), SETTINGS)
    assert bowl.class_name == "bowl"
    assert bowl.box == pytest.approx((0.25, 0.375, 0.75, 0.625), abs=1e-6)


def test_only_tracked_classes_are_kept():
    output = det_output(
        [
            ((0.1, 0.1, 0.2, 0.2), {PERSON: 0.95}),
            ((0.3, 0.3, 0.4, 0.4), {CUP: 0.9}),
            # Mostly a person: dropped, not relabeled as the bowl it scores lower for.
            ((0.5, 0.5, 0.6, 0.6), {PERSON: 0.9, BOWL: 0.6}),
            ((0.7, 0.7, 0.8, 0.8), {SPOON: 0.7}),
        ]
    )
    assert [d.class_name for d in decode_objects(output, FRAME, SETTINGS)] == ["spoon"]


def test_score_floor():
    floor = SETTINGS.object_min_score
    output = det_output([((0.1, 0.1, 0.2, 0.2), {KNIFE: floor}), ((0.5, 0.5, 0.6, 0.6), {KNIFE: floor - 0.01})])
    (kept,) = decode_objects(output, FRAME, SETTINGS)
    assert kept.box == pytest.approx((0.1, 0.1, 0.2, 0.2), abs=1e-6)


def test_nms_is_per_class():
    output = det_output(
        [
            ((0.10, 0.10, 0.30, 0.30), {KNIFE: 0.6}),
            ((0.11, 0.11, 0.31, 0.31), {KNIFE: 0.9}),  # overlaps the first knife: suppresses it
            ((0.10, 0.10, 0.30, 0.30), {SPOON: 0.5}),  # same place, other class: kept
            ((0.60, 0.60, 0.80, 0.80), {KNIFE: 0.7}),  # another knife elsewhere: kept
        ]
    )
    found = [(d.class_name, round(d.confidence, 2)) for d in decode_objects(output, FRAME, SETTINGS)]
    assert found == [("knife", 0.9), ("knife", 0.7), ("spoon", 0.5)]


def test_nms_keeps_best_first_and_ties_in_order():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60], [0, 0, 10, 10]], dtype=np.float32)
    scores = np.array([0.5, 0.9, 0.5, 0.5], dtype=np.float32)
    assert nms(boxes, scores, 0.45) == [1, 2]


def test_boxes_are_clipped_to_the_frame_and_padding_only_boxes_dropped():
    output = det_output(
        [
            ((-0.1, 0.9, 0.2, 1.2), {KNIFE: 0.9}),  # hangs off the bottom-left corner
            ((0.4, -0.3, 0.5, -0.1), {BOWL: 0.9}),  # wholly in the padding above the frame
        ]
    )
    (knife,) = decode_objects(output, FRAME, SETTINGS)
    assert knife.box == pytest.approx((0.0, 0.9, 0.2, 1.0), abs=1e-6)


@given(
    width=st.integers(640, 2000),
    height=st.integers(640, 2000),
    corners=st.tuples(*[st.floats(0.05, 0.45)] * 2, *[st.floats(0.55, 0.95)] * 2),
)
def test_letterbox_round_trip_recovers_the_box(width, height, corners):
    x1, y1, x2, y2 = corners
    scale = SIZE / max(width, height)
    content_w, content_h = round(width * scale), round(height * scale)
    left, top = (SIZE - content_w) // 2, (SIZE - content_h) // 2
    output = np.zeros((1, DET_CHANNELS, ANCHORS), dtype=np.float32)
    output[0, :4, 0] = [
        left + (x1 + x2) / 2 * content_w,
        top + (y1 + y2) / 2 * content_h,
        (x2 - x1) * content_w,
        (y2 - y1) * content_h,
    ]
    output[0, 4 + KNIFE, 0] = 0.9
    (knife,) = decode_objects(output, (width, height), SETTINGS)
    assert knife.box == pytest.approx(corners, abs=1e-4)


@given(arrays(np.float32, (1, DET_CHANNELS, 8), elements=st.floats(-2000, 2000, width=32)))
def test_any_output_decodes_to_boxes_inside_the_frame(output):
    for detection in decode_objects(output, FRAME, SETTINGS):
        x1, y1, x2, y2 = detection.box
        assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1


@pytest.mark.safety
@pytest.mark.parametrize("shape", [(1, 85, ANCHORS), (2, DET_CHANNELS, ANCHORS), (DET_CHANNELS, ANCHORS)])
def test_wrong_shaped_detection_output_raises(shape):
    with pytest.raises(ValueError, match="detection output"):
        decode_objects(np.zeros(shape, dtype=np.float32), FRAME, SETTINGS)


@pytest.mark.safety
def test_wrong_shaped_pose_output_raises():
    with pytest.raises(ValueError, match="pose output"):
        decode_people(np.zeros((1, DET_CHANNELS, ANCHORS), dtype=np.float32), FRAME, SETTINGS)


@pytest.mark.safety
@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize(
    ("decode", "output", "network"),
    [
        (decode_objects, det_output([((0.25, 0.5, 0.5, 0.75), {KNIFE: 0.9})]), "detection"),
        (decode_people, pose_output([((0.2, 0.1, 0.7, 0.9), 0.85, {9: (0.25, 0.6, 0.9)})]), "pose"),
    ],
)
def test_non_finite_output_raises_instead_of_an_empty_frame(decode, output, network, bad_value):
    # An all-NaN output fails every score floor, so without the check it would decode to an empty kitchen.
    with pytest.raises(ValueError, match=f"{network} output has NaN or inf"):
        decode(np.full_like(output, bad_value), FRAME, SETTINGS)
    # One bad value anywhere is enough, for example a NaN wrist with a confident visibility.
    output = output.copy()
    output[0, -1, 0] = bad_value
    with pytest.raises(ValueError, match=f"{network} output has NaN or inf"):
        decode(output, FRAME, SETTINGS)


def test_person_box_and_arm_keypoints_map_to_the_frame():
    keypoints = {7: (0.30, 0.40, 0.9), 8: (0.60, 0.40, 0.8), 9: (0.25, 0.60, 0.95), 10: (0.65, 0.62, 0.2)}
    output = pose_output([((0.2, 0.1, 0.7, 0.9), 0.85, keypoints)])
    (person,) = decode_people(output, FRAME, SETTINGS)
    assert person.box == pytest.approx((0.2, 0.1, 0.7, 0.9), abs=1e-6)
    assert person.confidence == pytest.approx(0.85)
    assert person.left_elbow == pytest.approx((0.30, 0.40, 0.9), abs=1e-6)
    assert person.right_elbow == pytest.approx((0.60, 0.40, 0.8), abs=1e-6)
    assert person.left_wrist == pytest.approx((0.25, 0.60, 0.95), abs=1e-6)
    # A barely visible wrist keeps its low confidence for tracking.py to reject; it is never raised or guessed.
    assert person.right_wrist == pytest.approx((0.65, 0.62, 0.2), abs=1e-6)


def test_keypoints_outside_the_frame_are_clipped_and_keep_their_visibility():
    keypoints = {9: (-0.1, 1.2, 0.9), 10: (1.3, -0.2, 0.4)}
    output = pose_output([((0.2, 0.1, 0.7, 0.9), 0.85, keypoints)])
    (person,) = decode_people(output, FRAME, SETTINGS)
    assert person.left_wrist == pytest.approx((0.0, 1.0, 0.9), abs=1e-6)
    assert person.right_wrist == pytest.approx((1.0, 0.0, 0.4), abs=1e-6)


def test_a_person_wholly_in_the_padding_is_dropped():
    output = pose_output(
        [
            ((0.4, -0.3, 0.5, -0.1), 0.9, {}),  # wholly in the padding above the frame
            ((0.1, 0.1, 0.4, 0.9), 0.8, {}),
        ]
    )
    (person,) = decode_people(output, FRAME, SETTINGS)
    assert person.box == pytest.approx((0.1, 0.1, 0.4, 0.9), abs=1e-6)


def test_people_score_floor_and_nms():
    floor = SETTINGS.person_min_score
    output = pose_output(
        [
            ((0.10, 0.10, 0.40, 0.90), 0.9, {}),
            ((0.11, 0.10, 0.41, 0.90), 0.7, {}),  # the same person again: suppressed
            ((0.60, 0.10, 0.90, 0.90), floor - 0.01, {}),  # below the floor
        ]
    )
    (person,) = decode_people(output, FRAME, SETTINGS)
    assert person.confidence == pytest.approx(0.9)


class FakeSession:
    """Stands in for an onnxruntime session: one input, a canned output, and the tensors it was given."""

    def __init__(self, output, input_shape=(1, 3, SIZE, SIZE), error=None):
        self.output = output
        self.input_shape = list(input_shape)
        self.error = error
        self.feeds = []

    def get_inputs(self):
        return [type("Input", (), {"name": "images", "shape": self.input_shape})()]

    def run(self, output_names, input_feed):
        self.feeds.append(input_feed)
        if self.error:
            raise self.error
        return [self.output]


def red_frame():
    """A 1280x720 frame that is pure red, so the tensor shows both the channel order and the padding."""
    rgb = np.zeros((FRAME[1], FRAME[0], 3), dtype=np.uint8)
    rgb[..., 0] = 255
    return SourceFrame(frame_id=1, t_ms=40, rgb=rgb)


def test_detector_runs_both_sessions_on_one_letterboxed_tensor():
    det = FakeSession(det_output([((0.25, 0.5, 0.5, 0.75), {KNIFE: 0.9})]))
    pose = FakeSession(pose_output([((0.2, 0.1, 0.7, 0.9), 0.85, {9: (0.25, 0.6, 0.95)})]))
    detector = Detector({"det": det, "pose": pose}, SETTINGS, "onnx")

    objects, people = detector.detect(red_frame())

    assert [o.class_name for o in objects] == ["knife"]
    assert objects[0].box == pytest.approx((0.25, 0.5, 0.5, 0.75), abs=1e-6)
    assert people[0].left_wrist == pytest.approx((0.25, 0.6, 0.95), abs=1e-6)
    (feed,) = det.feeds
    tensor = feed["images"]
    assert tensor.shape == (1, 3, SIZE, SIZE) and tensor.dtype == np.float32
    assert pose.feeds[0]["images"] is tensor
    assert tensor[0, :, TOP + 10, 10].tolist() == [1.0, 0.0, 0.0]  # RGB, 0 to 1
    assert tensor[0, :, 10, 10] == pytest.approx([114 / 255] * 3)  # the shared gray padding
    assert detector.backend == "onnx"


def test_input_tensor_uses_the_shared_clip_letterbox():
    from kitchen.shared.clips import letterbox_frame

    rgb = np.random.default_rng(0).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    expected = letterbox_frame(rgb, SIZE).transpose(2, 0, 1)[None] / 255
    assert to_input_tensor(rgb, SIZE) == pytest.approx(expected)


@pytest.mark.safety
def test_session_error_propagates_instead_of_an_empty_frame():
    det = FakeSession(None, error=RuntimeError("device stuck"))
    pose = FakeSession(pose_output([]))
    detector = Detector({"det": det, "pose": pose}, SETTINGS, "hailo")
    with pytest.raises(RuntimeError, match="device stuck"):
        detector.detect(red_frame())


@pytest.mark.safety
@pytest.mark.parametrize("network", ["det", "pose"])
def test_detector_refuses_a_model_of_another_input_size(network):
    sessions = {"det": FakeSession(None), "pose": FakeSession(None)}
    sessions[network] = FakeSession(None, input_shape=(1, 3, 320, 320))
    with pytest.raises(ValueError, match=f"the {network} network takes .*320"):
        Detector(sessions, SETTINGS, "onnx")


@pytest.mark.safety
def test_detector_needs_both_networks():
    with pytest.raises(ValueError, match="det and pose"):
        Detector({"det": FakeSession(None)}, SETTINGS, "onnx")


@pytest.mark.safety
def test_missing_bundle_raises_at_startup(tmp_path):
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        open_detector(VisionSettings(bundle_dir=str(tmp_path / "nowhere")))


@pytest.mark.safety
def test_bundle_missing_a_network_raises(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"networks": {"det": {"onnx": "det.onnx"}}}))
    with pytest.raises(ValueError, match="det and pose"):
        open_detector(VisionSettings(bundle_dir=str(tmp_path)))


@pytest.mark.safety
def test_bundle_missing_an_onnx_file_raises(tmp_path):
    networks = {"det": {"onnx": "det.onnx"}, "pose": {"onnx": "pose.onnx"}}
    (tmp_path / "manifest.json").write_text(json.dumps({"networks": networks}))
    (tmp_path / "det.onnx").write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="pose.onnx"):
        open_detector(VisionSettings(bundle_dir=str(tmp_path)))


@pytest.mark.safety
def test_unknown_backend_raises(tmp_path):
    with pytest.raises(ValueError, match="VISION_DETECTOR"):
        open_detector(VisionSettings(detector="cpu", bundle_dir=str(tmp_path)))


def test_importing_detect_needs_no_runtime():
    code = (
        "import sys, kitchen.vision.detect; "
        "assert 'onnxruntime' not in sys.modules and 'hailo_platform' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.slow
def test_real_bundle_finds_people_in_the_parity_frames():
    bundle = os.environ.get("VISION_TEST_BUNDLE")
    if not bundle or not (Path(bundle) / "manifest.json").is_file():
        pytest.skip("set VISION_TEST_BUNDLE to a deploy bundle folder to run the real models")
    detector = open_detector(VisionSettings(bundle_dir=bundle))
    frames = np.load(Path(bundle) / "parity" / "inputs.npy")

    for i, rgb in enumerate(frames):
        objects, people = detector.detect(SourceFrame(frame_id=i, t_ms=i * 33, rgb=rgb))
        assert people, f"no person found in parity frame {i}"
        for found in [*objects, *people]:
            x1, y1, x2, y2 = found.box
            assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1
