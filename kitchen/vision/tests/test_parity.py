"""Tests for the parity check: the reference decode, matching, and the agree/disagree verdict.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.parity, kitchen.vision.hailo (to build ONNX-shaped outputs)
Emits: nothing
Rules: none of the nine; this checks the tool the owner uses to accept the HEFs
"""

import numpy as np

from kitchen.vision.hailo import HailoSession, detections_to_onnx
from kitchen.vision.parity import compare, decode, main, run_backend

SIZE = 64


class FakeSession:
    """An onnxruntime-like session that returns the same output for every frame."""

    def __init__(self, output):
        self.output = output

    def get_inputs(self):
        return [type("Input", (), {"name": "images"})]

    def run(self, output_names, input_feed):
        return [self.output]


def detection_output(boxes_by_class):
    """An ONNX-shaped detection output from per-class [y_min, x_min, y_max, x_max, score] rows."""
    return detections_to_onnx([np.array(rows, dtype=np.float32).reshape(-1, 5) for rows in boxes_by_class], SIZE, SIZE)


def test_decode_filters_scores_and_suppresses_overlaps():
    output = detection_output(
        [
            [[0.0, 0.0, 0.5, 0.5, 0.9], [0.01, 0.01, 0.5, 0.5, 0.8], [0.5, 0.5, 1.0, 1.0, 0.1]],
            [[0.0, 0.0, 0.5, 0.5, 0.7]],
        ]
    )
    rows = decode(output)

    # The near-duplicate class-0 box is suppressed, the 0.1 box is below MIN_SCORE, and class 1 survives on its own.
    assert sorted(rows[:, 5].tolist()) == [0, 1]
    class_zero = rows[rows[:, 5] == 0][0]
    np.testing.assert_allclose(class_zero[:5], [0, 0, 32, 32, 0.9], atol=1e-5)


def test_identical_outputs_agree():
    detections = run_backend({"det": FakeSession(detection_output([[[0, 0, 0.5, 0.5, 0.9]]]))}, np.zeros((3, 4, 4, 3)))

    report = compare(detections, detections)

    assert report["det"]["agree"]
    assert report["det"]["recall"] == report["det"]["precision"] == 1.0


def test_shifted_boxes_disagree():
    reference = {"det_0": decode(detection_output([[[0, 0, 0.5, 0.5, 0.9]]]))}
    shifted = {"det_0": decode(detection_output([[[0, 0.3, 0.5, 0.8, 0.9]]]))}

    assert not compare(reference, shifted)["det"]["agree"]


def test_missing_boxes_disagree():
    reference = {"det_0": decode(detection_output([[[0, 0, 0.5, 0.5, 0.9], [0.5, 0.5, 1, 1, 0.9]]]))}
    missing = {"det_0": decode(detection_output([[[0, 0, 0.5, 0.5, 0.9]]]))}

    report = compare(reference, missing)["det"]

    assert report["recall"] == 0.5
    assert not report["agree"]


def test_a_hailo_session_runs_through_the_same_parity_path():
    class Network:
        is_nms, num_classes, input_shape = True, 1, (SIZE, SIZE, 3)

        def infer(self, frame):
            return {"nms": [np.array([[0, 0, 0.5, 0.5, 0.9]], dtype=np.float32)]}

    frames = np.zeros((2, SIZE, SIZE, 3), dtype=np.uint8)
    hailo = run_backend({"det": HailoSession(Network())}, frames)
    onnx = run_backend({"det": FakeSession(detection_output([[[0, 0, 0.5, 0.5, 0.9]]]))}, frames)

    assert compare(onnx, hailo)["det"]["agree"]


def test_compare_cli_exits_one_on_disagreement(tmp_path):
    np.savez(tmp_path / "onnx.npz", det_0=decode(detection_output([[[0, 0, 0.5, 0.5, 0.9]]])))
    np.savez(tmp_path / "hailo.npz", det_0=decode(detection_output([[]])))

    assert main(["compare", str(tmp_path / "onnx.npz"), str(tmp_path / "onnx.npz")]) == 0
    assert main(["compare", str(tmp_path / "onnx.npz"), str(tmp_path / "hailo.npz")]) == 1
