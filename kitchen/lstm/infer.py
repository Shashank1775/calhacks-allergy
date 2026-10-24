"""The ConvGRU interaction model at run time: an InteractionClassifier named "lstm" over the exported ONNX file.

Owner: partner (written by an agent at the owner's request)
Step:  LSTM classifier
Reads: PairWindow.tensor (uint8 [T, 3, H, W]: box_a, box_b, change) and the clip's dt_ms and channel order, nothing
       else; <model dir>/model.onnx and its data-manifest.json entry (LSTM_MODEL_DIR, default
       data/models/lstm/boxpooled-v1; export_onnx.py writes both)
Emits: [("transfer.contact", P(contact)), ("transfer.scoop", P(material moved))]
Rules: 6 blind classifier: the model sees box masks, the change mask and elapsed time only, never ids, features, object
       kinds or allergen data; 7 rules stay the fallback: anything wrong (a missing or corrupt model, a window it wasn't
       built for, a non-finite output) raises, and kitchen/interactions/classifier.py then uses zone rules. Nothing here
       makes the LSTM the default; adoption is the benchmark's call

Runs with onnxruntime on the CPU (the ml extra): about 50 ms per 16-step clip on an idle laptop. A call's budget is the
interactions wrapper's primary timeout (primary_timeout_ms, 100 ms): a slower call is skipped for zone rules, so the
LSTM labels nothing. The Pi is slower than a laptop, so time it there before adoption.
"""

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import onnxruntime

from kitchen.shared.interfaces import PairWindow
from kitchen.shared.models import Action

DEFAULT_MODEL_DIR = Path("data/models/lstm/boxpooled-v1")
MODEL_FILE = "model.onnx"
CHANNELS = ["box_a", "box_b", "change"]  # the channel order the model was trained on
MIN_STEPS = 2  # a clip has at least two steps (contracts: interaction_clip, dt_ms)


class LstmModelError(RuntimeError):
    """The LSTM model can't be used: missing, not matching its manifest, or not loadable by onnxruntime."""


def model_dir_from_env() -> Path:
    """The model folder: LSTM_MODEL_DIR if set, else data/models/lstm/boxpooled-v1 under the working directory."""
    return Path(os.environ.get("LSTM_MODEL_DIR") or DEFAULT_MODEL_DIR)


def file_sha256(path: Path) -> str:
    """The file's sha256 in hex, as data-manifest.json records it."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_sha256(model_dir: Path) -> str:
    """The sha256 that the folder's data-manifest.json records for model.onnx."""
    manifest_path = model_dir / "data-manifest.json"
    try:
        entries = json.loads(manifest_path.read_text())["files"]
        return next(entry["sha256"] for entry in entries if entry["name"] == MODEL_FILE)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise LstmModelError(
            f"{manifest_path} has no usable {MODEL_FILE} entry ({error!r}); run export_onnx.py"
        ) from error


def model_inputs(window: PairWindow, frame_shape: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    """The ONNX model's two inputs for one window: clip float32 [1, T, C, H, W] in [0, 1], and dt_ms float32 [1, T].

    Only the tensor, its channel order and the step times are read (safety rule: blind classifier). frame_shape is the
    (C, H, W) the model was exported for. Anything else raises ValueError, so the pair goes to zone rules instead of
    getting a score from an input the model never learned.
    """
    tensor = window.tensor
    if not isinstance(tensor, np.ndarray) or tensor.dtype != np.uint8:
        # A float tensor already scaled to [0, 1] would be divided by 255 again and look like an empty clip.
        raise ValueError(f"clip tensor must be a uint8 array, got {getattr(tensor, 'dtype', type(tensor))}")
    if tensor.ndim != 4 or tensor.shape[1:] != frame_shape:
        raise ValueError(f"clip tensor is {tensor.shape}, expected [T, {', '.join(map(str, frame_shape))}]")
    if list(window.clip["channels"]) != CHANNELS:
        raise ValueError(f"clip channels are {window.clip['channels']}, the model needs {CHANNELS}")

    steps = tensor.shape[0]
    if steps < MIN_STEPS:
        raise ValueError(f"clip has {steps} steps, needs at least {MIN_STEPS}")
    dt_ms = np.asarray(window.clip["dt_ms"], dtype=np.float32)
    if dt_ms.shape != (steps,):
        raise ValueError(f"clip has {steps} steps but {dt_ms.size} dt_ms values")

    clip = tensor[None].astype(np.float32) / 255  # the same scaling as training (train.py)
    return clip, dt_ms[None]


def sigmoid(logits: np.ndarray) -> np.ndarray:
    """Probabilities from logits, via logaddexp so a very large or small logit can't overflow."""
    return np.exp(-np.logaddexp(0.0, -logits))


class LstmClassifier:
    """InteractionClassifier "lstm": the exported ConvGRU scoring one candidate pair's clip.

    Its two outputs map onto actions as contracts/apis.md says: P(contact) to transfer.contact, P(material moved) to
    transfer.scoop. A contact without visible movement still comes back as a transfer.contact probability, so the
    graph can count it as a possible trace transfer.
    """

    name = "lstm"

    def __init__(self, model_dir: Path | str | None = None) -> None:
        """Load model.onnx from model_dir (default model_dir_from_env()) after checking it against its manifest.

        Raises LstmModelError when the file is missing, doesn't match data-manifest.json (corrupt or swapped), or
        onnxruntime can't load it, so the interactions wrapper keeps using zone rules.
        """
        model_dir = Path(model_dir) if model_dir is not None else model_dir_from_env()
        model_path = model_dir / MODEL_FILE
        if not model_path.is_file():
            raise LstmModelError(f"no LSTM model at {model_path}; export one with kitchen/lstm/export_onnx.py")

        expected_sha256 = manifest_sha256(model_dir)
        if file_sha256(model_path) != expected_sha256:
            raise LstmModelError(
                f"{model_path} doesn't match the sha256 in its data-manifest.json: corrupt or replaced"
            )

        try:
            self._session = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        except Exception as error:  # onnxruntime raises its own exception types for unreadable models
            raise LstmModelError(f"onnxruntime can't load {model_path}: {error}") from error

        # The folder names the training run and the hash pins the exact file, so an edge says which model made it.
        self.version = f"{model_dir.name}+{expected_sha256[:8]}"
        self._frame_shape = tuple(self._session.get_inputs()[0].shape[2:])  # (C, H, W); the step count is free

    def classify(self, window: PairWindow) -> list[tuple[Action, float]]:
        """Score one window: [("transfer.contact", P(contact)), ("transfer.scoop", P(material moved))].

        Raises ValueError for a window the model wasn't built for or a non-finite score; never guesses.
        """
        clip, dt_ms = model_inputs(window, self._frame_shape)
        logits = self._session.run(["logits"], {"clip": clip, "dt_ms": dt_ms})[0][0].astype(np.float64)
        if not np.all(np.isfinite(logits)):
            # A NaN would compare false against every threshold and read as "no contact"; raising sends it to rules.
            raise ValueError(f"LSTM produced non-finite logits {logits}")

        p_contact, p_moved = sigmoid(logits)
        return [("transfer.contact", float(p_contact)), ("transfer.scoop", float(p_moved))]
