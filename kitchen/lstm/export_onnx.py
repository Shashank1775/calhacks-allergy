"""Export a trained interaction model to ONNX, check it against torch, and record it in data-manifest.json.

Owner: partner (written by an agent at the owner's request)
Step:  LSTM classifier
Reads: a checkpoint from kitchen/lstm/train.py (state dict, and args["model"] naming its class in model.py); optionally
       an example clip npz (keys x and dt_ms)
Emits: model.onnx with inputs clip (float32 [batch, steps, 3, 128, 128], 0 to 1) and dt_ms (float32 [batch, steps]) and
       output logits ([batch, 2]: contact, material moved); its entry in data-manifest.json in the same folder
Rules: 6 the model's only inputs are the box masks, the change mask and elapsed time; 7 exporting adopts nothing: zone
       rules stay the default until the benchmark says otherwise

The loop over steps stays a loop (torch.jit.script, then the TorchScript exporter), so the ONNX model takes any number
of steps, as the network does. Channels and frame size are fixed by the clip geometry. Torch isn't a project dependency,
so pull it and onnx in for this one command (tested with torch 2.14.1, whose TorchScript exporter is deprecated
but works, and onnx 1.23.2):

    uv run --with torch==2.14.1 --with onnx==1.23.2 python -m kitchen.lstm.export_onnx \
        --checkpoint data/models/lstm/boxpooled-v1/best.pt --out data/models/lstm/boxpooled-v1/model.onnx \
        --example data/examples/example_clip.npz
"""

import argparse
import json
from pathlib import Path

import numpy as np
import onnxruntime
import torch

from kitchen.lstm import model as models
from kitchen.lstm.infer import file_sha256, sigmoid

CLIP_CHANNELS = 3  # box_a, box_b, change (the clip geometry in AGENTS.md, Open questions)
CLIP_SIZE = 128
CLIP_STEPS = 16  # only the example input's length: the exported model takes any number of steps
OPSET = 17
PARITY_TOLERANCE = 1e-3  # largest allowed |torch logit - ONNX logit|
RANDOM_CLIP_STEPS = (2, 12, 16, 24)  # random clips of these lengths exercise the ONNX Loop beyond the trained 16


def load_model(checkpoint_path: Path) -> torch.nn.Module:
    """Rebuild the trained network from a train.py checkpoint, in eval mode."""
    # train.py stores its settings (with Path values) next to the weights, which the weights-only loader refuses.
    # Only load checkpoints you trained yourself.
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    network = getattr(models, checkpoint["args"]["model"])()
    network.load_state_dict(checkpoint["model"])
    return network.eval()


def export(network: torch.nn.Module, out_path: Path) -> None:
    """Write the network as ONNX, with batch and step count left free."""
    example_clip = torch.zeros(1, CLIP_STEPS, CLIP_CHANNELS, CLIP_SIZE, CLIP_SIZE)
    example_dt_ms = torch.full((1, CLIP_STEPS), 62.0)
    torch.onnx.export(
        torch.jit.script(network),  # scripting keeps the step loop as a Loop; tracing would unroll it to 16 steps
        (example_clip, example_dt_ms),
        str(out_path),
        dynamo=False,
        input_names=["clip", "dt_ms"],
        output_names=["logits"],
        dynamic_axes={"clip": {0: "batch", 1: "steps"}, "dt_ms": {0: "batch", 1: "steps"}, "logits": {0: "batch"}},
        opset_version=OPSET,
    )


def parity_clips(example_path: Path | None, seed: int) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """(name, uint8 [T, C, H, W] clip, dt_ms [T]) to compare on: the example clip if given, plus random clips."""
    clips = []
    if example_path is not None:
        with np.load(example_path) as example:
            clips.append((example_path.name, example["x"], example["dt_ms"]))

    rng = np.random.default_rng(seed)
    for steps in RANDOM_CLIP_STEPS:
        clip = rng.integers(0, 256, size=(steps, CLIP_CHANNELS, CLIP_SIZE, CLIP_SIZE), dtype=np.uint8)
        dt_ms = np.concatenate([[0], rng.integers(30, 90, size=steps - 1)])
        clips.append((f"random T={steps}", clip, dt_ms))
    return clips


def check_parity(network: torch.nn.Module, onnx_path: Path, clips: list) -> float:
    """Print torch and ONNX logits for each clip, and return the largest absolute difference."""
    session = onnxruntime.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    worst = 0.0
    for name, clip, dt_ms in clips:
        x = clip[None].astype(np.float32) / 255  # the same scaling as train.py and infer.py
        dt = dt_ms[None].astype(np.float32)
        with torch.no_grad():
            expected = network(torch.from_numpy(x), torch.from_numpy(dt)).numpy()
        got = session.run(["logits"], {"clip": x, "dt_ms": dt})[0]
        difference = float(np.abs(expected - got).max())
        worst = max(worst, difference)
        print(f"{name:>20}: torch {expected[0]}  onnx {got[0]}  p {sigmoid(got[0])}  max |diff| {difference:.2e}")
    return worst


def record_in_manifest(onnx_path: Path, checkpoint_path: Path) -> dict:
    """Add (or replace) the ONNX file's entry in data-manifest.json next to it, and return the entry."""
    manifest_path = onnx_path.parent / "data-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": []}
    entry = {
        "name": onnx_path.name,
        "size": onnx_path.stat().st_size,
        "sha256": file_sha256(onnx_path),
        "source": (
            f"kitchen/lstm/export_onnx.py from {checkpoint_path.name} (sha256 {file_sha256(checkpoint_path)}), "
            f"torch {torch.__version__}, opset {OPSET}"
        ),
    }
    manifest["files"] = [item for item in manifest["files"] if item["name"] != onnx_path.name] + [entry]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return entry


def parse_args() -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Export a train.py checkpoint to ONNX for kitchen/lstm/infer.py.")
    parser.add_argument("--checkpoint", type=Path, required=True, help="best.pt from kitchen/lstm/train.py")
    parser.add_argument("--out", type=Path, required=True, help="where to write model.onnx")
    parser.add_argument("--example", type=Path, default=None, help="an example clip npz to include in the check")
    parser.add_argument("--seed", type=int, default=0, help="seed for the random parity clips")
    return parser.parse_args()


def main() -> None:
    """Export, refuse an ONNX file that disagrees with torch, then record it in the manifest."""
    args = parse_args()
    network = load_model(args.checkpoint)
    export(network, args.out)

    worst = check_parity(network, args.out, parity_clips(args.example, args.seed))
    print(f"parity: max |torch - onnx| logit difference {worst:.2e} (tolerance {PARITY_TOLERANCE})")
    if worst > PARITY_TOLERANCE:
        args.out.unlink()  # never leave a model behind that doesn't match the trained network
        raise SystemExit("ONNX disagrees with torch; deleted it")

    entry = record_in_manifest(args.out, args.checkpoint)
    print(f"wrote {args.out} ({entry['size']} bytes, sha256 {entry['sha256']})")


if __name__ == "__main__":
    main()
