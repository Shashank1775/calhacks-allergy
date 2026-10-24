"""Fixtures for the LSTM runtime tests: the exported model and the example clip, skipped when they aren't on disk.

Owner: partner (written by an agent at the owner's request)
Step:  LSTM classifier
Reads: LSTM_MODEL_DIR (default <repo>/data/models/lstm/boxpooled-v1) and LSTM_EXAMPLE_CLIP (default
       <repo>/data/examples/example_clip.npz, with its .json of known scores beside it)
Emits: model_dir, lstm, make_classifier (for the shared InteractionClassifier contract suite), example_clip
Rules: 7 these tests check the runtime only; nothing here makes the LSTM the default

The model and the example live in data/ (gitignored, never in git), so CI has neither and the tests that need them
skip there. In a git worktree, point both variables at the main checkout's data/.
"""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from kitchen.lstm.infer import MODEL_FILE, LstmClassifier

REPO_DATA = Path(__file__).resolve().parents[3] / "data"
MODEL_DIR = Path(os.environ.get("LSTM_MODEL_DIR") or REPO_DATA / "models" / "lstm" / "boxpooled-v1")
EXAMPLE_CLIP = Path(os.environ.get("LSTM_EXAMPLE_CLIP") or REPO_DATA / "examples" / "example_clip.npz")


@pytest.fixture(scope="session")
def model_dir() -> Path:
    """The exported model's folder; skips the test when model.onnx isn't there."""
    if not (MODEL_DIR / MODEL_FILE).is_file():
        pytest.skip(
            f"no exported LSTM model at {MODEL_DIR / MODEL_FILE}: export one (kitchen/lstm/README.md) "
            "or set LSTM_MODEL_DIR to a folder that has it"
        )
    return MODEL_DIR


@pytest.fixture(scope="session")
def lstm(model_dir: Path) -> LstmClassifier:
    """One LstmClassifier over the exported model, shared by the tests (loading it once keeps them fast)."""
    return LstmClassifier(model_dir)


@pytest.fixture
def make_classifier(lstm: LstmClassifier):
    """The factory the shared InteractionClassifier contract suite calls: the real LSTM classifier."""
    return lambda: lstm


@pytest.fixture(scope="session")
def example_clip() -> dict:
    """The example sim clip: its uint8 tensor, its interaction_clip record, and the scores the trained model gave it."""
    known_scores = EXAMPLE_CLIP.with_suffix(".json")
    if not (EXAMPLE_CLIP.is_file() and known_scores.is_file()):
        pytest.skip(f"no example clip at {EXAMPLE_CLIP} (and .json): set LSTM_EXAMPLE_CLIP to one")
    with np.load(EXAMPLE_CLIP) as clip:
        tensor = clip["x"]
    return {"tensor": tensor, **json.loads(known_scores.read_text())}
