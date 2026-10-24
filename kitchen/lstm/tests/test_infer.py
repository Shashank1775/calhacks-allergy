"""Tests for the "lstm" InteractionClassifier (kitchen/lstm/infer.py).

Owner: partner (written by an agent at the owner's request)
Step:  LSTM classifier
Reads: kitchen/lstm/infer.py; the exported model and the example clip through conftest.py
Emits: nothing
Rules: 6 the scores depend only on the clip; 7 every load or input problem raises, so the wrapper falls back to rules

Loading and input checks run anywhere, CI included. Tests that score clips need the exported model and skip without it.
"""

import json
import time

import numpy as np
import pytest

from kitchen.lstm.infer import MODEL_FILE, LstmClassifier, LstmModelError, file_sha256, model_inputs
from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.interfaces import PairWindow

FRAME_SHAPE = (3, 128, 128)  # what the exported model takes per step: box_a, box_b, change at 128 x 128


def make_window(tensor: np.ndarray, **clip_fields) -> PairWindow:
    """A knife-at-a-bin window around the given tensor, with 62 ms steps unless clip_fields say otherwise."""
    clip = contract_fixture("interaction_clip", "live-unlabeled")
    clip["dt_ms"] = [0] + [62] * (len(tensor) - 1)
    clip.update(clip_fields)
    return PairWindow(a_id=clip["a_id"], b_id=clip["b_id"], features=[], clip=clip, tensor=tensor)


def random_tensor(steps: int, seed: int = 0) -> np.ndarray:
    """A uint8 [steps, 3, 128, 128] clip of random pixels."""
    return np.random.default_rng(seed).integers(0, 256, size=(steps, *FRAME_SHAPE), dtype=np.uint8)


# ------------------------------------------------------------------------------------- loading (no model needed)


def test_missing_model_raises(tmp_path):
    with pytest.raises(LstmModelError, match="no LSTM model"):
        LstmClassifier(tmp_path)


def test_model_without_a_manifest_entry_raises(tmp_path):
    (tmp_path / MODEL_FILE).write_bytes(b"not a model")

    with pytest.raises(LstmModelError, match="no usable model.onnx entry"):
        LstmClassifier(tmp_path)


def test_model_not_matching_its_manifest_raises(tmp_path):
    (tmp_path / MODEL_FILE).write_bytes(b"a model that changed after export")
    manifest = {"files": [{"name": MODEL_FILE, "size": 1, "sha256": "0" * 64}]}
    (tmp_path / "data-manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(LstmModelError, match="doesn't match the sha256"):
        LstmClassifier(tmp_path)


def test_unloadable_model_raises(tmp_path):
    model_path = tmp_path / MODEL_FILE
    model_path.write_bytes(b"bytes onnxruntime can't parse")
    manifest = {"files": [{"name": MODEL_FILE, "size": 29, "sha256": file_sha256(model_path)}]}
    (tmp_path / "data-manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(LstmModelError, match="can't load"):
        LstmClassifier(tmp_path)


# ------------------------------------------------------------------------------- input checks (no model needed)


@pytest.mark.parametrize(
    ("window", "message"),
    [
        (make_window(random_tensor(16).astype(np.float32) / 255), "uint8"),
        (make_window(random_tensor(16)[:, 0]), "expected"),
        (make_window(np.zeros((16, 4, 128, 128), dtype=np.uint8)), "expected"),
        (make_window(np.zeros((16, 3, 64, 64), dtype=np.uint8)), "expected"),
        (make_window(random_tensor(16), channels=["box_b", "box_a", "change"]), "channels"),
        (make_window(random_tensor(1)), "at least 2"),
        (make_window(random_tensor(16), dt_ms=[0, 62, 62]), "dt_ms"),
    ],
    ids=["float tensor", "3-d tensor", "4 channels", "64 x 64 frame", "swapped channels", "1 step", "short dt_ms"],
)
def test_a_window_the_model_was_not_built_for_raises(window, message):
    with pytest.raises(ValueError, match=message):
        model_inputs(window, FRAME_SHAPE)


def test_model_inputs_are_scaled_like_training():
    tensor = random_tensor(12)

    clip, dt_ms = model_inputs(make_window(tensor), FRAME_SHAPE)

    assert clip.shape == (1, 12, *FRAME_SHAPE) and clip.dtype == np.float32
    np.testing.assert_allclose(clip[0], tensor / 255, rtol=1e-6)
    assert dt_ms.shape == (1, 12) and dt_ms[0, 0] == 0 and dt_ms[0, 1] == 62


# ---------------------------------------------------------------------------- scoring (needs the exported model)


def test_example_clip_scores_match_the_trained_model(lstm, example_clip):
    record = example_clip["record"]
    window = PairWindow(record["a_id"], record["b_id"], [], record, example_clip["tensor"])

    labels = dict(lstm.classify(window))

    assert list(labels) == ["transfer.contact", "transfer.scoop"]
    assert labels["transfer.contact"] == pytest.approx(example_clip["p_contact"], abs=1e-3)
    assert labels["transfer.scoop"] == pytest.approx(example_clip["p_moved"], abs=1e-3)


@pytest.mark.parametrize("steps", [2, 12, 16, 24])
def test_probabilities_are_between_zero_and_one(lstm, steps):
    for tensor in (
        random_tensor(steps),
        np.zeros((steps, *FRAME_SHAPE), np.uint8),
        np.full((steps, *FRAME_SHAPE), 255, np.uint8),
    ):
        labels = lstm.classify(make_window(tensor))

        assert [action for action, _ in labels] == ["transfer.contact", "transfer.scoop"]
        assert all(0.0 <= probability <= 1.0 for _, probability in labels)


def test_wrong_shape_window_raises_through_classify(lstm):
    with pytest.raises(ValueError, match="expected"):
        lstm.classify(make_window(np.zeros((16, 3, 64, 64), dtype=np.uint8)))


@pytest.mark.safety
def test_scores_depend_only_on_the_clip(lstm):
    # Blind classifier: the same boxes and motion score the same whatever the entities are called or their features.
    tensor = random_tensor(16, seed=3)
    window = make_window(tensor)
    clip = {**window.clip, "a_id": "hand-p1-left", "b_id": "plate-t7"}
    features = [contract_fixture("interaction_features", "knife-in-bin")]
    renamed = PairWindow("hand-p1-left", "plate-t7", features, clip, tensor)

    assert lstm.classify(window) == lstm.classify(renamed)


def test_version_names_the_run_and_pins_the_file(lstm, model_dir):
    assert lstm.name == "lstm"
    assert lstm.version == f"{model_dir.name}+{file_sha256(model_dir / MODEL_FILE)[:8]}"


def test_one_clip_fits_the_edge_budget(lstm):
    window = make_window(random_tensor(16))
    lstm.classify(window)  # the first run sets up onnxruntime's buffers
    seconds = []
    for _ in range(5):
        start = time.perf_counter()
        lstm.classify(window)
        seconds.append(time.perf_counter() - start)

    assert np.median(seconds) < 0.5  # the whole event-end to edge budget; the Pi is slower than a laptop
