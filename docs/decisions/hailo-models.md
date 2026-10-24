# Vision uses YOLOv8s detection and pose with precompiled Hailo Model Zoo files

- **Status:** accepted
- **Date:** 2026-10-23
- **Decided by:** director, vision manager, owner (installs the Pi software and runs the parity check)

## Context

The Hailo-8 isn't connected yet. Vision runs locally with ONNX Runtime and must stay ready to upload. We needed to know, without a device, which YOLOv8 variants the Hailo Model Zoo supports, whether precompiled HEF files exist, and which runtime versions the Pi needs. Assumed hardware: Pi 5 with the AI HAT+ (Hailo-8) and Camera Module 3.

## Decision

- **Models.** Detection is `yolov8s.pt` and pose is `yolov8s-pose.pt`, both at a fixed 640×640 input, batch 1. Pose only exists as `s` and `m` on the Hailo-8, and `s` leaves room for two networks at 30 FPS.
- **HEFs: precompiled, not compiled by us.** Use the Model Zoo release that pairs with the Pi's HailoRT: v2.18.0 for HailoRT 4.23.0 (v2.19.0 if the Pi ends up on 4.24.x). Compile ourselves only if we fine-tune, change classes or change input size; that needs an x86-64 Linux machine with a GPU, not the Mac.
- **One processing path.** Letterbox, box mapping, class map (COCO `fork`, `knife`, `spoon`, `bowl`; wrists are pose keypoints 9 and 10), thresholds and NMS are shared by both backends in `detect.py`. Two HEF differences are absorbed in `hailo.py`, so `detect.py` sees the ONNX output either way: the detection HEF runs NMS inside HailoRT (its score floor sits below `detect.py`'s threshold), and the pose HEF returns raw heads that are decoded on the host.
- **One device, two networks.** The Hailo backend opens one HailoRT `VDevice` and loads both HEFs.
- **What the owner installs on the Pi:** Raspberry Pi OS (64-bit) Trixie, then `sudo apt install dkms hailo-all` (expect `hailo-all` and HailoRT 4.23.0; `hailo-tappas-core` is 5.1.0), then `sudo apt-mark hold hailort hailort-pcie-driver python3-hailort hailo-all` so an upgrade can't break the pairing. Check with `hailortcli fw-control identify`. Make the venv with `--system-site-packages`, because `hailo_platform` only exists for the system Python 3.13. `kitchen/vision/pi_setup.sh` does all of this.

## Consequences

- The Pi runs Python 3.13, so vision pins 3.13.
- The bundle's `manifest.json` records the Model Zoo version, the HailoRT version, and each HEF's URL and sha256.
- Unverified until the device is connected: det + pose + ByteTrack at 30 FPS on the Pi's single PCIe lane (fallbacks: pose every Nth frame, then `yolov8n` for detection), and that the HEFs match our ONNX (the parity check measures it).
- The networks are AGPL-3.0 (Ultralytics), which is fine for a non-distributed demo.
