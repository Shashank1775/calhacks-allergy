"""Tests for the deploy bundle builder, with ONNX inspection and the Model Zoo download stubbed out.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.bundle; a tiny synthetic video written by the test
Emits: nothing
Rules: the bundle's ONNX files are byte-identical to the inputs; stock HEFs only for stock-shaped models; a custom
       bundle names its detector's classes, one per output class, and a stock one names none
"""

import json

import cv2
import numpy as np
import pytest

from kitchen.vision import bundle
from kitchen.vision.detect import detector_classes, read_bundle_manifest

STOCK = {
    "det": {"height": 640, "width": 640, "channels": 84},
    "pose": {"height": 640, "width": 640, "channels": 56},
}

# The fine-tuned detector's classes, in its output order.
CUSTOM_NAMES = ["plate", "bowl", "knife", "spoon", "fork"]


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


def build(tmp_path, inputs, weights, class_names=None):
    """Build a bundle with 8 calibration frames and 4 parity frames."""
    out = tmp_path / "models" / "kitchen-v1"
    return out, bundle.build_bundle(out, weights, inputs["det"], inputs["pose"], inputs["video"], 8, 4, class_names)


def test_stock_bundle_has_everything_the_pi_needs(tmp_path, inputs):
    out, manifest = build(tmp_path, inputs, "stock")

    assert (out / "det.onnx").read_bytes() == b"det weights"
    assert manifest["networks"]["det"]["onnx_sha256"] == bundle.sha256(inputs["det"])
    assert manifest["networks"]["det"]["hef_source"] == "zoo/yolov8s"
    assert manifest["networks"]["pose"]["hef_sha256"] == bundle.sha256(out / "pose.hef")
    assert manifest["networks"]["det"]["classes"] == 80
    assert manifest["hailo"] == {"model_zoo": "v2.18.0", "hailort": "4.23.0", "hw_arch": "hailo8l"}
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
    out, manifest = build(tmp_path, inputs, "custom", ["plate", "knife", "fork"])

    script = (out / "compile.sh").read_text()
    assert "hailomz compile yolov8s --ckpt det.onnx --hw-arch hailo8l --calib-path calib/ --classes 3" in script
    assert manifest["networks"]["det"]["hef_sha256"] is None
    assert not (out / "det.hef").exists()


def test_stock_hefs_are_refused_for_a_custom_model(tmp_path, inputs):
    inputs["shapes"][inputs["det"]]["channels"] = 4 + 3

    with pytest.raises(SystemExit, match="not a stock COCO model"):
        build(tmp_path, inputs, "stock")


def test_custom_class_names_round_trip_from_the_command_line_to_the_detector(tmp_path, inputs):
    inputs["shapes"][inputs["det"]]["channels"] = 4 + len(CUSTOM_NAMES)
    inputs["shapes"][inputs["det"]]["class_names"] = CUSTOM_NAMES  # what Ultralytics' export writes in the metadata
    args = ["--name", "kitchen-v1", "--weights", "custom", "--det-onnx", str(inputs["det"])]
    args += [
        "--pose-onnx",
        str(inputs["pose"]),
        "--video",
        str(inputs["video"]),
        "--out-root",
        str(tmp_path / "models"),
    ]
    args += ["--calib-frames", "8", "--parity-frames", "4", "--class-names", "plate, bowl,knife,spoon,fork"]

    assert bundle.main(args) == 0

    out = tmp_path / "models" / "kitchen-v1"
    det = read_bundle_manifest(out)["networks"]["det"]
    assert det["class_names"] == CUSTOM_NAMES
    assert det["classes"] == 5
    assert detector_classes(det).tracked == dict(enumerate(CUSTOM_NAMES))
    assert "--classes 5" in (out / "compile.sh").read_text()


@pytest.mark.safety
def test_custom_weights_need_one_class_name_per_detector_class(tmp_path, inputs):
    inputs["shapes"][inputs["det"]]["channels"] = 4 + len(CUSTOM_NAMES)

    with pytest.raises(SystemExit, match="--class-names"):
        build(tmp_path, inputs, "custom")
    with pytest.raises(SystemExit, match="det.onnx scores 5 classes, but --class-names names 4"):
        build(tmp_path, inputs, "custom", CUSTOM_NAMES[:4])
    assert not (tmp_path / "models").exists()  # refused before anything is written


@pytest.mark.safety
def test_class_names_in_another_order_than_the_onnx_metadata_are_refused(tmp_path, inputs):
    """Names in the wrong order read one class as another: here every plate as a knife, and every knife as a plate."""
    assert bundle.metadata_class_names("{0: 'plate', 1: 'bowl', 2: 'knife', 3: 'spoon', 4: 'fork'}") == CUSTOM_NAMES
    inputs["shapes"][inputs["det"]]["channels"] = 4 + len(CUSTOM_NAMES)
    inputs["shapes"][inputs["det"]]["class_names"] = CUSTOM_NAMES
    swapped = ["knife", "bowl", "plate", "spoon", "fork"]

    with pytest.raises(SystemExit, match="metadata names its classes"):
        build(tmp_path, inputs, "custom", swapped)
    assert not (tmp_path / "models").exists()


def test_stock_bundles_name_no_classes(tmp_path, inputs):
    with pytest.raises(SystemExit, match="only for --weights custom"):
        build(tmp_path, inputs, "stock", CUSTOM_NAMES)

    _, manifest = build(tmp_path, inputs, "stock")
    assert "class_names" not in manifest["networks"]["det"]
