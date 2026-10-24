"""Pair windows: features over the last window, plus a clip from the shared builder only when real input allows one.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.windows, features, candidates, scenes
Emits: pytest tests
Rules: 6 clips are object-blind (box masks and a change mask only) and validate against interaction_clip; 1 no change
       mask means no clip, never a made-up one
"""

import numpy as np
import pytest

from kitchen.interactions.candidates import candidate_pairs, zone_kinds
from kitchen.interactions.features import FeatureTracker
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import KNIFE, SCENES, ZONES
from kitchen.interactions.windows import WindowBuilder
from kitchen.shared.models import schema_validator, validate

SETTINGS = InteractionSettings()
KINDS = zone_kinds(ZONES)
SOURCE_SIZE = (1280, 720)
CHANGE_PARAMS = {"gain": 4.0, "blur_sigma_px": 1.5, "decay": 0.0}


def all_windows(frames: list[dict], masks: list | None, with_clips: bool = True) -> list:
    """Every PairWindow the frames produce, frame by frame."""
    aspect = SOURCE_SIZE[1] / SOURCE_SIZE[0]
    tracker = FeatureTracker(KINDS, SETTINGS, aspect)
    builder = WindowBuilder(SETTINGS, source_size=SOURCE_SIZE, change_mask_params=CHANGE_PARAMS, with_clips=with_clips)
    windows = []
    for index, frame in enumerate(frames):
        pairs = candidate_pairs(frame["detections"], KINDS, SETTINGS.candidate_max_gap, aspect)
        rows = tracker.update(frame, pairs)
        windows += builder.update(frame, rows, None if masks is None else masks[index])
    return windows


def random_masks(count: int, seed: int = 7) -> list[np.ndarray]:
    """Float change masks in [0, 1] at clip size, as vision hands them over."""
    rng = np.random.default_rng(seed)
    return [rng.random((128, 128)).astype(np.float32) for _ in range(count)]


@pytest.fixture(scope="module")
def scoop_windows() -> list:
    frames = SCENES["scoop_then_plate"].frames()
    return all_windows(frames, random_masks(len(frames)))


def test_windows_from_fixture_scene_hold_the_last_window_of_one_pair(scoop_windows):
    for window in scoop_windows:
        times = [row["t_ms"] for row in window.features]
        assert {(row["a_id"], row["b_id"]) for row in window.features} == {(window.a_id, window.b_id)}
        assert times == sorted(times)
        assert times[-1] - times[0] <= SETTINGS.window_ms


@pytest.mark.safety
def test_every_clip_validates_and_holds_only_box_masks_and_the_change_mask(scoop_windows):
    clipped = [window for window in scoop_windows if window.clip is not None]
    allowed = set(schema_validator("interaction_clip").schema["properties"])

    assert clipped, "a pair in range for a second should get clips"
    for window in clipped:
        validate(window.clip, "interaction_clip")
        assert set(window.clip) <= allowed  # nothing about what the objects are
        assert window.clip["channels"] == ["box_a", "box_b", "change"]
        steps = window.clip["shape"]["T"]
        assert 2 <= steps <= SETTINGS.clip_steps
        assert window.tensor.shape == (steps, 3, 128, 128)
        assert window.tensor.dtype == np.uint8


def test_a_full_window_samples_sixteen_steps_over_about_a_second(scoop_windows):
    window = next(w for w in scoop_windows if w.clip is not None and w.clip["t_ms"][-1] == 1000 and w.b_id == "bin-1")

    assert window.clip["shape"]["T"] == 16
    assert window.clip["t_ms"][-1] - window.clip["t_ms"][0] == pytest.approx(940, abs=70)
    assert window.tensor[-1, 0].any() and window.tensor[-1, 1].any()  # both boxes drawn


@pytest.mark.safety
def test_a_missing_change_mask_gives_a_window_with_no_clip_never_a_made_up_one():
    frames = SCENES["scoop_then_plate"].frames()
    masks = random_masks(len(frames))
    masks[20] = None  # vision sent no mask for this frame

    windows = all_windows(frames, masks)

    gap_ms = frames[20]["t_ms"]
    at_gap = [window for window in windows if window.features[-1]["t_ms"] == gap_ms]
    assert at_gap and all(window.clip is None and window.tensor is None for window in at_gap)
    assert all(len(window.features) > 1 for window in at_gap)  # zone rules still get the feature rows
    for window in windows:
        if window.clip is not None:
            assert gap_ms not in window.clip["t_ms"]


def test_no_clips_unless_asked_and_never_for_a_self_pair():
    frames = SCENES["wash"].frames()
    masks = random_masks(len(frames))

    unasked = all_windows(SCENES["scoop_then_plate"].frames(), None, with_clips=False)
    washes = all_windows(frames, masks)

    assert all(window.clip is None and window.tensor is None for window in unasked)
    assert all(window.a_id == window.b_id == KNIFE for window in washes)
    assert all(window.clip is None for window in washes)


def test_a_mask_of_the_wrong_size_leaves_the_window_to_zone_rules():
    frames = SCENES["scoop_then_plate"].frames()
    masks = [np.zeros((64, 64), dtype=np.float32) for _ in frames]

    windows = all_windows(frames, masks)

    assert windows and all(window.clip is None for window in windows)
