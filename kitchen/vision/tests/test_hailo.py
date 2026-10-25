"""Tests for the Hailo backend on a mock runtime: HEF outputs come back exactly as the ONNX models return them.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.hailo
Emits: nothing
Rules: fail closed: a device error raises and returns nothing, and a detection HEF whose class count isn't the
       manifest's fails at startup (safety tests); no test needs hailo_platform
"""

import json
import subprocess
import sys
import types

import numpy as np
import pytest

from kitchen.vision.common import VisionSettings
from kitchen.vision.detect import decode_objects, detector_classes
from kitchen.vision.hailo import (
    DFL_BINS,
    KEYPOINTS,
    STRIDES,
    HailoSession,
    anchor_count,
    check_detection_classes,
    detections_to_onnx,
    open_sessions,
    pose_heads_to_onnx,
    to_hailo_input,
)

SIZE = 64  # a small input keeps the grids tiny: 8x8, 4x4 and 2x2 cells


class FakeNetwork:
    """Stands in for HailoNetwork: returns canned raw outputs and remembers the frame it was given."""

    def __init__(self, raw, is_nms, num_classes=0, error=None):
        self.raw = raw
        self.is_nms = is_nms
        self.num_classes = num_classes
        self.input_shape = (SIZE, SIZE, 3)
        self.error = error
        self.frames = []

    def infer(self, frame):
        self.frames.append(frame)
        if self.error:
            raise self.error
        return self.raw


def encode_pose_heads(cells):
    """Build raw pose heads that decode to known values: {(stride, row, col): (left, top, right, bottom, score, kx)}.

    A box side's distribution is one-hot (a large logit) at its distance in stride units; keypoint x is chosen so it
    decodes to kx pixels; visibility logits are 0 (sigmoid 0.5).
    """
    heads = {}
    for stride in STRIDES:
        n = SIZE // stride
        box = np.full((n, n, 4, DFL_BINS), -50.0, dtype=np.float32)
        score = np.zeros((n, n, 1), dtype=np.float32)
        keypoints = np.zeros((n, n, KEYPOINTS, 3), dtype=np.float32)
        for (cell_stride, row, col), (left, top, right, bottom, cell_score, kx) in cells.items():
            if cell_stride != stride:
                continue
            for side, bins in enumerate((left, top, right, bottom)):
                box[row, col, side, bins] = 50.0
            score[row, col, 0] = cell_score
            keypoints[row, col, :, 0] = (kx / stride - col) / 2
        heads[f"box_{stride}"] = box.reshape(1, n, n, 4 * DFL_BINS)
        heads[f"score_{stride}"] = score[None]
        heads[f"keypoints_{stride}"] = keypoints.reshape(1, n, n, 3 * KEYPOINTS)
    return heads


def column_index(stride, row, col):
    """Where a grid cell lands in the ONNX output: strides 8, 16 then 32, each grid row-major."""
    offset = sum((SIZE // s) ** 2 for s in STRIDES if s < stride)
    return offset + row * (SIZE // stride) + col


def test_anchor_count_matches_yolov8_at_640():
    assert anchor_count(640, 640) == 8400


def test_detections_become_onnx_columns():
    person = np.array([[0.25, 0.5, 0.75, 1.0, 0.9]])  # y_min, x_min, y_max, x_max, score
    knife = np.array([[0.0, 0.0, 0.5, 0.25, 0.6], [0.5, 0.5, 1.0, 1.0, 0.3]])
    out = detections_to_onnx([person, np.zeros((0, 5)), knife], SIZE, SIZE)

    assert out.shape == (1, 4 + 3, anchor_count(SIZE, SIZE))
    np.testing.assert_allclose(out[0, :, 0], [48, 32, 32, 32, 0.9, 0, 0])
    np.testing.assert_allclose(out[0, :, 1], [8, 16, 16, 32, 0, 0, 0.6])
    np.testing.assert_allclose(out[0, :, 2], [48, 48, 32, 32, 0, 0, 0.3])
    assert not out[0, :, 3:].any()


def test_pose_heads_decode_like_the_onnx_export():
    cells = {(8, 2, 3): (1, 2, 3, 4, 0.8, 20.0), (32, 1, 0): (0, 1, 1, 0, 0.4, 40.0)}
    out = pose_heads_to_onnx(encode_pose_heads(cells), SIZE, SIZE)

    assert out.shape == (1, 56, anchor_count(SIZE, SIZE))
    # Stride 8, cell (2, 3): center (28, 20); distances 8, 16, 24, 32 give x1=20, y1=4, x2=52, y2=52.
    first = out[0, :, column_index(8, 2, 3)]
    np.testing.assert_allclose(first[:5], [36, 28, 32, 48, 0.8], atol=1e-4)
    np.testing.assert_allclose(first[5::3], 20.0, atol=1e-4)
    np.testing.assert_allclose(first[7::3], 0.5, atol=1e-6)
    # Stride 32, cell (1, 0): center (16, 48); distances 0, 32, 32, 0 give x1=16, y1=16, x2=48, y2=48.
    second = out[0, :, column_index(32, 1, 0)]
    np.testing.assert_allclose(second[:5], [32, 32, 32, 32, 0.4], atol=1e-4)
    np.testing.assert_allclose(second[5::3], 40.0, atol=1e-4)


def test_pose_heads_are_found_by_shape_not_name():
    cells = {(16, 0, 1): (1, 1, 1, 1, 0.7, 24.0)}
    heads = encode_pose_heads(cells)
    shuffled = {f"conv{i}": heads[name] for i, name in enumerate(reversed(list(heads)))}

    np.testing.assert_array_equal(pose_heads_to_onnx(shuffled, SIZE, SIZE), pose_heads_to_onnx(heads, SIZE, SIZE))


def test_unrecognized_pose_heads_raise():
    heads = encode_pose_heads({})
    del heads["keypoints_16"]
    with pytest.raises(ValueError, match="no recognizable heads"):
        pose_heads_to_onnx(heads, SIZE, SIZE)


def test_input_goes_to_the_device_as_the_original_uint8_frame():
    frame = np.random.default_rng(0).integers(0, 256, (SIZE, SIZE, 3), dtype=np.uint8)
    tensor = frame.transpose(2, 0, 1)[None].astype(np.float32) / 255

    np.testing.assert_array_equal(to_hailo_input(tensor, SIZE, SIZE), frame)


def test_wrong_input_shape_raises():
    with pytest.raises(ValueError, match="expected a"):
        to_hailo_input(np.zeros((1, 3, SIZE, SIZE + 1), dtype=np.float32), SIZE, SIZE)


def test_detection_session_looks_like_an_onnx_session():
    network = FakeNetwork({"nms": [np.array([[0.25, 0.5, 0.75, 1.0, 0.9]])]}, is_nms=True, num_classes=1)
    session = HailoSession(network)
    tensor = np.zeros((1, 3, SIZE, SIZE), dtype=np.float32)

    (out,) = session.run(None, {session.get_inputs()[0].name: tensor})

    assert session.kind == "detect"
    assert session.get_inputs()[0].shape == [1, 3, SIZE, SIZE]
    assert session.get_outputs()[0].shape == list(out.shape) == [1, 5, anchor_count(SIZE, SIZE)]
    assert network.frames[0].dtype == np.uint8
    assert network.frames[0].shape == (SIZE, SIZE, 3)


def test_pose_session_returns_the_pose_layout():
    session = HailoSession(FakeNetwork(encode_pose_heads({}), is_nms=False))
    (out,) = session.run(None, {"images": np.zeros((1, 3, SIZE, SIZE), dtype=np.float32)})

    assert session.kind == "pose"
    assert session.get_outputs()[0].shape == list(out.shape) == [1, 56, anchor_count(SIZE, SIZE)]


@pytest.mark.safety
def test_device_error_raises_instead_of_returning_detections():
    """Fail closed: a stuck or failed device stops frames (health goes stale, the gate faults); it never looks empty."""
    session = HailoSession(FakeNetwork({}, is_nms=True, num_classes=1, error=RuntimeError("HAILO_TIMEOUT")))

    with pytest.raises(RuntimeError, match="HAILO_TIMEOUT"):
        session.run(None, {"images": np.zeros((1, 3, SIZE, SIZE), dtype=np.float32)})


def test_a_custom_hef_reports_its_classes_in_class_names_order():
    """The HEF's NMS lists classes by id in the output order of the ONNX it was compiled from, the order of
    class_names, and detections_to_onnx keeps class id i on score row 4 + i: so class id i decodes as class_names[i]."""
    names = ["plate", "bowl", "knife", "spoon", "fork"]
    per_class = [np.zeros((0, 5))] * len(names)
    per_class[0] = np.array([[0.0, 0.0, 0.5, 0.5, 0.9]])  # a plate, top left
    per_class[4] = np.array([[0.5, 0.5, 1.0, 1.0, 0.8]])  # a fork, bottom right
    session = HailoSession(FakeNetwork({"nms": per_class}, is_nms=True, num_classes=len(names)))
    check_detection_classes(session, len(names))

    (out,) = session.run(None, {"images": np.zeros((1, 3, SIZE, SIZE), dtype=np.float32)})
    classes = detector_classes({"classes": len(names), "class_names": names})
    found = decode_objects(out, (SIZE, SIZE), VisionSettings(input_size=SIZE), classes)

    assert [d.class_name for d in found] == ["plate", "fork"]
    assert found[0].box == pytest.approx((0.0, 0.0, 0.5, 0.5), abs=1e-6)
    assert found[1].box == pytest.approx((0.5, 0.5, 1.0, 1.0), abs=1e-6)


@pytest.mark.safety
def test_a_detection_hef_with_another_class_count_than_the_manifest_fails_at_startup():
    """A stock 80-class HEF left in a custom bundle (or the reverse) would put each class's boxes under another class's
    name, so the count must match before any frame runs."""
    session = HailoSession(FakeNetwork({}, is_nms=True, num_classes=80))

    with pytest.raises(ValueError, match="the det HEF scores 80 classes, but the bundle manifest has 5"):
        check_detection_classes(session, 5)
    check_detection_classes(session, 80)


class FakeVDevice:
    """Stands in for hailo_platform.VDevice: open_sessions only makes one and hands it to each network."""

    def __init__(self, params):
        self.params = params

    @staticmethod
    def create_params():
        return types.SimpleNamespace()


@pytest.mark.safety
@pytest.mark.parametrize(("hef_classes", "opens"), [(5, True), (80, False)])
def test_open_sessions_checks_the_det_hef_against_the_manifest_class_names(tmp_path, monkeypatch, hef_classes, opens):
    names = ["plate", "bowl", "knife", "spoon", "fork"]
    networks = {"det": {"hef": "det.hef", "classes": 5, "class_names": names}, "pose": {"hef": "pose.hef"}}
    (tmp_path / "manifest.json").write_text(json.dumps({"networks": networks}))
    scheduling = types.SimpleNamespace(ROUND_ROBIN="round robin")
    hailo_platform = types.SimpleNamespace(VDevice=FakeVDevice, HailoSchedulingAlgorithm=scheduling)
    monkeypatch.setitem(sys.modules, "hailo_platform", hailo_platform)

    def fake_network(vdevice, hef_path):
        is_det = hef_path.name == "det.hef"
        return FakeNetwork({}, is_nms=is_det, num_classes=hef_classes if is_det else 0)

    monkeypatch.setattr("kitchen.vision.hailo.HailoNetwork", fake_network)

    if opens:
        assert open_sessions(tmp_path)["det"].get_outputs()[0].shape[1] == 4 + 5
    else:
        with pytest.raises(ValueError, match="the det HEF scores 80 classes, but the bundle manifest has 5"):
            open_sessions(tmp_path)


def test_no_hailo_import_by_default():
    """The laptop, CI and the rest of vision never need hailo_platform: importing these modules doesn't load it."""
    code = (
        "import sys, kitchen.vision.hailo, kitchen.vision.parity, kitchen.vision.bundle; "
        "assert 'hailo_platform' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
