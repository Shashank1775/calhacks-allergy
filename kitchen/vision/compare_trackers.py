"""Compare the two trackers on one video: the same detections through VISION_TRACKER=iou and =motion, side by side.

Owner: vision (agents)
Step:  vision
Reads: a video file, an optional zones file, the detector bundle (VISION_* settings)
Emits: a table on stdout: per class and tracker, how often it was tracked, how many entity IDs it used, and how much of
       it had unknown history
Rules: read only: nothing is written and the running tracker setting isn't changed. Time runs forward (the video's own
       time from 0, as an offline run)

The motion tracker stays off until real overhead footage shows it helps. This is how to check: record the overhead
shot list, then run

    uv run python -m kitchen.vision.compare_trackers --video data/recordings/overhead.mp4 --zones zones.json
    uv run python -m kitchen.vision.compare_trackers --video data/recordings/overhead.mp4 --fps 15   # a slower camera

Fewer IDs per minute for the same objects means fewer identity breaks (each break is a new entity with unknown
history, so plates hold). The detector runs once per frame, and both trackers see exactly the same detections.
"""

import argparse
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from kitchen.shared.clock import FakeClock
from kitchen.shared.config import load_settings
from kitchen.vision.common import SourceFrame, VisionSettings, ZoneLookup
from kitchen.vision.tracking import TRACKERS, Tracker


@dataclass
class ClassTally:
    """What one tracker emitted for one class (knife, spoon, fork, bowl or wrist) over a video."""

    frames_seen: int = 0
    items: int = 0
    unknown_items: int = 0
    entity_ids: set[str] = field(default_factory=set)


def compare(
    frames: Iterable[SourceFrame], detector, zone_of: ZoneLookup, settings: VisionSettings
) -> tuple[dict[str, dict[str, ClassTally]], int, int]:
    """Run every frame's detections through both trackers. Returns {tracker: {class_name: ClassTally}}, the number
    of frames, and the video's duration in ms."""
    trackers = {name: Tracker(replace(settings, tracker=name)) for name in TRACKERS}
    tallies: dict[str, dict[str, ClassTally]] = {name: {} for name in TRACKERS}
    frame_count = 0
    first_t_ms = last_t_ms = 0
    for frame in frames:
        height, width = frame.rgb.shape[:2]
        objects, people = detector.detect(frame)
        for name, tracker in trackers.items():
            items = tracker.update(objects, people, frame.t_ms, zone_of, width, height)
            add_frame(tallies[name], items)
        if frame_count == 0:
            first_t_ms = frame.t_ms
        last_t_ms = frame.t_ms
        frame_count += 1
    return tallies, frame_count, last_t_ms - first_t_ms


def add_frame(tally: dict[str, ClassTally], items: list[dict]) -> None:
    """Count one frame's observation items into a tracker's per-class tallies."""
    seen = set()
    for item in items:
        counts = tally.setdefault(item["class_name"], ClassTally())
        counts.items += 1
        counts.unknown_items += item["origin"] == "unknown"
        counts.entity_ids.add(item["entity_id"])
        seen.add(item["class_name"])
    for class_name in seen:
        tally[class_name].frames_seen += 1


def every_nth_ms(frames: Iterable[SourceFrame], fps: float) -> Iterator[SourceFrame]:
    """Keep about `fps` frames a second of the video's own time, to see how each tracker copes with a slower camera."""
    step_ms = 1000 / fps
    next_ms = None
    for frame in frames:
        if next_ms is None or frame.t_ms >= next_ms:
            next_ms = (next_ms if next_ms is not None else frame.t_ms) + step_ms
            yield frame


def table(tallies: dict[str, dict[str, ClassTally]], frame_count: int, duration_ms: int) -> str:
    """The comparison as a markdown table, one row per class and tracker."""
    minutes = max(duration_ms, 1) / 60_000
    lines = [
        f"{frame_count} frames, {duration_ms / 1000:.1f} s",
        "",
        "| class | tracker | frames tracked | entity IDs | new IDs per minute | unknown history |",
        "|---|---|---|---|---|---|",
    ]
    class_names = sorted({name for per_tracker in tallies.values() for name in per_tracker})
    for class_name in class_names:
        for tracker_name in TRACKERS:
            counts = tallies[tracker_name].get(class_name, ClassTally())
            tracked = counts.frames_seen / max(frame_count, 1)
            unknown = counts.unknown_items / max(counts.items, 1)
            lines.append(
                f"| {class_name} | {tracker_name} | {tracked:.0%} | {len(counts.entity_ids)} | "
                f"{len(counts.entity_ids) / minutes:.1f} | {unknown:.0%} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI; see the module docstring."""
    parser = argparse.ArgumentParser(description="Compare VISION_TRACKER=iou and =motion on one video.")
    parser.add_argument("--video", type=Path, required=True, help="a video file (overhead footage)")
    parser.add_argument("--zones", type=Path, help="zones.json, so origins (clean stack, bins) are as in the kitchen")
    parser.add_argument("--fps", type=float, help="keep only this many frames a second (default: every frame)")
    args = parser.parse_args(argv)

    from kitchen.vision.camera import VideoSource
    from kitchen.vision.detect import open_detector
    from kitchen.vision.zones import load_zones, zone_lookup

    settings = load_settings(VisionSettings, "VISION_")
    zone_of = zone_lookup(load_zones(args.zones) if args.zones else [])
    detector = open_detector(settings)
    frames = VideoSource(args.video, FakeClock(0))
    if args.fps:
        frames = every_nth_ms(frames, args.fps)
    print(table(*compare(frames, detector, zone_of, settings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
