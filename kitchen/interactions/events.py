"""Segments per-frame labels into InteractionEvents and streams them to the database; also the JSONL CLI.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: observation_frame stream (+ change masks), zones, candidates.py, features.py, windows.py, classifier.py,
       StateClient, Clock
Emits: interaction_event via StateClient.record_interaction (interaction table) and events.jsonl
Rules: 5 frames must move forward, t_start_ms <= t_end_ms, each pair and action's events come out in time order, and
       event IDs are deterministic, so a replay writes no duplicates; 1 events are buffered and retried on
       StateUnavailable, never dropped; a wash is a self-loop (a_id == b_id), only a self pair can make one, and only
       with probability at least wash_floor; "unimportant" makes no edge

How a frame becomes edges: candidate pairs (candidates.py) -> feature rows (features.py) -> one PairWindow per pair
(windows.py) -> labels from the classifier wrapper (classifier.py) -> the Segmenter here, which merges a pair's hits on
the same action into one event and closes it merge_gap_ms after the last hit, so it reaches the database within about
half a second of its end. A transfer still going after max_event_ms is closed and a new one started.

Directions (AGENTS.md, Open questions): a scoop from a bin moves bin -> tool (b_to_a, as b is the bin); every other
transfer can move both ways, which is also the safe answer when the camera can't tell which way food went.

Usage (a vision recording directory holds frames.jsonl, change.npz and manifest.json):
  uv run python -m kitchen.interactions.events --frames <frames.jsonl> --zones <zones.json> --out events.jsonl
  uv run python -m kitchen.interactions.events --frames <rec>/frames.jsonl --change <rec>/change.npz \
      --zones <zones.json> --out events.jsonl
"""

import argparse
import json
import logging
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from kitchen.interactions.candidates import candidate_pairs, zone_kinds
from kitchen.interactions.classifier import FallbackClassifier, Labels
from kitchen.interactions.features import FeatureTracker
from kitchen.interactions.settings import InteractionSettings, load_interaction_settings
from kitchen.interactions.windows import WindowBuilder
from kitchen.interactions.zone_rules import ZoneRules
from kitchen.shared.change_mask import DEFAULT_BLUR_SIGMA_PX, DEFAULT_DECAY, DEFAULT_GAIN
from kitchen.shared.interfaces import Clock, StateClient, StateUnavailable
from kitchen.shared.models import InteractionEvent, InteractionFeatures, ObservationFrame, Zone, validate

logger = logging.getLogger("kitchen.interactions.events")

DEFAULT_CHANGE_MASK_PARAMS = {"gain": DEFAULT_GAIN, "blur_sigma_px": DEFAULT_BLUR_SIGMA_PX, "decay": DEFAULT_DECAY}


def event_order(event: InteractionEvent) -> tuple[int, int, str]:
    """Sort key for edges: t_start_ms, then t_end_ms, then event_id (AGENTS.md, Open questions: equal timestamps)."""
    return (event["t_start_ms"], event["t_end_ms"], event["event_id"])


def direction_for(action: str, b_kind: str) -> str:
    """Which way allergens can move along an edge: bin to tool for a scoop from a bin, both ways for anything else."""
    if action == "transfer.scoop" and b_kind == "bin":
        return "b_to_a"
    return "both"


@dataclass
class Segment:
    """An open event: consecutive hits of one action on one pair, from one classifier."""

    a_id: str
    b_id: str
    b_kind: str
    action: str
    classifier: dict
    t_start_ms: int
    t_end_ms: int
    frame_start: int
    frame_end: int
    probability: float

    def extend(self, t_ms: int, frame_id: int, probability: float) -> None:
        """Add a hit. A transfer keeps its highest probability; a wash its lowest, since a wash lowers risk."""
        self.t_end_ms = t_ms
        self.frame_end = frame_id
        if self.action == "clean.wash":
            self.probability = min(self.probability, probability)
        else:
            self.probability = max(self.probability, probability)

    def event(self) -> InteractionEvent:
        """The finished interaction_event, validated. Its ID depends only on what happened, so replays repeat it."""
        short_action = self.action.split(".")[-1]
        event = {
            "event_id": f"ev-{self.t_start_ms}-{self.a_id}-{self.b_id}-{short_action}",
            "t_start_ms": self.t_start_ms,
            "t_end_ms": self.t_end_ms,
            "a_id": self.a_id,
            "b_id": self.b_id,
            "action": self.action,
            "probability": self.probability,
            "direction": direction_for(self.action, self.b_kind),
            "classifier": dict(self.classifier),
            "evidence": {"frame_start": self.frame_start, "frame_end": self.frame_end},
        }
        return validate(event, "interaction_event")


class Segmenter:
    """Merges per-frame labels into events, one open Segment per (pair, action, classifier)."""

    def __init__(self, settings: InteractionSettings) -> None:
        self.settings = settings
        self._open: dict[tuple, Segment] = {}

    def edge_labels(self, latest: InteractionFeatures, labels: Labels) -> Labels:
        """The labels that can become edges.

        A wash only on a self pair and only at wash_floor or above; a transfer only between two entities and only at
        event_floor or above. A transfer on a self pair means nothing and a wash between two entities would clean
        the wrong thing, so both are dropped.
        """
        self_pair = latest["a_id"] == latest["b_id"]
        kept = []
        for action, probability in labels:
            if action == "unimportant":
                continue
            if action == "clean.wash":
                if self_pair and probability >= self.settings.wash_floor:
                    kept.append((action, probability))
            elif not self_pair and probability >= self.settings.event_floor:
                kept.append((action, probability))
        return kept

    def continues(self, segment: Segment, t_ms: int) -> bool:
        """Whether a hit at t_ms belongs to the open segment rather than a new one."""
        if t_ms - segment.t_end_ms > self.settings.merge_gap_ms:
            return False
        # A long transfer is cut so its edge isn't held back. A wash never is: two washes would clean twice.
        if segment.action != "clean.wash" and t_ms - segment.t_start_ms > self.settings.max_event_ms:
            return False
        return True

    def add(self, latest: InteractionFeatures, labels: Labels, classifier: dict) -> list[InteractionEvent]:
        """Add one window's labels, judged at its newest frame. Returns any event this closes (a cut transfer)."""
        t_ms, frame_id = latest["t_ms"], latest["frame_id"]
        closed = []
        for action, probability in self.edge_labels(latest, labels):
            key = (latest["a_id"], latest["b_id"], action, classifier["name"], classifier["version"])
            segment = self._open.get(key)
            if segment is not None and self.continues(segment, t_ms):
                segment.extend(t_ms, frame_id, probability)
                continue
            if segment is not None:
                closed.append(segment.event())
            self._open[key] = Segment(
                a_id=latest["a_id"],
                b_id=latest["b_id"],
                b_kind=latest["b_kind"],
                action=action,
                classifier=classifier,
                t_start_ms=t_ms,
                t_end_ms=t_ms,
                frame_start=frame_id,
                frame_end=frame_id,
                probability=probability,
            )
        return closed

    def close_stale(self, t_ms: int) -> list[InteractionEvent]:
        """Close every segment with no hit for longer than merge_gap_ms, in edge order."""
        stale = [key for key, segment in self._open.items() if t_ms - segment.t_end_ms > self.settings.merge_gap_ms]
        return sorted((self._open.pop(key).event() for key in stale), key=event_order)

    def close_all(self) -> list[InteractionEvent]:
        """Close every open segment (the stream ended), in edge order."""
        events = [segment.event() for segment in self._open.values()]
        self._open.clear()
        return sorted(events, key=event_order)


class EventWriter:
    """Writes events through StateClient.record_interaction, buffering them while the database is unreachable.

    Nothing is ever dropped (safety rule 1): an event leaves the buffer only after the write succeeds, and a failed
    write is retried no sooner than retry_ms later, by the Clock. record_interaction is idempotent by event_id, so a
    retry after a write that landed but reported failure is harmless.
    """

    def __init__(self, state: StateClient, clock: Clock, retry_ms: int) -> None:
        self.state = state
        self.clock = clock
        self.retry_ms = retry_ms
        self.pending: deque[InteractionEvent] = deque()
        self._retry_at_ms: int | None = None

    def write(self, events: list[InteractionEvent]) -> int:
        """Buffer `events` behind any still waiting and try to send them all. Returns how many are still buffered."""
        self.pending.extend(events)
        return self.flush()

    def flush(self) -> int:
        """Send buffered events oldest first, stopping at the first StateUnavailable. Returns the buffer depth."""
        if self._retry_at_ms is not None and self.clock.now_ms() < self._retry_at_ms:
            return len(self.pending)
        while self.pending:
            try:
                self.state.record_interaction(self.pending[0])
            except StateUnavailable:
                if self._retry_at_ms is None:
                    logger.warning("database unreachable: buffering interaction events (%s waiting)", len(self.pending))
                self._retry_at_ms = self.clock.now_ms() + self.retry_ms
                return len(self.pending)
            self.pending.popleft()
        if self._retry_at_ms is not None:
            logger.info("database reachable again: buffered interaction events written")
            self._retry_at_ms = None
        return 0


class InteractionStep:
    """The whole step for one camera: feed it frames in order, get back the events each frame closes."""

    def __init__(
        self,
        zones: list[Zone],
        *,
        settings: InteractionSettings | None = None,
        classifier: FallbackClassifier | None = None,
        source_size: tuple[int, int] | None = None,
        change_mask_params: dict | None = None,
        with_clips: bool = False,
        source: str = "live",
    ) -> None:
        """Set up the step. source_size is the camera frame's (width, height); with_clips builds a clip per window.

        Turn with_clips on when the classifier has a primary (the ConvLSTM reads clips; zone rules don't).
        """
        self.settings = settings or InteractionSettings()
        width, height = source_size or (self.settings.frame_width, self.settings.frame_height)
        self.aspect = height / width
        self.kinds = zone_kinds(zones)
        self.classifier = classifier or FallbackClassifier(ZoneRules(self.settings))
        self.features = FeatureTracker(self.kinds, self.settings, self.aspect)
        self.windows = WindowBuilder(
            self.settings,
            source_size=(width, height),
            change_mask_params=change_mask_params or DEFAULT_CHANGE_MASK_PARAMS,
            with_clips=with_clips,
            source=source,
        )
        self.segmenter = Segmenter(self.settings)
        self._last_t_ms: int | None = None
        primary = self.classifier.primary
        primary_name = "none" if primary is None else f"{primary.name} {primary.version}"
        logger.info(
            "interactions: zone rules %s, primary %s, clips %s", self.classifier.rules.version, primary_name, with_clips
        )

    def process(self, frame: ObservationFrame, change_mask: np.ndarray | None = None) -> list[InteractionEvent]:
        """Run one frame (and its change mask, float [S, S] in [0, 1]) through the step; return the events it closes."""
        validate(frame, "observation_frame")
        t_ms = frame["t_ms"]
        # An older or repeated frame would put a pair's events out of order (safety rule: time runs forward).
        if self._last_t_ms is not None and t_ms <= self._last_t_ms:
            raise ValueError(f"time runs forward: frame at t_ms {t_ms} after {self._last_t_ms}")
        self._last_t_ms = t_ms

        pairs = candidate_pairs(frame["detections"], self.kinds, self.settings.candidate_max_gap, self.aspect)
        rows = self.features.update(frame, pairs)
        closed = []
        for window in self.windows.update(frame, rows, change_mask):
            labels, classifier = self.classifier.classify_window(window)
            closed += self.segmenter.add(window.features[-1], labels, classifier)
        closed += self.segmenter.close_stale(t_ms)
        return sorted(closed, key=event_order)

    def finish(self) -> list[InteractionEvent]:
        """Close every open event at the end of the stream."""
        return self.segmenter.close_all()


def live_consumer(state: StateClient, clock: Clock, classifier: FallbackClassifier | None = None):
    """The vision frame loop's consumer for live mode: each FrameOutput runs through the step, and the events it
    closes are written with state.record_interaction.

    Zone rules answer unless `classifier` brings an adopted primary (safety rule 7: never the ConvLSTM by default).
    The step is built at the first frame, from the zones in `state` and the camera's frame size, so calibration has
    to land before the loop starts. Errors are raised, never swallowed, so live mode can hold every plate when this
    step stops working. A write the database refuses (StateUnavailable) leaves its events buffered for the next
    frame and still raises: nothing is dropped (safety rule 1), and the failure shows.
    """
    settings = load_interaction_settings()
    writer = EventWriter(state, clock, settings.retry_ms)
    step: InteractionStep | None = None

    def consume(output) -> None:
        """One kitchen.vision.common.FrameOutput: observation, change_mask, width, height."""
        nonlocal step
        if step is None:
            step = InteractionStep(
                state.list_zones(),
                settings=settings,
                classifier=classifier,
                source_size=(output.width, output.height),
                with_clips=classifier is not None and classifier.primary is not None,
            )
        waiting = writer.write(step.process(output.observation, output.change_mask))
        if waiting:
            raise StateUnavailable(f"{waiting} interaction events are waiting for the database")

    return consume


def read_zones(path: Path) -> list[Zone]:
    """Read a zones file ({"zones": [{"zone_id", "kind", "polygon"}]}, vision's format). Only IDs and kinds are used."""
    data = json.loads(Path(path).read_text())
    return [Zone(zone["zone_id"], zone["kind"], [tuple(point) for point in zone["polygon"]]) for zone in data["zones"]]


def read_frames(path: Path) -> list[ObservationFrame]:
    """Read a frames.jsonl file, one ObservationFrame per line (InteractionStep validates each)."""
    lines = Path(path).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def read_change_masks(path: Path, frame_count: int) -> np.ndarray:
    """Read change.npz (uint8 [N, S, S]); refuse it unless it has exactly one mask per frame."""
    with np.load(path) as data:
        masks = data["change"]
    if masks.shape[0] != frame_count:
        raise ValueError(f"{path} has {masks.shape[0]} masks for {frame_count} frames")
    return masks


def read_manifest(frames_path: Path) -> dict:
    """The recording's manifest.json next to frames.jsonl, or {} when there is none (hand-written frames)."""
    path = Path(frames_path).parent / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else {}


def replay(
    frames: list[ObservationFrame],
    zones: list[Zone],
    settings: InteractionSettings,
    *,
    masks: np.ndarray | None = None,
    manifest: dict | None = None,
) -> list[InteractionEvent]:
    """Run recorded frames (and optionally their stored masks) through the step; return every event in edge order."""
    manifest = manifest or {}
    source_size = (manifest["width"], manifest["height"]) if "width" in manifest else None
    step = InteractionStep(
        zones,
        settings=settings,
        source_size=source_size,
        change_mask_params=manifest.get("change_mask_params"),
        with_clips=masks is not None,
        source="recording",
    )
    events = []
    for index, frame in enumerate(frames):
        # Stored masks are uint8; the clip builder takes floats in [0, 1] (contracts/apis.md: divide by 255).
        mask = None if masks is None else masks[index].astype(np.float32) / 255
        events += step.process(frame, mask)
    events += step.finish()
    return sorted(events, key=event_order)


def write_events(events: list[InteractionEvent], path: Path) -> None:
    """Write events as JSONL, one canonical line each (sorted keys), so equal runs give equal files."""
    lines = [json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events]
    Path(path).write_text("".join(lines))


def main(argv: list[str] | None = None) -> int:
    """The CLI: frames.jsonl (+ change.npz) and zones.json in, events.jsonl out."""
    parser = argparse.ArgumentParser(description="Turn recorded frames into interaction events with zone rules.")
    parser.add_argument("--frames", type=Path, required=True, help="frames.jsonl, one ObservationFrame per line")
    parser.add_argument("--zones", type=Path, required=True, help="zones.json, as vision writes it")
    parser.add_argument("--out", type=Path, required=True, help="where to write events.jsonl")
    parser.add_argument("--change", type=Path, help="change.npz from the same recording; adds a clip to every window")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    settings = load_interaction_settings()
    frames = read_frames(args.frames)
    masks = read_change_masks(args.change, len(frames)) if args.change else None
    events = replay(frames, read_zones(args.zones), settings, masks=masks, manifest=read_manifest(args.frames))
    write_events(events, args.out)
    print(f"{len(events)} events from {len(frames)} frames -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
