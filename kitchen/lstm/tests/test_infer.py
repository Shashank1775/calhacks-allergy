"""Tests for the "lstm" InteractionClassifier (kitchen/lstm/infer.py).

Owner: partner (written by an agent at the owner's request)
Step:  LSTM classifier
Reads: kitchen/lstm/infer.py; the exported model and the example clip through conftest.py
Emits: nothing
Rules: 6 the scores depend only on the clip; 7 every load or input problem raises, so the wrapper falls back to rules

Loading and input checks run anywhere, CI included. Tests that score clips need the exported model and skip without it.
"""

import json
import math
import time
from types import SimpleNamespace

import numpy as np
import onnxruntime
import pytest

from kitchen.lstm.infer import MODEL_FILE, LstmClassifier, LstmModelError, file_sha256, model_inputs
from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.interfaces import PairWindow

FRAME_SHAPE = (3, 128, 128)  # what the exported model takes per step: box_a, box_b, change at 128 x 128
LOGIT_TOLERANCE = 1e-3  # the export's torch-vs-ONNX parity tolerance; near p = 1 a probability tolerance hides a lot

# Logits measured once on clips made from the example clip, with this model (onnxruntime 1.31; 1, 2 and 4 threads gave
# the same values). The two heads disagree on the first, so swapped outputs, a constant answer or other weights fail.
# After a re-export or retraining, measure the new model on the same clips and update both; never widen the tolerance.
PINNED_MODEL = "boxpooled-v1+152b93cf"
PINNED_LOGITS = {
    "touch without movement": (3.7519, -7.0128),  # P(contact) 0.977, P(moved) 0.0009
    "boxes apart and still": (-13.1163, -17.2835),  # both near 0
}

# The interactions wrapper's primary_timeout_ms (InteractionSettings in kitchen/interactions/settings.py; import it
# from there once that file is on main): a primary that takes longer is skipped for zone rules, so the LSTM's labels
# only count if it answers inside this.
PRIMARY_TIMEOUT_MS = 100


def make_window(tensor: np.ndarray, **clip_fields) -> PairWindow:
    """A knife-at-a-bin window around the given tensor, with 62 ms steps unless clip_fields say otherwise."""
    clip = contract_fixture("interaction_clip", "live-unlabeled")
    clip["dt_ms"] = [0] + [62] * (len(tensor) - 1)
    clip.update(clip_fields)
    return PairWindow(a_id=clip["a_id"], b_id=clip["b_id"], features=[], clip=clip, tensor=tensor)


def random_tensor(steps: int, seed: int = 0) -> np.ndarray:
    """A uint8 [steps, 3, 128, 128] clip of random pixels."""
    return np.random.default_rng(seed).integers(0, 256, size=(steps, *FRAME_SHAPE), dtype=np.uint8)


def logit(probability: float) -> float:
    """The logit behind a probability, so scores near 0 or 1 can be compared closely."""
    return math.log(probability / (1 - probability))


def touch_without_movement(tensor: np.ndarray) -> np.ndarray:
    """The clip with its change channel blanked: the boxes still meet, but nothing in the frame moves."""
    clip = tensor.copy()
    clip[:, 2] = 0
    return clip


def boxes_apart_and_still(tensor: np.ndarray) -> np.ndarray:
    """Both boxes held where the clip starts (near each other, not touching) and nothing moving."""
    clip = tensor.copy()
    clip[:, 0] = tensor[0, 0]
    clip[:, 1] = tensor[0, 1]
    clip[:, 2] = 0
    return clip


DERIVED_CLIPS = {"touch without movement": touch_without_movement, "boxes apart and still": boxes_apart_and_still}


class FixedLogitsSession:
    """Stands in for onnxruntime.InferenceSession: takes 128 x 128 clips and answers every one with the same logits."""

    def __init__(self, logits: list[float]) -> None:
        self.logits = np.array([logits], dtype=np.float32)

    def get_inputs(self):
        """The clip input's shape, as the exported model reports it."""
        return [SimpleNamespace(shape=["batch", "steps", *FRAME_SHAPE])]

    def run(self, output_names, inputs):
        """The fixed logits, shaped like the real model's output ([batch, 2])."""
        return [self.logits]


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


@pytest.mark.safety
@pytest.mark.parametrize("logits", [[np.nan, 0.0], [0.0, np.inf], [-np.inf, 0.0]], ids=["NaN", "inf", "-inf"])
def test_non_finite_logits_raise_instead_of_scoring(tmp_path, monkeypatch, logits):
    # Rules fallback: a NaN compares false against every threshold and would read as "no contact", so the classifier
    # must raise (the wrapper then uses zone rules), never hand back a probability.
    model_path = tmp_path / MODEL_FILE
    model_path.write_bytes(b"stands in for model.onnx")
    manifest = {"files": [{"name": MODEL_FILE, "size": 24, "sha256": file_sha256(model_path)}]}
    (tmp_path / "data-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *args, **kwargs: FixedLogitsSession(logits))
    classifier = LstmClassifier(tmp_path)

    with pytest.raises(ValueError, match="non-finite logits"):
        classifier.classify(make_window(random_tensor(16)))


# ---------------------------------------------------------------------------- scoring (needs the exported model)


def test_example_clip_scores_match_the_trained_model(lstm, example_clip):
    record = example_clip["record"]
    window = PairWindow(record["a_id"], record["b_id"], [], record, example_clip["tensor"])

    labels = dict(lstm.classify(window))

    assert list(labels) == ["transfer.contact", "transfer.scoop"]
    assert logit(labels["transfer.contact"]) == pytest.approx(logit(example_clip["p_contact"]), abs=LOGIT_TOLERANCE)
    assert logit(labels["transfer.scoop"]) == pytest.approx(logit(example_clip["p_moved"]), abs=LOGIT_TOLERANCE)


@pytest.mark.parametrize("clip_name", list(DERIVED_CLIPS))
def test_each_output_keeps_its_pinned_score(lstm, example_clip, clip_name):
    # The example clip is a scoop, where both outputs are near 1, so it can't tell them apart. On a touch without
    # movement P(contact) is high and P(moved) low: swapping them would turn a dirty tool's touch on a bin into a
    # bin-to-tool scoop, and the tool's allergens would never reach the bin.
    assert lstm.version == PINNED_MODEL, "the pinned logits were measured on another model: re-measure them"
    record = example_clip["record"]
    tensor = DERIVED_CLIPS[clip_name](example_clip["tensor"])
    window = PairWindow(record["a_id"], record["b_id"], [], record, tensor)

    labels = dict(lstm.classify(window))

    contact_logit, moved_logit = PINNED_LOGITS[clip_name]
    assert list(labels) == ["transfer.contact", "transfer.scoop"]
    assert logit(labels["transfer.contact"]) == pytest.approx(contact_logit, abs=LOGIT_TOLERANCE)
    assert logit(labels["transfer.scoop"]) == pytest.approx(moved_logit, abs=LOGIT_TOLERANCE)


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


@pytest.mark.slow
def test_one_clip_fits_the_wrappers_primary_timeout(lstm):
    # p95, not the median: every call over the timeout loses its label to zone rules. This machine only; the Pi is
    # slower, so time it there before adoption (README). Marked slow because it measures wall-clock time: on a laptop
    # busy with other work it fails for reasons that have nothing to do with the model. Run it on demand:
    #   uv run pytest kitchen/lstm -m slow
    window = make_window(random_tensor(16))
    lstm.classify(window)  # the first run sets up onnxruntime's buffers
    milliseconds = []
    for _ in range(20):
        start = time.perf_counter()
        lstm.classify(window)
        milliseconds.append((time.perf_counter() - start) * 1000)

    assert np.percentile(milliseconds, 95) < PRIMARY_TIMEOUT_MS
