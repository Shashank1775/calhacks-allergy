"""Builds the Hailo deploy bundle on the laptop: one folder in data/models/<name>/ to copy to the Pi.

Owner: vision (agents)
Step:  vision
Reads: the detection and pose ONNX files the local backend runs; a kitchen recording (video) for calibration frames
Emits: data/models/<name>/ with det.onnx and pose.onnx (byte-identical copies), calib/*.jpg, parity/inputs.npy,
       manifest.json, and either det.hef and pose.hef (stock COCO weights: Hailo Model Zoo's precompiled files) or
       compile.sh, which builds them from custom weights on an x86-64 Linux machine with Hailo's Dataflow Compiler
Rules: the bundle's ONNX files are the exact files the local backend loads (sha256 in the manifest); stock HEFs are
       only used with stock-shaped ONNX files (80 classes, 17 keypoints, 640x640); nothing here goes in git (data/
       is ignored)

Usage:
  uv run python -m kitchen.vision.bundle --name kitchen-v1 --weights stock \
      --det-onnx yolov8s.onnx --pose-onnx yolov8s-pose.onnx --video data/recordings/prep.mp4
"""

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import onnxruntime

# The Model Zoo release that pairs with HailoRT 4.23 on Raspberry Pi OS Trixie (docs/decisions/hailo-models.md).
MODEL_ZOO_VERSION = "v2.18.0"
HAILORT_VERSION = "4.23.0"
HW_ARCH = "hailo8"
ZOO_URL = "https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/{version}/{arch}/{model}.hef"

# Model Zoo names for the two networks. Change both if the partner trains another size (yolov8n, yolov8m).
ZOO_MODELS = {"det": "yolov8s", "pose": "yolov8s_pose"}

STOCK_INPUT = 640
COCO_CLASSES = 80
LETTERBOX_GRAY = 114  # Ultralytics' padding color

COMPILE_SCRIPT = """#!/usr/bin/env bash
# Compiles this bundle's custom ONNX files into Hailo-8 HEFs. Run it from this folder on an x86-64 Linux machine with
# Hailo's Dataflow Compiler and Model Zoo installed (the Hailo AI Software Suite, free from the Hailo Developer Zone).
# It does not run on the Mac or the Pi. A GPU makes the optimization step much faster.
# Export the ONNX files with opset 11 (yolo export format=onnx imgsz=640 opset=11) so the Model Zoo recipes match.
set -euo pipefail
cd "$(dirname "$0")"
hailomz compile {det_model} --ckpt det.onnx --hw-arch {arch} --calib-path calib/ --classes {classes}
mv {det_model}.hef det.hef
hailomz compile {pose_model} --ckpt pose.onnx --hw-arch {arch} --calib-path calib/
mv {pose_model}.hef pose.hef
sha256sum det.hef pose.hef
"""


def sha256(path: Path) -> str:
    """Hex sha256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_onnx(path: Path) -> dict:
    """The input size and output channels of an Ultralytics ONNX file, read with ONNX Runtime."""
    session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    _, _, height, width = session.get_inputs()[0].shape
    return {"height": height, "width": width, "channels": session.get_outputs()[0].shape[1]}


def check_stock(det: dict, pose: dict) -> None:
    """Refuse stock HEFs for ONNX files that can't be the stock COCO models, since the HEF would run other weights."""
    for name, info, channels in (("det", det, 4 + COCO_CLASSES), ("pose", pose, 56)):
        if (info["height"], info["width"], info["channels"]) != (STOCK_INPUT, STOCK_INPUT, channels):
            raise SystemExit(
                f"{name}.onnx is not a stock COCO model (input {info['height']}x{info['width']}, "
                f"{info['channels']} output channels); use --weights custom and compile.sh"
            )


def letterbox(frame: np.ndarray, height: int, width: int) -> np.ndarray:
    """Resize a BGR frame into height x width keeping its aspect ratio, pad with gray, and return it as RGB.

    Only for calibration and parity frames: both backends see the same frames, so this needn't match detect.py.
    """
    scale = min(height / frame.shape[0], width / frame.shape[1])
    new_h, new_w = round(frame.shape[0] * scale), round(frame.shape[1] * scale)
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    out = np.full((height, width, 3), LETTERBOX_GRAY, dtype=np.uint8)
    top, left = (height - new_h) // 2, (width - new_w) // 2
    out[top : top + new_h, left : left + new_w] = resized
    return out[..., ::-1].copy()


def sample_frames(video: Path, count: int, height: int, width: int) -> np.ndarray:
    """`count` frames spread evenly over a video, letterboxed to the network input, as uint8 RGB [N, H, W, 3]."""
    capture = cv2.VideoCapture(str(video))
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        raise SystemExit(f"can't read frames from {video}")
    wanted = set(np.linspace(0, total - 1, min(count, total)).round().astype(int).tolist())
    frames = []
    index = 0
    ok, frame = capture.read()
    while ok:
        if index in wanted:
            frames.append(letterbox(frame, height, width))
        index += 1
        ok, frame = capture.read()
    capture.release()
    return np.stack(frames)


def fetch_zoo_hef(model: str, dest: Path) -> str:
    """Download a precompiled Model Zoo HEF to dest and return its URL."""
    url = ZOO_URL.format(version=MODEL_ZOO_VERSION, arch=HW_ARCH, model=model)
    urllib.request.urlretrieve(url, dest)
    return url


def build_bundle(
    out: Path, weights: str, det_onnx: Path, pose_onnx: Path, video: Path, calib_count: int, parity_count: int
) -> dict:
    """Write the bundle folder and return its manifest. See the module docstring for what goes in it."""
    det, pose = inspect_onnx(det_onnx), inspect_onnx(pose_onnx)
    if weights == "stock":
        check_stock(det, pose)
    if (det["height"], det["width"]) != (pose["height"], pose["width"]):
        raise SystemExit("det.onnx and pose.onnx must take the same input size")
    height, width = det["height"], det["width"]

    out.mkdir(parents=True, exist_ok=True)
    networks = {}
    for name, source in (("det", det_onnx), ("pose", pose_onnx)):
        shutil.copyfile(source, out / f"{name}.onnx")
        networks[name] = {"onnx": f"{name}.onnx", "onnx_sha256": sha256(out / f"{name}.onnx"), "hef": f"{name}.hef"}
    networks["det"]["classes"] = det["channels"] - 4

    frames = sample_frames(video, calib_count, height, width)
    (out / "calib").mkdir(exist_ok=True)
    for i, frame in enumerate(frames):
        cv2.imwrite(str(out / "calib" / f"{i:04d}.jpg"), frame[..., ::-1])
    (out / "parity").mkdir(exist_ok=True)
    np.save(out / "parity" / "inputs.npy", frames[:parity_count])

    for name, network in networks.items():
        if weights == "stock":
            network["hef_source"] = fetch_zoo_hef(ZOO_MODELS[name], out / network["hef"])
            network["hef_sha256"] = sha256(out / network["hef"])
        else:
            network["hef_source"] = "compile.sh"
            network["hef_sha256"] = None
    if weights == "custom":
        script = COMPILE_SCRIPT.format(
            det_model=ZOO_MODELS["det"], pose_model=ZOO_MODELS["pose"], arch=HW_ARCH, classes=networks["det"]["classes"]
        )
        (out / "compile.sh").write_text(script)
        (out / "compile.sh").chmod(0o755)

    manifest = {
        "name": out.name,
        "weights": weights,
        "input": {"height": height, "width": width},
        "hailo": {"model_zoo": MODEL_ZOO_VERSION, "hailort": HAILORT_VERSION, "hw_arch": HW_ARCH},
        "networks": networks,
        "calib": {"dir": "calib", "frames": len(frames), "video": video.name, "video_sha256": sha256(video)},
        "parity": {"inputs": "parity/inputs.npy", "frames": min(parity_count, len(frames))},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; see the module docstring."""
    parser = argparse.ArgumentParser(description="Build the Hailo deploy bundle in data/models/<name>/.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--weights", choices=["stock", "custom"], required=True)
    parser.add_argument("--det-onnx", type=Path, required=True)
    parser.add_argument("--pose-onnx", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=Path("data/models"))
    parser.add_argument("--calib-frames", type=int, default=256)
    parser.add_argument("--parity-frames", type=int, default=32)
    args = parser.parse_args(argv)
    manifest = build_bundle(
        args.out_root / args.name,
        args.weights,
        args.det_onnx,
        args.pose_onnx,
        args.video,
        args.calib_frames,
        args.parity_frames,
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
