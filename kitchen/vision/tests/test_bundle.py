"""Tests for the deploy bundle builder, with ONNX inspection and the Model Zoo download stubbed out.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.bundle; a tiny synthetic video written by the test
Emits: nothing
Rules: the bundle's ONNX files are byte-identical to the inputs; stock HEFs only for stock-shaped models
"""

import json

import cv2
import numpy as np
import pytest

from kitchen.vision import bundle

STOCK = {
    "det": {"height": 640, "width": 640, "channels": 84},
    "pose": {"height": 640, "width": 640, "channels": 56},
}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    """Two fake ONNX files, a 20-frame video, and stubs so no real model or network is needed."""
    det, pose = tmp_path / "yolov8s.onnx", tmp_path / "yolov8s-pose.onnx"
    det.write_bytes(b"det weights")
    pose.write_bytes(b"pose weights")

    video = tmp_path / "prep.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
    for i in range(20):
        writer.write(np.full((120, 160, 3), i * 10, dtype=np.uint8))
    writer.release()

    def fake_fetch(model, dest):
        dest.write_bytes(model.encode())
        return f"zoo/{model}"

    shapes = {det: dict(STOCK["det"]), pose: dict(STOCK["pose"])}
    monkeypatch.setattr(bundle, "inspect_onnx", lambda path: dict(shapes[path]))
    monkeypatch.setattr(bundle, "fetch_zoo_hef", fake_fetch)
    return {"det": det, "pose": pose, "video": video, "shapes": shapes}


def build(tmp_path, inputs, weights):
    """Build a bundle with 8 calibration frames and 4 parity frames."""
    out = tmp_path / "models" / "kitchen-v1"
    return out, bundle.build_bundle(out, weights, inputs["det"], inputs["pose"], inputs["video"], 8, 4)


def test_stock_bundle_has_everything_the_pi_needs(tmp_path, inputs):
    out, manifest = build(tmp_path, inputs, "stock")

    assert (out / "det.onnx").read_bytes() == b"det weights"
    assert manifest["networks"]["det"]["onnx_sha256"] == bundle.sha256(inputs["det"])
    assert manifest["networks"]["det"]["hef_source"] == "zoo/yolov8s"
    assert manifest["networks"]["pose"]["hef_sha256"] == bundle.sha256(out / "pose.hef")
    assert manifest["networks"]["det"]["classes"] == 80
    assert manifest["hailo"] == {"model_zoo": "v2.18.0", "hailort": "4.23.0", "hw_arch": "hailo8"}
    assert len(list((out / "calib").glob("*.jpg"))) == 8
    assert json.loads((out / "manifest.json").read_text()) == manifest
    assert not (out / "compile.sh").exists()


def test_parity_frames_are_letterboxed_rgb_at_the_network_size(tmp_path, inputs):
    out, _ = build(tmp_path, inputs, "stock")
    frames = np.load(out / "parity" / "inputs.npy")

    assert frames.shape == (4, 640, 640, 3)
    assert frames.dtype == np.uint8
    # 160x120 scales to 640x480, centered: 80 gray rows above and below.
    assert (frames[:, :80] == bundle.LETTERBOX_GRAY).all()
    assert (frames[:, 560:] == bundle.LETTERBOX_GRAY).all()


def test_custom_weights_get_a_compile_script_and_no_hef(tmp_path, inputs):
    inputs["shapes"][inputs["det"]]["channels"] = 4 + 3
    out, manifest = build(tmp_path, inputs, "custom")

    script = (out / "compile.sh").read_text()
    assert "hailomz compile yolov8s --ckpt det.onnx --hw-arch hailo8 --calib-path calib/ --classes 3" in script
    assert manifest["networks"]["det"]["hef_sha256"] is None
    assert not (out / "det.hef").exists()


def test_stock_hefs_are_refused_for_a_custom_model(tmp_path, inputs):
    inputs["shapes"][inputs["det"]]["channels"] = 4 + 3

    with pytest.raises(SystemExit, match="not a stock COCO model"):
        build(tmp_path, inputs, "stock")
