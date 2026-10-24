"""Tests for the export's parity check (kitchen/lstm/export_onnx.py), with the network and onnxruntime stubbed.

Owner: partner (written by an agent at the owner's request)
Step:  LSTM classifier
Reads: kitchen/lstm/export_onnx.py
Emits: nothing
Rules: 7 an ONNX file that disagrees with torch, a NaN included, is refused, so it never reaches the runtime

Torch isn't a project dependency (export_onnx.py says why), so these skip without it. Run them with:

    uv run --with torch==2.14.1 python -m pytest kitchen/lstm/tests/test_export_onnx.py
"""

import numpy as np
import onnxruntime
import pytest

torch = pytest.importorskip("torch")

from kitchen.lstm.export_onnx import PARITY_TOLERANCE, check_parity  # noqa: E402  (imports torch, so after the skip)

CLIPS = [("tiny", np.zeros((2, 3, 4, 4), np.uint8), np.array([0, 62]))]  # the stubs ignore the clip's contents


class FixedLogitsSession:
    """Stands in for onnxruntime.InferenceSession: the same logits for every clip."""

    def __init__(self, logits: list[float]) -> None:
        self.logits = np.array([logits], dtype=np.float32)

    def run(self, output_names, inputs):
        """The fixed logits, shaped like the real model's output ([batch, 2])."""
        return [self.logits]


def worst_difference(monkeypatch, torch_logits: list[float], onnx_logits: list[float]) -> float:
    """check_parity's answer when the network gives torch_logits and the ONNX file gives onnx_logits."""
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *args, **kwargs: FixedLogitsSession(onnx_logits))

    def network(clip, dt_ms):
        return torch.tensor([torch_logits], dtype=torch.float32)

    return check_parity(network, "unused.onnx", CLIPS)


def test_matching_logits_pass(monkeypatch):
    assert worst_difference(monkeypatch, [1.0, -2.0], [1.0001, -2.0]) <= PARITY_TOLERANCE


@pytest.mark.parametrize(
    ("torch_logits", "onnx_logits"),
    [([0.0, 0.0], [np.nan, np.nan]), ([0.0, 0.0], [0.0, np.inf]), ([np.nan, 0.0], [np.nan, 0.0])],
    ids=["onnx NaN", "onnx inf", "torch and onnx NaN"],
)
def test_non_finite_logits_fail_the_parity_check(monkeypatch, torch_logits, onnx_logits):
    # NaN compares false against the tolerance, so it would slip through; a model that gives one is never recorded.
    assert worst_difference(monkeypatch, torch_logits, onnx_logits) > PARITY_TOLERANCE
