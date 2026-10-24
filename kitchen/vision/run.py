"""Main loop and CLI: source -> detect -> track -> zones -> ObservationFrame + change mask -> consumers.

Owner: vision (agents)
Step:  vision
Reads: camera.py, detect.py, tracking.py, zones.py, kitchen/shared/change_mask.py, Clock
Emits: observation_frame and its change mask to EntityStore.ingest (partner), kitchen/interactions and record.py
Rules: the frame loop never blocks on a consumer; time runs forward

FrameLoop.step() runs one frame through the pipeline and hands the FrameOutput to every consumer: the entity store,
the interactions step, a recorder, and the Vision API's status. A detector error stops the loop (fail closed: frames
stop, /health goes stale, the gate faults); a consumer error is logged and the frame still reaches the others.

    uv run python -m kitchen.vision.run --video data/recordings/prep.mp4 --zones zones.json --out frames.jsonl
    uv run python -m kitchen.vision.run --webcam 0 --zones zones.json --serve       # live, with the Vision HTTP API
"""

import argparse
import json
import logging
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np

from kitchen.shared.change_mask import change_mask
from kitchen.shared.clips import letterbox_frame
from kitchen.shared.clock import SystemClock
from kitchen.shared.config import load_settings, log_settings
from kitchen.vision.common import FrameOutput, SourceFrame, VisionSettings, ZoneLookup

logger = logging.getLogger(__name__)

Consumer = Callable[[FrameOutput], None]


class FrameLoop:
    """One frame at a time through detection, tracking and zones, to an ObservationFrame and its change mask."""

    def __init__(self, settings: VisionSettings, detector, tracker, zone_of: ZoneLookup, consumers: list[Consumer]):
        self.settings = settings
        self.detector = detector
        self.tracker = tracker
        self.zone_of = zone_of
        self.consumers = consumers
        self._previous_rgb: np.ndarray | None = None
        self._previous_mask: np.ndarray | None = None
        self._previous_t_ms: int | None = None
        self._fps: float | None = None

    def step(self, frame: SourceFrame) -> FrameOutput:
        """Run one frame and hand its output to every consumer. Returns the output."""
        if self._previous_t_ms is not None and frame.t_ms <= self._previous_t_ms:
            raise ValueError(f"frame t_ms {frame.t_ms} does not follow {self._previous_t_ms} (time runs forward)")
        objects, people = self.detector.detect(frame)  # an error here propagates: the loop stops, health goes stale
        detections = self.tracker.update(objects, people, frame.t_ms, self.zone_of)
        observation = {
            "frame_id": frame.frame_id,
            "t_ms": frame.t_ms,
            "camera_id": self.settings.camera_id,
            "detections": detections,
        }
        fps = self._update_fps(frame.t_ms)
        if fps is not None:
            observation["fps_estimate"] = round(fps, 2)
        height, width = frame.rgb.shape[:2]
        output = FrameOutput(observation, self._change_mask(frame.rgb), width, height)
        for consumer in self.consumers:
            try:
                consumer(output)
            except Exception:  # one failing consumer must not starve the others or stop the camera
                logger.exception("frame %s: consumer %r failed", frame.frame_id, consumer)
        return output

    def run(self, source: Iterable[SourceFrame], stop: threading.Event | None = None) -> int:
        """Run every frame from the source until it ends or `stop` is set. Returns how many frames ran."""
        count = 0
        for frame in source:
            if stop is not None and stop.is_set():
                break
            self.step(frame)
            count += 1
        return count

    def _change_mask(self, rgb: np.ndarray) -> np.ndarray:
        """The shared change mask on the frame letterboxed to clip_size; all zeros for the first frame (no motion yet)."""
        letterboxed = letterbox_frame(rgb, self.settings.clip_size)
        if self._previous_rgb is None:
            mask = np.zeros(letterboxed.shape[:2], dtype=np.float32)
        else:
            mask = change_mask(
                self._previous_rgb,
                letterboxed,
                self._previous_mask,
                gain=self.settings.change_gain,
                blur_sigma_px=self.settings.change_blur_sigma_px,
                decay=self.settings.change_decay,
            ).astype(np.float32)
        self._previous_rgb, self._previous_mask = letterboxed, mask
        return mask

    def _update_fps(self, t_ms: int) -> float | None:
        """Frames per second, smoothed over recent frames, from the frames' own t_ms."""
        if self._previous_t_ms is not None:
            instant = 1000.0 / max(1, t_ms - self._previous_t_ms)
            self._fps = instant if self._fps is None else 0.9 * self._fps + 0.1 * instant
        self._previous_t_ms = t_ms
        return self._fps


def jsonl_writer(path: Path) -> Consumer:
    """A consumer that appends each ObservationFrame to a JSONL file, one canonical line per frame."""
    handle = open(path, "w")  # noqa: SIM115  (closed by the CLI when the run ends)

    def write(output: FrameOutput) -> None:
        handle.write(json.dumps(output.observation, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()

    write.close = handle.close
    return write


def main(argv: list[str] | None = None) -> int:
    """CLI; see the module docstring."""
    raise NotImplementedError("wired up once the pieces land")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
