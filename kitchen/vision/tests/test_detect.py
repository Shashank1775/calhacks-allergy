"""Tests for the detector: letterbox, decoding, class filter (stock COCO or a bundle's class_names), NMS, keypoints,
sessions and opening a bundle.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.detect
Emits: nothing
Rules: fail closed: a missing bundle, bad class names, a wrong-shaped or non-finite output or a session error raises,
       never an empty result, and a class vision doesn't track is dropped, never relabeled (safety tests); a stock
       bundle decodes exactly as before custom detectors; no test needs onnxruntime, hailo_platform or model files
       except the slow one, which skips without VISION_TEST_BUNDLE
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

from kitchen.vision import detect
from kitchen.vision.common import COCO_OBJECTS, Detection, SourceFrame, VisionSettings
from kitchen.vision.detect import (
    COCO_DETECTOR,
    DET_CHANNELS,
    POSE_CHANNELS,
    Detector,
    decode_objects,
    decode_people,
    detector_classes,
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
FORK = 42

# The fine-tuned detector's classes, in its output order.
CUSTOM_NAMES = ["plate", "bowl", "knife", "spoon", "fork"]
CUSTOM_CHANNELS = 4 + len(CUSTOM_NAMES)
CUSTOM = detector_classes({"classes": len(CUSTOM_NAMES), "class_names": CUSTOM_NAMES})


def to_input_columns(box):
    """A box normalized to the 1280x720 frame as center x, center y, width, height in input pixels."""
    x1, y1, x2, y2 = box
    left, right = LEFT + x1 * CONTENT_W, LEFT + x2 * CONTENT_W
    top, bottom = TOP + y1 * CONTENT_H, TOP + y2 * CONTENT_H
    return [(left + right) / 2, (top + bottom) / 2, right - left, bottom - top]


def det_output(objects, channels=DET_CHANNELS):
    """A [1, channels, ANCHORS] output (84 for stock COCO) with one column per (box, {class_id: score}); every other
    column is zero."""
    out = np.zeros((1, channels, ANCHORS), dtype=np.float32)
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


def test_input_tensor_has_the_shared_clip_letterbox_geometry():
    """The content sits exactly where clips.py's letterbox puts it (so boxes map back the same way), with the shared
    gray around it; only the resampling filter differs, so a smooth image comes out nearly the same."""
    from kitchen.shared.clips import LETTERBOX_PAD, letterbox_frame, letterbox_layout

    x = np.linspace(0, 255, 1280)[None, :, None]
    y = np.linspace(0, 255, 720)[:, None, None]
    rgb = np.broadcast_to((x + y) / 2, (720, 1280, 3)).astype(np.uint8)
    tensor = to_input_tensor(rgb, SIZE)

    content_width, content_height, left, top = letterbox_layout(1280, 720, SIZE)
    pixels = np.rint(tensor[0].transpose(1, 2, 0) * 255).astype(np.uint8)
    assert (pixels[:top] == LETTERBOX_PAD).all()
    assert (pixels[top + content_height :] == LETTERBOX_PAD).all()
    assert pixels.shape == (SIZE, SIZE, 3)
    shared = letterbox_frame(rgb, SIZE).astype(int)
    assert np.abs(pixels.astype(int) - shared).max() <= 3


def test_input_tensor_upscales_a_frame_smaller_than_the_input():
    """A small webcam frame (smaller than the network input) is upscaled into the square instead of refused."""
    tensor = to_input_tensor(np.zeros((240, 320, 3), dtype=np.uint8), SIZE)

    assert tensor.shape == (1, 3, SIZE, SIZE)


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


def test_onnx_providers_keep_the_wanted_order_drop_missing_ones_and_end_on_the_cpu():
    from kitchen.vision.detect import onnx_providers

    available = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    assert onnx_providers("CoreMLExecutionProvider,CPUExecutionProvider", available) == available
    assert onnx_providers("CUDAExecutionProvider, CoreMLExecutionProvider", available) == available
    assert onnx_providers("CUDAExecutionProvider", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_a_custom_detector_decodes_its_own_classes_in_output_order():
    output = det_output(
        [
            ((0.1, 0.1, 0.2, 0.2), {0: 0.9}),
            ((0.3, 0.3, 0.4, 0.4), {1: 0.8}),
            ((0.5, 0.5, 0.6, 0.6), {2: 0.7}),
            ((0.7, 0.7, 0.8, 0.8), {3: 0.6}),
            ((0.1, 0.7, 0.2, 0.8), {4: 0.5}),
        ],
        CUSTOM_CHANNELS,
    )
    found = decode_objects(output, FRAME, SETTINGS, CUSTOM)

    assert [d.class_name for d in found] == ["plate", "bowl", "knife", "spoon", "fork"]
    assert found[0].box == pytest.approx((0.1, 0.1, 0.2, 0.2), abs=1e-6)
    assert found[0].confidence == pytest.approx(0.9)


@pytest.mark.safety
def test_a_custom_class_vision_does_not_track_is_dropped_never_relabeled():
    classes = detector_classes({"classes": 4, "class_names": ["cup", "knife", "ladle", "plate"]})
    output = det_output(
        [
            ((0.1, 0.1, 0.2, 0.2), {0: 0.95}),  # a cup
            ((0.3, 0.3, 0.4, 0.4), {2: 0.9}),  # a ladle: no entity kind, so never a tool
            ((0.5, 0.5, 0.6, 0.6), {0: 0.9, 1: 0.6}),  # mostly a cup: dropped, not the knife it scores lower for
            ((0.7, 0.7, 0.8, 0.8), {3: 0.7}),
        ],
        4 + 4,
    )

    assert classes.tracked == {1: "knife", 3: "plate"}
    assert [d.class_name for d in decode_objects(output, FRAME, SETTINGS, classes)] == ["plate"]


def decode_objects_before_custom_classes(output, frame_size, settings):
    """decode_objects as it was before bundles could name their classes: the reference for stock bundles."""
    columns = detect._columns(output, DET_CHANNELS, "detection")
    scores = columns[:, 4:].max(axis=1)
    classes = columns[:, 4:].argmax(axis=1)
    wanted = np.isin(classes, list(COCO_OBJECTS)) & (scores >= settings.object_min_score)
    boxes, scores, classes = detect._corner_boxes(columns[wanted]), scores[wanted], classes[wanted]

    layout = detect.letterbox_for(*frame_size, settings.input_size)
    found = []
    for class_id in sorted(set(classes.tolist())):
        same = np.flatnonzero(classes == class_id)
        for i in same[nms(boxes[same], scores[same], settings.nms_iou)]:
            box = detect._to_frame_boxes(boxes[i : i + 1], layout)[0]
            if detect._has_area(box):
                found.append(Detection(COCO_OBJECTS[class_id], detect._box(box), float(scores[i])))
    return sorted(found, key=lambda detection: -detection.confidence)


@pytest.mark.safety
def test_the_fine_tunes_hand_class_is_dropped_and_hands_still_come_from_pose():
    """The fine-tune's six classes end in "hand", which has no detector entity kind: hands are made from pose wrists
    (tracking.py), so a detector hand box is dropped, never turned into a tool or a second hand."""
    names = [*CUSTOM_NAMES, "hand"]
    classes = detector_classes({"classes": 6, "class_names": names})
    output = det_output([((0.1, 0.1, 0.2, 0.2), {5: 0.95}), ((0.5, 0.5, 0.6, 0.6), {2: 0.8})], 4 + len(names))

    assert classes.tracked == dict(enumerate(CUSTOM_NAMES))
    assert [d.class_name for d in decode_objects(output, FRAME, SETTINGS, classes)] == ["knife"]


def test_a_stock_manifest_is_the_coco_detector():
    """No class_names (every stock bundle, stock-yolov8s and stock-yolov8s-hailo8l included) is COCO, indices 42-45."""
    assert detector_classes({"onnx": "det.onnx", "classes": 80}) == COCO_DETECTOR
    assert detector_classes({"onnx": "det.onnx"}) == COCO_DETECTOR
    output = det_output(
        [
            ((0.1, 0.1, 0.2, 0.2), {KNIFE: 0.9}),
            ((0.3, 0.3, 0.4, 0.4), {SPOON: 0.8}),
            ((0.5, 0.5, 0.6, 0.6), {FORK: 0.7}),
            ((0.7, 0.7, 0.8, 0.8), {BOWL: 0.6}),
            ((0.1, 0.5, 0.2, 0.6), {PERSON: 0.95}),
            ((0.5, 0.1, 0.6, 0.2), {CUP: 0.9}),
        ]
    )

    assert [(d.class_name, round(d.confidence, 2)) for d in decode_objects(output, FRAME, SETTINGS, COCO_DETECTOR)] == [
        ("knife", 0.9),
        ("spoon", 0.8),
        ("fork", 0.7),
        ("bowl", 0.6),
    ]


def random_coco_output(seed, anchors=2000):
    """A seeded random [1, 84, anchors] output: boxes anywhere in the input (some in the padding or past its edge) and
    mostly low scores, so about a twentieth of the anchors are a tracked class and many of those overlap."""
    rng = np.random.default_rng(seed)
    output = np.zeros((1, DET_CHANNELS, anchors), dtype=np.float32)
    output[0, 0:2] = rng.uniform(-50, SIZE + 50, (2, anchors))
    output[0, 2:4] = rng.uniform(1, 200, (2, anchors))
    output[0, 4:] = rng.uniform(0, 1, (DET_CHANNELS - 4, anchors)) ** 3
    return output


@pytest.mark.safety
@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("frame_size", [(1280, 720), (640, 640), (720, 1280)])
def test_a_stock_84_channel_decode_is_bit_for_bit_what_it_was_before(seed, frame_size):
    output = random_coco_output(seed)
    stock = decode_objects(output, frame_size, SETTINGS, detector_classes({"classes": 80}))

    assert len(stock) > 10
    assert stock == decode_objects_before_custom_classes(output, frame_size, SETTINGS)


@pytest.mark.safety
def test_a_custom_detector_refuses_an_output_with_another_class_count():
    with pytest.raises(ValueError, match=r"detection output must be \[1, 9, anchors\]"):
        decode_objects(np.zeros((1, DET_CHANNELS, ANCHORS), dtype=np.float32), FRAME, SETTINGS, CUSTOM)


@pytest.mark.safety
@pytest.mark.parametrize("backend", ["onnx", "hailo"])
@pytest.mark.parametrize(
    ("det_network", "message"),
    [
        ({"classes": 5, "class_names": []}, "non-empty list of strings"),
        ({"classes": 5, "class_names": "plate,bowl,knife,spoon,fork"}, "non-empty list of strings"),
        ({"classes": 2, "class_names": ["plate", 7]}, "non-empty list of strings"),
        ({"classes": 6, "class_names": CUSTOM_NAMES}, "5 class_names but 6 classes"),
        ({"classes": 2, "class_names": ["cup", "ladle"]}, "none of"),
        ({"classes": 5}, "5 classes but no class_names"),
    ],
)
def test_bad_class_names_raise_at_startup_before_any_network_opens(tmp_path, backend, det_network, message):
    # No network files exist and hailo_platform isn't installed here, so only a check made first can raise this.
    networks = {"det": {"onnx": "det.onnx", **det_network}, "pose": {"onnx": "pose.onnx"}}
    (tmp_path / "manifest.json").write_text(json.dumps({"networks": networks}))
    with pytest.raises(ValueError, match=message):
        open_detector(VisionSettings(detector=backend, bundle_dir=str(tmp_path)))


def test_open_detector_runs_the_class_names_from_the_manifest(tmp_path, monkeypatch):
    networks = {"det": {"onnx": "det.onnx", "classes": 5, "class_names": CUSTOM_NAMES}, "pose": {"onnx": "pose.onnx"}}
    (tmp_path / "manifest.json").write_text(json.dumps({"networks": networks}))
    sessions = {
        "det": FakeSession(det_output([((0.25, 0.5, 0.5, 0.75), {0: 0.9})], CUSTOM_CHANNELS)),
        "pose": FakeSession(pose_output([])),
    }
    monkeypatch.setattr("kitchen.vision.detect.open_onnx_sessions", lambda bundle_dir, networks, providers: sessions)

    objects, _ = open_detector(VisionSettings(bundle_dir=str(tmp_path))).detect(red_frame())

    assert [o.class_name for o in objects] == ["plate"]
