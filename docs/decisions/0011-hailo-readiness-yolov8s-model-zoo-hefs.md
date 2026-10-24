# 0011: Vision targets the Hailo-8 with YOLOv8s det and pose at 640x640, using precompiled Model Zoo HEFs

- **Status:** proposed
- **Date:** 2026-10-23
- **Deciders:** director, mgr-vision, owner (installs the Pi software and runs the parity check)

## Context

The Hailo-8 isn't connected yet (OQ-11). Vision runs locally with ONNX Runtime and must stay ready to upload to the Hailo later ([`kitchen/vision/README.md`](../../kitchen/vision/README.md#local-now-hailo-later)). Issue #15 (vision-02) asks, without a device:

- which YOLOv8 detection and pose variants and which input size the Hailo Model Zoo supports for the Hailo-8;
- whether precompiled HEFs exist for them, or what our own compile with the Dataflow Compiler (DFC) would need (OQ-18);
- which HailoRT and Raspberry Pi OS versions the owner installs.

vision-01 (#14), vision-11 (#53) and vision-12 (#54) build on this answer. The assumed hardware is a Pi 5 with the AI HAT+ (Hailo-8, 26 TOPS) and Camera Module 3 (OQ-11).

Everything below was checked on 2026-10-23 against the sources at the end. Nothing was run on a Hailo or a Pi. Claims I could not check are marked **unverified**.

### What the Model Zoo offers for the Hailo-8

Hailo-8 support lives on the Model Zoo's **v2.x** line, paired with DFC v3.x. The `master` branch is for Hailo-10 and Hailo-15 only [1].

| Model Zoo name | Task | Input (HxWxC) | COCO mAP float / on Hailo | FPS batch 1 / batch 8 | Precompiled HEF |
|---|---|---|---|---|---|
| `yolov8n` | detection | 640x640x3 | 37.0 / 36.4 | 1036 / 1036 | yes |
| `yolov8s` | detection | 640x640x3 | 44.6 / 44.0 | 491 / 491 | yes |
| `yolov8m` | detection | 640x640x3 | 49.9 / 49.2 | 67.3 / 141 | yes |
| `yolov8l`, `yolov8x` | detection | 640x640x3 | 52.4, 53.5 (float) | 36.3, 18.0 (batch 1) | yes |
| `yolov8s_pose` | pose | 640x640x3 | 59.2 / 55.7 | 393 / 393 | yes |
| `yolov8m_pose` | pose | 640x640x3 | 64.3 / 62.1 | 68.7 / 145 | yes |

Sources: the Hailo-8 object detection and pose tables at Model Zoo v2.18 [2][3]. Later releases list nearly the same numbers. FPS was measured on an x86 host over PCIe Gen 3 x4. The Pi 5 has a single PCIe lane, so these are upper bounds, not Pi numbers. There is no `n`, `l` or `x` pose model for the Hailo-8. Every YOLOv8 entry uses a 640x640 input, and its source is the Ultralytics repository under AGPL-3.0 [4].

### Which Model Zoo release matches which HailoRT

A HEF must come from the DFC that pairs with the HailoRT on the device. Hailo's community support says the same, and mismatches fail both ways. A HEF from a newer DFC failed with "HEF version does not match" [9], and HailoRT 4.21 rejected an older HEF as "outdated and no longer supported" [10].

| Model Zoo | DFC | HailoRT | Source |
|---|---|---|---|
| v2.16 | 3.32.0 | 4.22.0 | README badges at the tag [1] |
| v2.17, v2.18 | 3.33.0 | 4.23.0 | README badges at the tags [1] |
| v2.19.x, v2.20.0 | 3.34.0 | 4.24.0 | README badges and v2.20.0 release notes [1] |

Hailo's own app installer agrees: on the Hailo-8 it picks Model Zoo v2.18.0 for HailoRT 4.23.x and v2.19.0 for 4.24.x [5].

### What the Pi gets from apt today

On Raspberry Pi OS Trixie (Debian 13, current image released 2026-10-06, kernel 6.18 [7]), `hailo-all` 5.1.1 pulls in [6]:

| Package | Version | What it is |
|---|---|---|
| `hailort` | 4.23.0 | runtime library and `hailortcli` |
| `hailort-pcie-driver` | 4.23.0 | PCIe driver and firmware |
| `python3-hailort` | 4.23.0-1 | Python API (`hailo_platform`); depends on Python >= 3.13 and < 3.14 |
| `hailo-tappas-core`, `python3-hailo-tappas` | 5.1.0 | GStreamer elements (we don't need them, but `hailo-all` installs them) |
| `rpicam-apps-hailo-postprocess` | 1.14.0-1 | camera demo post-processing |

The legacy Bookworm image only gets `hailo-all` 4.20.0 (HailoRT 4.20.0, Python 3.11) [6]. The Raspberry Pi AI documentation now asks for 64-bit Trixie [8].

So the Pi will run **HailoRT 4.23.0**, and the matching HEFs come from **Model Zoo v2.18.0**.

### What the precompiled HEFs output

This matters for `postprocess.py`, which is meant to be shared by both backends.

- **`yolov8s.hef` already contains NMS.** Its compile script ends with `nms_postprocess(..., meta_arch=yolov8, engine=cpu)`, so HailoRT runs NMS on the Pi's CPU. The output is per-class lists of boxes with scores: 80 classes, up to 100 boxes each. The baked-in defaults are score >= 0.2 and IoU 0.7 [11][12]. HailoRT 4.23's Python `InferModel` can change the score threshold, IoU threshold and max boxes per class at runtime [13].
- **`yolov8s_pose.hef` has no NMS.** It outputs the 9 raw head tensors, NHWC: for strides 8, 16 and 32, a 64-channel box distribution, a 1-channel person score and 51 channels of keypoints [14]. The compile script applies a sigmoid on the chip to three of these outputs. They are presumably the person scores, but the script names layers, not heads (**unverified**). The host does box decoding, NMS and keypoint decoding.
- **Both HEFs take uint8 RGB at 640x640.** The divide-by-255 runs on the chip [11][14], and the Model Zoo pads with value 114, the same as Ultralytics [15].
- **Our local ONNX export outputs decoded candidates before NMS.** That is `1x84x8400` for detection and `1x56x8400` for pose, with float32 NCHW input scaled to 0..1. See the smoke check below.

### Smoke check run for this ADR (laptop, no Hailo)

vision-01's ONNX path isn't built yet, so I checked the chosen variants in a scratch environment outside the repo:

- Weights came only from the official Ultralytics releases (`yolov8s.pt`, `yolov8s-pose.pt`, assets v8.4.0, sha256 matching the release digests [21]). They live in gitignored `data/weights/`, listed in a local `data/data-manifest.json`.
- Exported with Ultralytics 8.4.184: `imgsz=640`, static shape, batch 1, opset 18.
- Ran with ONNX Runtime 1.31.0 (CPU) on one synthetic 1280x720 OpenCV frame, letterboxed to 640 with pad 114:

| Weights | ONNX input | ONNX output | Model Zoo head nodes present in our export |
|---|---|---|---|
| `yolov8s.pt` | `images` [1, 3, 640, 640] float32 | `output0` [1, 84, 8400] | all 6 (`/model.22/cv2.N/cv2.N.2/Conv`, `/model.22/cv3.N/cv3.N.2/Conv`) |
| `yolov8s-pose.pt` | `images` [1, 3, 640, 640] float32 | `output0` [1, 56, 8400] | all 9 (`cv2`, `cv3`, `cv4` for N = 0, 1, 2), the exact nodes `yolov8s_pose.yaml` parses up to [14] |

## Decision

1. **Variants and input size.** Detection is **YOLOv8s** and pose is **YOLOv8s-pose**, both at a fixed **640x640** input, batch 1, letterboxed with pad value 114. The names line up like this, and vision-01, vision-11 and vision-12 use them exactly:

   | Role | Ultralytics weights (local ONNX) | Model Zoo name and HEF | Bundle folder |
   |---|---|---|---|
   | detection | `yolov8s.pt` | `yolov8s`, `yolov8s.hef` | `data/models/yolov8s/` |
   | pose | `yolov8s-pose.pt` | `yolov8s_pose`, `yolov8s_pose.hef` | `data/models/yolov8s_pose/` |

   Why `s`: pose only exists as `s` and `m` on the Hailo-8. `yolov8s_pose` runs at about 6x the FPS of `yolov8m_pose` on Hailo's bench, and its batch-1 FPS equals its batch-8 FPS. Two networks share the chip at 30 FPS, so that headroom matters. Hailo's own Hailo-8 demos default to the `m` variants, but each demo runs only one network [5]. `yolov8s` keeps detection in the same family and is much stronger than `n` (44.6 vs 37.0 mAP). That matters for small objects like forks and spoons.

2. **HEF source (answers OQ-18): precompiled Model Zoo HEFs**, from the Model Zoo release that pairs with the HailoRT on the Pi. For HailoRT 4.23.0 (Trixie `hailo-all` 5.1.1) that is **v2.18.0**:
   - `https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.18.0/hailo8/yolov8s.hef` (11,284,359 bytes)
   - `https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.18.0/hailo8/yolov8s_pose.hef` (10,592,016 bytes)

   Both URLs answered HTTP 200 on 2026-10-23. If the Pi ends up on HailoRT 4.24.x, use the v2.19.0 HEFs instead. The rule is in the pairing table above. Nobody downloads these until the owner sets up the Pi (vision-12 fetches them into the bundle and records their sha256).

3. **We don't compile until we need to.** That means fine-tuning (M3), other classes, or another input size. Precompiled HEFs need no x86 machine and no Hailo account. If we do compile, the requirements are in "If we compile ourselves" below.

4. **What `postprocess.py` shares, and the one exception.**
   - Shared by both backends: letterbox (uint8 RGB 640x640), mapping boxes back to normalized frame coordinates, the class map (COCO `fork`, `knife`, `spoon`, `bowl`, and pose wrists as keypoints 9 and 10), all score and IoU thresholds (from config), NMS, and pose keypoint decoding.
   - Each backend only adapts the input layout: float32 NCHW 0..1 for ONNX, uint8 NHWC for the HEF.
   - Pose: the Hailo backend hands the 9 raw head tensors to a shared `postprocess.py` decoder (box distribution to boxes) and then the same NMS. vision-11 can test that decoder with no device: export the same ONNX with the 9 head nodes above as extra outputs, and check that decoding them reproduces `output0`.
   - **Exception:** for detection, NMS runs inside HailoRT, because `yolov8s.hef` contains it. The Hailo backend sets HailoRT's NMS score threshold, IoU threshold and max boxes per class from the same config values the ONNX path uses. vision-12's parity script is what measures any difference.

5. **One device, two networks.** The Hailo backend opens one HailoRT `VDevice` and loads both HEFs as `InferModel`s. HailoRT's default scheduler for a `VDevice` is round robin [16], so it switches between the two networks.

6. **What the owner installs on the Pi** when the Hailo is connected (also posted on #15):
   1. Raspberry Pi OS (64-bit) **Trixie**, the current image (2026-10-06 or later). System Python is 3.13.
   2. `sudo apt update && sudo apt full-upgrade -y && sudo rpi-eeprom-update -a && sudo reboot`
   3. `sudo apt install dkms hailo-all`, then `sudo reboot`. Expect `hailo-all` 5.1.1 with `hailort`, `hailort-pcie-driver` and `python3-hailort` at 4.23.0. The AI HAT+ switches PCIe to Gen 3 on its own; only the older AI Kit needs `dtparam=pciex1_gen=3` [8].
   4. Hold the runtime so an upgrade can't break the HEF pairing: `sudo apt-mark hold hailort hailort-pcie-driver python3-hailort hailo-all`.
   5. Check it: `hailortcli fw-control identify` should show `Device Architecture: HAILO8`. `dmesg | grep -i hailo` should show the firmware loaded. `rpicam-hello` should show the camera.
   6. Make our venv with `python3 -m venv --system-site-packages .venv`. `hailo_platform` comes from the apt package and only exists for the system Python 3.13 [5][6].
   7. Copy the bundle (with the two v2.18.0 HEFs) to the Pi and run the parity script (vision-12).

## If we compile ourselves (not now)

From the Model Zoo v2.18 system requirements [17] and Hailo's support forum:

- **Machine:** x86-64 running Ubuntu 22.04 or 24.04, 64-bit (or Windows under WSL2). Not macOS, and not Apple Silicon [17][18].
- **Python:** 3.10, 3.11 or 3.12, with `pip` and `virtualenv` [17].
- **DFC:** **3.33.0**, to match HailoRT 4.23.0. Downloading it needs a Hailo Developer Zone login [17][19]. **Unverified:** whether a free registration is enough, or whether Hailo must approve the account.
- **GPU:** an NVIDIA Pascal, Turing or Ampere card with driver 525, CUDA 12.5.1 and cuDNN 9.10 [17]. Hailo recommends running the optimization step on a GPU machine with at least 1024 calibration images [20]. Ours would come from the owner's kitchen recordings.
- **RAM:** 16 GB minimum, 32 GB recommended [23]. **Unverified:** this comes from a third-party install guide, not Hailo's docs.
- **Recipe:** reuse the Model Zoo's `yolov8s` and `yolov8s_pose` configs (`hailomz compile <name> --ckpt <our.onnx> --hw-arch hailo8 --calib-path <frames>`, plus `--classes` if the class count changes) [17]. Our export has the same head node names the configs cut at. **Unverified:** that the DFC accepts our opset-18 export as is. It may need a lower opset.

## Consequences

- vision-01 exports exactly `yolov8s.pt` and `yolov8s-pose.pt` to static 640x640 ONNX. Changing a variant or the input size means revisiting this ADR, because precompiled HEFs only exist at 640x640.
- vision-11 uses the HailoRT 4.23 `InferModel` API through one `VDevice`. It sets the detection NMS thresholds from config and decodes pose heads in `postprocess.py`. Its mock runtime returns the shapes listed above.
- vision-12's `manifest.json` records the Model Zoo version (v2.18.0), the HailoRT version (4.23.0), and each HEF's URL and sha256. The parity script compares final detections, so it also covers the detection NMS exception.
- **The Pi runs Python 3.13**, because `python3-hailort` on Trixie only supports 3.13. AGENTS.md §8 says vision pins exactly what the Pi runs, so the `pi` extra and CI need a 3.13 job. That is platform's to change, and this PR doesn't touch it.
- **Watch for:** an apt upgrade that moves HailoRT off 4.23 (hence the hold). Re-fetch the HEFs from the paired Model Zoo release whenever HailoRT changes. If the owner's board turns out to be the AI HAT+ 2 (Hailo-10H), everything here changes: `hailo-h10-all`, HailoRT 5.x, and Model Zoo v5.x HEFs [8].
- **Licensing:** the Model Zoo lists these networks as AGPL-3.0 (Ultralytics), so OQ-16 covers the HEFs too [4].
- No new Python dependency and no code in this PR. Weights, ONNX and HEFs stay out of git.

### Still unverified

1. det + pose + ByteTrack at 30 FPS or more on a Pi 5 over one PCIe lane. Hailo's FPS figures are from an x86 host over PCIe Gen 3 x4, and the third-party Pi figures I found are not reproducible. The owner measures this in M2. Fallbacks, in order: pose on every Nth frame (flagged), then `yolov8n` for detection.
2. That the v2.18.0 HEFs load and run on the Pi's HailoRT 4.23.0. This is the documented pairing, but nobody has run it.
3. That Hailo's `yolov8s` weights match Ultralytics `yolov8s.pt`. They come from the same upstream repo, and the float mAPs are close (Hailo's eval says 44.6, Ultralytics' docs say 44.9 [22]), but the HEF can't be compared with our ONNX until the parity check runs.
4. The exact layout of the detection HEF's NMS output: the box coordinate order, and that class index i is COCO class i. vision-11 pins it in its mock fixtures, and the parity check confirms it.
5. That `yolov8s` and `yolov8s_pose` each fit the chip in a single context. I inferred this from batch-1 FPS equalling batch-8 FPS; the profiler reports were too large to fetch.

## Sources

1. Hailo Model Zoo: README compatibility notice and version badges at tags v2.16, v2.17, v2.18, v2.19.1 and v2.20.0, and the v2.20.0 release notes. <https://github.com/hailo-ai/hailo_model_zoo>, <https://github.com/hailo-ai/hailo_model_zoo/releases/tag/v2.20.0>
2. Hailo-8 object detection table (v2.18). <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/docs/public_models/HAILO8/HAILO8_object_detection.rst>
3. Hailo-8 pose estimation table (v2.18). <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/docs/public_models/HAILO8/HAILO8_pose_estimation.rst>
4. Model Zoo network config `yolov8s.yaml` (source, license, input and output shape). <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/hailo_model_zoo/cfg/networks/yolov8s.yaml>
5. hailo-apps `install.sh` (HailoRT to Model Zoo mapping), Raspberry Pi install guide (`--system-site-packages`), and `defines.py` (default models per device). <https://github.com/hailo-ai/hailo-apps/blob/main/install.sh>, <https://github.com/hailo-ai/hailo-apps/blob/main/doc/user_guide/installation.md>, <https://github.com/hailo-ai/hailo-apps/blob/main/hailo_apps/python/core/common/defines.py>
6. Raspberry Pi apt indexes, read 2026-10-23. <http://archive.raspberrypi.com/debian/dists/trixie/main/binary-arm64/Packages.gz>, <http://archive.raspberrypi.com/debian/dists/bookworm/main/binary-arm64/Packages.gz>
7. Raspberry Pi OS downloads page. <https://www.raspberrypi.com/software/operating-systems/>
8. Raspberry Pi AI documentation (AI Kit, AI HAT+, AI HAT+ 2). <https://www.raspberrypi.com/documentation/computers/ai.html>
9. Hailo community, "HEF version does not match" (staff: compile with the DFC that matches your HailoRT). <https://community.hailo.ai/t/hef-version-does-not-match/13704>
10. Hailo community, "Hef has periph_calculation_in_hailort feature disabled" (HailoRT 4.21 rejects an outdated HEF). <https://community.hailo.ai/t/hef-has-periph-calculation-in-hailort-feature-disabled/14875>
11. Model Zoo compile script `yolov8s.alls`. <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/hailo_model_zoo/cfg/alls/generic/yolov8s.alls>
12. Model Zoo NMS config `yolov8s_nms_config.json`. <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/hailo_model_zoo/cfg/postprocess_config/yolov8s_nms_config.json>
13. HailoRT 4.23.0 Python bindings, `infer_model_api.cpp` (`set_nms_score_threshold`, `set_nms_iou_threshold`, `set_nms_max_proposals_per_class`). <https://github.com/hailo-ai/hailort/blob/v4.23.0/hailort/libhailort/bindings/python/src/infer_model_api.cpp>
14. Model Zoo `yolov8s_pose.yaml` (parser end nodes, output shapes) and `yolov8s_pose.alls`. <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/hailo_model_zoo/cfg/networks/yolov8s_pose.yaml>, <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/hailo_model_zoo/cfg/alls/generic/yolov8s_pose.alls>
15. Model Zoo base config `yolo.yaml` (640x640 input, `padding_color: 114`, normalization). <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/hailo_model_zoo/cfg/base/yolo.yaml>
16. HailoRT 4.23.0 `hailort_defaults.cpp` (default `VDevice` scheduling is round robin). <https://github.com/hailo-ai/hailort/blob/v4.23.0/hailort/libhailort/src/hailort_defaults.cpp>
17. Model Zoo v2.18 Getting Started (system requirements, `hailomz compile`). <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/docs/GETTING_STARTED.rst>
18. Hailo community, "Can't compile from onnx to hef on Mac m1". <https://community.hailo.ai/t/cant-compile-from-onnx-to-hef-on-mac-m1/12061>
19. Hailo Developer Zone documentation and downloads (login for most guides). <https://hailo.ai/developer-zone/>
20. Model Zoo v2.18 Optimization guide (GPU, at least 1024 calibration images). <https://github.com/hailo-ai/hailo_model_zoo/blob/v2.18/docs/OPTIMIZATION.rst>
21. Ultralytics release assets v8.4.0 (`yolov8s.pt`, `yolov8s-pose.pt` with sha256 digests). <https://github.com/ultralytics/assets/releases/tag/v8.4.0>
22. Ultralytics YOLOv8 docs (YOLOv8s COCO mAP 50-95 = 44.9). <https://docs.ultralytics.com/models/yolov8/>
23. RidgeRun, "Hailo AI Software Suite Installation" (third-party; RAM figures). <https://developer.ridgerun.com/wiki/index.php/Hailo/Hailo-8/AI_Software_and_Tools/Hailo_AI_Software_Suite_Installation>
