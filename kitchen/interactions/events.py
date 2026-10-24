"""Segments per-frame labels into InteractionEvents and streams them to the database; also the JSONL CLI.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: observation_frame stream (+ change masks), zones, candidates.py, features.py, windows.py, classifier.py,
       StateClient, Clock
Emits: interaction_event via StateClient.record_interaction (interaction table) and events.jsonl
Rules: 5 frames must move forward, t_start_ms <= t_end_ms, each pair and action's events come out in time order and
       never overlap, edge order follows cause and effect, and event IDs are deterministic, so a replay writes no
       duplicates; 1 events are buffered and retried on StateUnavailable, never dropped; a transfer's first hit is
       written the frame it is seen; a wash is a self-loop (a_id == b_id), only a self pair can make one, only with
       probability at least wash_floor, one rinse is one wash, and no wash comes before a transfer it followed;
       "unimportant" makes no edge

How a frame becomes edges: candidate pairs (candidates.py) -> feature rows (features.py) -> one PairWindow per pair
(windows.py) -> labels from the classifier wrapper (classifier.py) -> the Segmenter here, which turns each frame's
labels into events (its docstring says how).

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


# A transfer's open event is keyed by pair and action, whichever classifier answered each frame.
Key = tuple[str, str, str]


@dataclass(frozen=True)
class Hit:
    """One frame's label on one pair that can become an edge, and the classifier that gave it."""

    a_id: str
    b_id: str
    b_kind: str
    action: str
    t_ms: int
    frame_id: int
    probability: float
    classifier: dict

    @property
    def key(self) -> Key:
        """The pair and action."""
        return (self.a_id, self.b_id, self.action)


@dataclass
class Segment:
    """An open event: consecutive hits of one action on one pair."""

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

    def extend(self, hit: Hit) -> None:
        """Add a hit. A transfer keeps its highest probability, a wash its lowest (a wash lowers risk), and the event
        names the classifier that gave that probability."""
        self.t_end_ms = hit.t_ms
        self.frame_end = hit.frame_id
        if self.action == "clean.wash":
            stronger = hit.probability < self.probability
        else:
            stronger = hit.probability > self.probability
        if stronger:
            self.probability = hit.probability
            self.classifier = hit.classifier

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


def segment_from(hit: Hit) -> Segment:
    """A new segment holding one hit."""
    return Segment(
        a_id=hit.a_id,
        b_id=hit.b_id,
        b_kind=hit.b_kind,
        action=hit.action,
        classifier=hit.classifier,
        t_start_ms=hit.t_ms,
        t_end_ms=hit.t_ms,
        frame_start=hit.frame_id,
        frame_end=hit.frame_id,
        probability=hit.probability,
    )


class Segmenter:
    """Turns each frame's labels into events, so edges reach the database early and apply in cause-and-effect order.

    The graph applies edges in (t_start_ms, t_end_ms, event_id) order, so an event counts as happening at its start:
    - A touch is a transfer's hits on one pair and action less than merge_gap_ms apart. Its first hit is written at
      once as a one-hit event, so a plate carried to the pass by a dirty hand has its edge when it arrives. The rest
      is one event from the next hit, written merge_gap_ms after the touch's last hit, or cut every max_event_ms.
    - When a new touch starts, every other open transfer on either of its entities is cut there and its rest starts
      after it (written at once, again a new touch), so what the new touch brings is carried on: a dirty hand that
      grabs a spoon resting on a plate dirties the plate. Transfers on the same pair carry nothing new and aren't cut.
    - A wash is one event per rinse, bridged across a loss of up to wash_gap_ms. It counts only once no transfer has
      touched the entity for wash_min_dwell_ms, so it never comes before a touch during the soak, and a transfer on
      the entity ends its open wash. A new wash on an entity no transfer has touched since its last one is dropped:
      it can't make it cleaner than the safe reading, and two washes from one rinse would clean twice.
    """

    def __init__(self, settings: InteractionSettings) -> None:
        self.settings = settings
        self._open: dict[Key, Segment] = {}  # open transfers
        self._washes: dict[str, Segment] = {}  # open washes, by entity
        self._last_hit_ms: dict[Key, int] = {}  # each recent touch's last hit, to tell a new touch from a going one
        self._write_next: set[Key] = set()  # transfers cut for a new touch: their next hit is written at once
        self._last_transfer_ms: dict[str, int] = {}  # entity -> its last transfer hit, within wash_min_dwell_ms
        self._washed: set[str] = set()  # entities washed, and touched by no transfer since

    def edge_hits(self, latest: InteractionFeatures, labels: Labels, classifier: dict) -> list[Hit]:
        """The labels that can become edges, one hit per action.

        A wash only on a self pair and only at wash_floor or above; a transfer only between two entities and only at
        event_floor or above. A transfer on a self pair means nothing and a wash between two entities would clean
        the wrong thing, so both are dropped. An action named twice keeps its strongest (a wash its weakest).
        """
        self_pair = latest["a_id"] == latest["b_id"]
        kept: dict[str, float] = {}
        for action, probability in labels:
            if action == "unimportant":
                continue
            if action == "clean.wash":
                keep = self_pair and probability >= self.settings.wash_floor
            else:
                keep = not self_pair and probability >= self.settings.event_floor
            if keep:
                pick = min if action == "clean.wash" else max
                kept[action] = pick(kept.get(action, probability), probability)
        a_id, b_id, b_kind, t_ms, frame_id = (latest[name] for name in ("a_id", "b_id", "b_kind", "t_ms", "frame_id"))
        return [Hit(a_id, b_id, b_kind, action, t_ms, frame_id, p, classifier) for action, p in kept.items()]

    def add_frame(self, t_ms: int, judged: list[tuple[InteractionFeatures, Labels, dict]]) -> list[InteractionEvent]:
        """Add one frame's judged windows, each (newest feature row, labels, classifier). Returns the events to write
        now, in edge order: first hits, cut and finished transfers, finished washes.

        Transfers go first, so a transfer on an entity this frame already stops its wash this frame.
        """
        hits = [hit for latest, labels, classifier in judged for hit in self.edge_hits(latest, labels, classifier)]
        written = []
        new_touches: list[Hit] = []
        for hit in hits:
            if hit.action != "clean.wash":
                written += self.add_transfer(hit, new_touches)
        written += self.cut_for_new_touches(t_ms, new_touches)
        for hit in hits:
            if hit.action == "clean.wash":
                written += self.add_wash(hit)
        written += self.close_stale(t_ms)
        return sorted(written, key=event_order)

    def add_transfer(self, hit: Hit, new_touches: list[Hit]) -> list[InteractionEvent]:
        """One transfer hit: extend its open event, or start one. Returns any event to write now.

        A new touch's first hit, and the first hit after a cut for a new touch, is written at once as a one-hit
        event and noted in new_touches; the touch goes on from its next hit.
        """
        written = []
        for entity_id in (hit.a_id, hit.b_id):
            written += self.note_transfer(entity_id, hit.t_ms)

        key = hit.key
        last_ms = self._last_hit_ms.get(key)
        new_touch = last_ms is None or hit.t_ms - last_ms > self.settings.merge_gap_ms
        self._last_hit_ms[key] = hit.t_ms
        segment = self._open.get(key)
        # A touch that ended, or one that ran max_event_ms, is written; this hit starts a new event.
        if segment is not None and (new_touch or hit.t_ms - segment.t_start_ms > self.settings.max_event_ms):
            written.append(self._open.pop(key).event())
            segment = None

        if segment is not None:
            segment.extend(hit)
        elif new_touch or key in self._write_next:
            self._write_next.discard(key)
            written.append(segment_from(hit).event())
            new_touches.append(hit)
        else:
            self._open[key] = segment_from(hit)
        return written

    def note_transfer(self, entity_id: str, t_ms: int) -> list[InteractionEvent]:
        """A transfer touched the entity: its wash dwell starts over, it may be washed again, and an open wash on it
        ends here, so no wash spans a transfer. Returns that wash's event, if any."""
        self._last_transfer_ms[entity_id] = t_ms
        self._washed.discard(entity_id)
        wash = self._washes.pop(entity_id, None)
        return [] if wash is None else [wash.event()]

    def cut_for_new_touches(self, t_ms: int, new_touches: list[Hit]) -> list[InteractionEvent]:
        """Cut every open transfer that started before this frame and shares an entity with a new touch on another
        pair. Its part so far is written, and its rest starts at its next hit, after the new touch's first hit.

        Without the cut, a long transfer stamped at its start applies before the new touch for all of its length,
        and what the new touch brings never crosses it.
        """
        written = []
        for new in new_touches:
            entities = {new.a_id, new.b_id}
            for key, segment in list(self._open.items()):
                shares_an_entity = segment.a_id in entities or segment.b_id in entities
                same_pair = (segment.a_id, segment.b_id) == (new.a_id, new.b_id)
                if shares_an_entity and not same_pair and segment.t_start_ms < t_ms:
                    written.append(self._open.pop(key).event())
                    self._write_next.add(key)
        return written

    def add_wash(self, hit: Hit) -> list[InteractionEvent]:
        """One wash hit on a self pair: extend the entity's open wash, start one, or drop it. Returns any event to
        write now.

        Dropped while a transfer touched the entity less than wash_min_dwell_ms ago, so the dwell counts from the
        later of zone entry (zone rules) and that touch; and dropped when nothing touched the entity since its last
        wash.
        """
        entity_id = hit.a_id
        last_transfer_ms = self._last_transfer_ms.get(entity_id)
        if last_transfer_ms is not None and hit.t_ms - last_transfer_ms < self.settings.wash_min_dwell_ms:
            return []

        written = []
        wash = self._washes.get(entity_id)
        if wash is not None and hit.t_ms - wash.t_end_ms <= self.settings.wash_gap_ms:
            wash.extend(hit)
            return []
        if wash is not None:
            written.append(self._washes.pop(entity_id).event())
        if entity_id not in self._washed:
            self._washes[entity_id] = segment_from(hit)
            self._washed.add(entity_id)
        return written

    def close_stale(self, t_ms: int) -> list[InteractionEvent]:
        """Close every transfer with no hit for longer than merge_gap_ms and every wash with none for wash_gap_ms,
        and forget what can no longer matter."""
        s = self.settings
        stale = [key for key, segment in self._open.items() if t_ms - segment.t_end_ms > s.merge_gap_ms]
        written = [self._open.pop(key).event() for key in stale]
        rinsed = [entity_id for entity_id, wash in self._washes.items() if t_ms - wash.t_end_ms > s.wash_gap_ms]
        written += [self._washes.pop(entity_id).event() for entity_id in rinsed]

        for key in [key for key, last_ms in self._last_hit_ms.items() if t_ms - last_ms > s.merge_gap_ms]:
            del self._last_hit_ms[key]
            self._write_next.discard(key)
        settled = [
            entity for entity, last_ms in self._last_transfer_ms.items() if t_ms - last_ms >= s.wash_min_dwell_ms
        ]
        for entity_id in settled:
            del self._last_transfer_ms[entity_id]
        return written

    def close_all(self) -> list[InteractionEvent]:
        """Close every open transfer and wash (the stream ended), in edge order."""
        events = [segment.event() for segment in self._open.values()]
        events += [wash.event() for wash in self._washes.values()]
        self._open.clear()
        self._washes.clear()
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
    """The whole step for one camera: feed it frames in order, get back the events each frame writes."""

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
        """Run one frame (and its change mask, float [S, S] in [0, 1]) through the step; return the events to write."""
        validate(frame, "observation_frame")
        t_ms = frame["t_ms"]
        # An older or repeated frame would put a pair's events out of order (safety rule: time runs forward).
        if self._last_t_ms is not None and t_ms <= self._last_t_ms:
            raise ValueError(f"time runs forward: frame at t_ms {t_ms} after {self._last_t_ms}")
        self._last_t_ms = t_ms

        pairs = candidate_pairs(frame["detections"], self.kinds, self.settings.candidate_max_gap, self.aspect)
        rows = self.features.update(frame, pairs)
        judged = []
        for window in self.windows.update(frame, rows, change_mask):
            labels, classifier = self.classifier.classify_window(window)
            judged.append((window.features[-1], labels, classifier))
        return self.segmenter.add_frame(t_ms, judged)

    def finish(self) -> list[InteractionEvent]:
        """Close every open event at the end of the stream."""
        return self.segmenter.close_all()


def live_consumer(state: StateClient, clock: Clock, classifier: FallbackClassifier | None = None):
    """The vision frame loop's consumer for live mode: each FrameOutput runs through the step, and the events it
    gives (a touch's first hit among them, the frame it is seen) are written with state.record_interaction.

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
