#!/usr/bin/env bash
# Sets up the Raspberry Pi 5 (Raspberry Pi OS 64-bit, Trixie) to run vision on the Hailo-8 AI HAT+, then checks it.
#
# Owner: vision (agents). The owner runs it on the Pi, from the repo root:
#   bash kitchen/vision/pi_setup.sh install                       # once, then reboot
#   bash kitchen/vision/pi_setup.sh check data/models/<name>      # after the reboot, with the bundle copied over
#
# Versions are pinned by docs/decisions/hailo-models.md: hailo-all brings HailoRT 4.23.0, which pairs with the
# Hailo Model Zoo v2.18.0 HEFs in the bundle.
set -euo pipefail

HAILO_PACKAGES="hailo-all hailort hailort-pcie-driver python3-hailort"

install() {
  sudo apt update
  sudo apt full-upgrade -y
  # dkms first, or the PCIe driver isn't built for the running kernel.
  sudo apt install -y dkms
  sudo apt install -y hailo-all
  # Hold the HailoRT packages so an upgrade can't break the pairing with the bundle's HEFs.
  for package in $HAILO_PACKAGES; do
    if dpkg -s "$package" >/dev/null 2>&1; then
      sudo apt-mark hold "$package"
    fi
  done

  # hailo_platform (and picamera2) exist only for the system Python 3.13, so the venv sees system packages.
  # pip, not `uv sync`: uv follows .python-version and would swap this venv for one without hailo_platform.
  python3 -m venv --system-site-packages .venv
  # The server extra (FastAPI, uvicorn) runs the Vision API and the local runner's live mode; the ml extra brings
  # OpenCV (frames, the MJPEG stream) and ONNX Runtime (the parity check's reference).
  .venv/bin/pip install -e '.[server,ml]'
  echo "Installed. Reboot (sudo reboot), copy the bundle to data/models/<name>, then:"
  echo "  bash kitchen/vision/pi_setup.sh check data/models/<name>"
}

check() {
  local bundle="$1"
  hailortcli fw-control identify
  .venv/bin/python -c "import hailo_platform; print('hailo_platform', hailo_platform.__version__)"
  # Versions, output shapes, top score on the first parity frame, and FPS for det, pose and both (need >= 30).
  .venv/bin/python -m kitchen.vision.hailo --bundle "$bundle"
  # Detections for the parity frames; copy parity/hailo.npz back to the laptop and run `parity compare` there.
  .venv/bin/python -m kitchen.vision.parity run --bundle "$bundle" --backend hailo
}

case "${1:-}" in
  install) install ;;
  check) check "${2:?usage: pi_setup.sh check data/models/<name>}" ;;
  *)
    echo "usage: bash kitchen/vision/pi_setup.sh install | check data/models/<name>"
    exit 2
    ;;
esac
