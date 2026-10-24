"""Builds a PairWindow per candidate pair: vector features for zone rules, plus a clip (box masks + change mask) for the ConvLSTM.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: feature rows (features.py), observation_frame boxes, change masks from vision (float [S, S] in [0, 1])
Emits: PairWindow (features + interaction_clip + tensor) for classifier.py
Rules: 6 allergen-blind features, object-blind clips; clips come only from kitchen/shared/clips.build_clip, never from
       code here; 1 a missing change mask, a pair too young for two steps, or a clip that fails to build gives a window
       with no clip (clip and tensor None) rather than a made-up one, and the classifier wrapper sends such a window
       to zone rules (rule 7)

A window holds the pair's feature rows from the last window_ms. Its clip samples up to clip_steps of those frames,
about window_ms / clip_steps apart (16 steps over about 1 s by default, as the sim clips the ConvLSTM trains on).
Clips are built only when asked (with_clips): zone rules never read them, and drawing one per pair per frame would
cost the Pi a good share of a core for nothing.
"""

import logging
from collections import deque
from dataclasses import dataclass

import numpy as np

from kitchen.interactions.settings import InteractionSettings
from kitchen.shared.clips import ClipStep, build_clip
from kitchen.shared.interfaces import PairWindow
from kitchen.shared.models import InteractionFeatures, ObservationFrame, validate

logger = logging.getLogger("kitchen.interactions.windows")


@dataclass(frozen=True)
class WindowRow:
    """One frame of one pair: its feature row, both boxes, and that frame's change mask (None if vision sent none)."""

    features: InteractionFeatures
    box_a: list[float]
    box_b: list[float]
    change_mask: np.ndarray | None


def sample_steps(rows: list[WindowRow], steps: int, step_ms: float) -> list[WindowRow]:
    """Pick up to `steps` rows about step_ms apart, ending at the newest, oldest first and strictly increasing in time.

    Each step takes the row nearest its target time, so the spacing is the same at 15, 30 or 60 FPS. A pair younger
    than the window simply gets fewer steps.
    """
    newest_ms = rows[-1].features["t_ms"]
    picked: list[WindowRow] = []
    for k in range(steps - 1, -1, -1):
        target_ms = newest_ms - k * step_ms
        row = min(rows, key=lambda candidate: abs(candidate.features["t_ms"] - target_ms))
        if not picked or row.features["t_ms"] > picked[-1].features["t_ms"]:
            picked.append(row)
    return picked


class WindowBuilder:
    """Keeps each candidate pair's recent rows and hands out one PairWindow per pair per frame."""

    def __init__(
        self,
        settings: InteractionSettings,
        *,
        source_size: tuple[int, int],
        change_mask_params: dict,
        with_clips: bool,
        source: str = "live",
    ) -> None:
        self.settings = settings
        self.source_size = source_size
        self.change_mask_params = dict(change_mask_params)
        self.with_clips = with_clips
        self.source = source
        self._rows: dict[tuple[str, str], deque[WindowRow]] = {}

    def update(
        self, frame: ObservationFrame, feature_rows: list[InteractionFeatures], change_mask: np.ndarray | None
    ) -> list[PairWindow]:
        """Add this frame's rows and return a window for every pair in it, each ending at this frame."""
        t_ms = frame["t_ms"]
        boxes = {detection["entity_id"]: detection["box"] for detection in frame["detections"]}
        if self.with_clips and change_mask is None:
            logger.warning("frame %s at t_ms %s has no change mask: its windows get no clip", frame["frame_id"], t_ms)

        windows = []
        for features in feature_rows:
            key = (features["a_id"], features["b_id"])
            rows = self._rows.setdefault(key, deque())
            # A pair that was out of range for longer than pair_gap_ms starts a new window (its dwell restarted too).
            if rows and t_ms - rows[-1].features["t_ms"] > self.settings.pair_gap_ms:
                rows.clear()
            rows.append(WindowRow(features, boxes[key[0]], boxes[key[1]], change_mask))
            while t_ms - rows[0].features["t_ms"] > self.settings.window_ms:
                rows.popleft()
            windows.append(self.window(list(rows)))

        self.forget_stale_pairs(t_ms)
        return windows

    def window(self, rows: list[WindowRow]) -> PairWindow:
        """One pair's window: every feature row in it, and a clip when one can honestly be built."""
        a_id, b_id = rows[-1].features["a_id"], rows[-1].features["b_id"]
        clip, tensor = self.clip(rows) if self.with_clips and a_id != b_id else (None, None)
        return PairWindow(a_id=a_id, b_id=b_id, features=[row.features for row in rows], clip=clip, tensor=tensor)

    def clip(self, rows: list[WindowRow]) -> tuple[dict | None, np.ndarray | None]:
        """Build the window's clip with the shared builder, or (None, None) when it can't be built from real input.

        A self pair (a wash) never gets one: washes are judged by zone rules alone.
        """
        steps = sample_steps(rows, self.settings.clip_steps, self.settings.window_ms / self.settings.clip_steps)
        if len(steps) < 2 or any(row.change_mask is None for row in steps):
            return None, None

        first = steps[0].features
        last = steps[-1].features
        clip_id = f"{self.source}-{first['a_id']}-{first['b_id']}-{last['t_ms']}"
        clip_steps = [
            ClipStep(row.features["t_ms"], tuple(row.box_a), tuple(row.box_b), row.change_mask) for row in steps
        ]
        try:
            record, tensor = build_clip(
                first["a_id"],
                first["b_id"],
                clip_steps,
                clip_id=clip_id,
                source=self.source,
                tensor_file=f"{clip_id}.npz",
                change_mask_params=self.change_mask_params,
                H=self.settings.clip_size,
                W=self.settings.clip_size,
                source_size=self.source_size,
            )
            return validate(record, "interaction_clip"), tensor
        except ValueError:
            # A wrong-sized or integer mask from upstream. Zone rules still judge the window from its features.
            logger.exception("clip %s could not be built: its window goes to zone rules", clip_id)
            return None, None

    def forget_stale_pairs(self, t_ms: int) -> None:
        """Drop pairs with no row for longer than pair_gap_ms, so memory doesn't grow over a shift."""
        stale = [
            key for key, rows in self._rows.items() if t_ms - rows[-1].features["t_ms"] > self.settings.pair_gap_ms
        ]
        for key in stale:
            del self._rows[key]
