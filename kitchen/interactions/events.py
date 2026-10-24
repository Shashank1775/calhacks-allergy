"""Segments per-frame labels into InteractionEvents and streams them to the database; also the JSONL CLI.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: observation_frame stream (+ change masks), zones, candidates.py, features.py, windows.py, classifier.py,
       StateClient, Clock
Emits: interaction_event via StateClient.record_interaction (interaction table) and events.jsonl
Rules: 5 frames must move forward, t_start_ms <= t_end_ms, each pair and action's events come out in time order and
       never overlap, edge order follows cause and effect, and event IDs are deterministic, so a replay writes no
       duplicates; 1 events are buffered and retried on StateUnavailable, never dropped; a transfer's first hit is
       written the frame it is seen; one touch is one edge, plus one each time it carries news across; a wash is a
       self-loop (a_id == b_id), only a self pair can make one, only with probability at least wash_floor, one rinse
       is one wash, and no wash comes before a transfer it followed; "unimportant" makes no edge

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
from kitchen.interactions.zone_rules import ZoneRules, frame_interval_ms
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


def touch_began_ms(rows: list[InteractionFeatures], min_overlap: float) -> int:
    """The earliest the touch a window's rows show may have begun, for news and washes to look back to.

    Zone rules count a touch's overlap from the window's first overlapping row, across short dropouts, so it may have
    begun up to a frame before that row; one more frame allows for a late one. With no overlapping row (a primary
    that judged the whole window), the window's first row.
    """
    overlapping = [row for row in rows if row["overlap"] >= min_overlap]
    first_ms = (overlapping or rows)[0]["t_ms"]
    return first_ms - 2 * frame_interval_ms(rows)


# A touch is keyed by pair and action, whichever classifier answered each frame.
Key = tuple[str, str, str]


@dataclass(frozen=True)
class Hit:
    """One frame's label on one pair that can become an edge, the classifier that gave it, and the earliest its touch
    may have begun."""

    a_id: str
    b_id: str
    b_kind: str
    action: str
    t_ms: int
    frame_id: int
    probability: float
    classifier: dict
    began_ms: int

    @property
    def key(self) -> Key:
        """The pair and action."""
        return (self.a_id, self.b_id, self.action)


@dataclass
class Segment:
    """An event being built: a one-hit transfer edge, or a wash's consecutive hits on one entity."""

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
    direction: str | None = None  # a carry's one-way direction; None: the action's own (direction_for)
    wave: int = 0  # a carry's wave within its frame (1, 2, ...); 0 for anything else

    def extend(self, hit: Hit) -> None:
        """Add a wash hit. A wash keeps its lowest probability, since a wash lowers risk, and the event names the
        classifier that gave it."""
        self.t_end_ms = hit.t_ms
        self.frame_end = hit.frame_id
        if hit.probability < self.probability:
            self.probability = hit.probability
            self.classifier = hit.classifier

    def event(self) -> InteractionEvent:
        """The finished interaction_event, validated. Its ID depends only on what happened, so replays repeat it."""
        short_action = self.action.split(".")[-1]
        news = f"-news{self.wave}" if self.wave else ""
        event = {
            "event_id": f"ev-{self.t_start_ms}-{self.a_id}-{self.b_id}-{short_action}{news}",
            "t_start_ms": self.t_start_ms,
            "t_end_ms": self.t_end_ms,
            "a_id": self.a_id,
            "b_id": self.b_id,
            "action": self.action,
            "probability": self.probability,
            "direction": self.direction or direction_for(self.action, self.b_kind),
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


def one_hit_event(hit: Hit, probability: float, classifier: dict) -> InteractionEvent:
    """A transfer edge at one hit: a touch's first hit, or a surer one."""
    segment = segment_from(hit)
    segment.probability = probability
    segment.classifier = classifier
    return segment.event()


def carry_event(touch: "Touch", t_ms: int, wave: int, direction: str) -> InteractionEvent:
    """A touch carrying news across in the frame at t_ms, one way. It spans [t_ms, t_ms + wave]: the graph orders
    edges by start then end, so it applies after the frame's hits and earlier waves, and before the next frame's
    edges, however close the frames are."""
    segment = segment_from(touch.last_hit)
    segment.t_start_ms, segment.t_end_ms = t_ms, t_ms + wave
    segment.probability, segment.classifier = touch.probability, touch.classifier
    segment.direction, segment.wave = direction, wave
    return segment.event()


def receivers(key: Key, direction: str) -> list[str]:
    """The entities an edge on this pair can move allergens into."""
    a_id, b_id, _ = key
    return {"a_to_b": [b_id], "b_to_a": [a_id], "both": [a_id, b_id]}[direction]


# News that reached an entity: (entity, news ID, the touch it came through).
Arrival = tuple[str, int, Key]


@dataclass(frozen=True)
class News:
    """Where a piece of news began: the two entities whose touch made it, the earliest that touch may have begun, and
    the frame it happened in."""

    entities: frozenset[str]
    began_ms: int
    t_ms: int


@dataclass
class Touch:
    """A transfer going on: hits of one action on one pair, less than merge_gap_ms apart. `started_ms` is its first
    hit, `last_hit` when it was last seen, `probability` its strongest hit so far and `classifier` the one that gave
    it."""

    started_ms: int
    last_hit: Hit
    probability: float
    classifier: dict

    @property
    def direction(self) -> str:
        """Which way it can move allergens: bin to tool for a scoop from a bin, both ways otherwise."""
        return direction_for(self.last_hit.action, self.last_hit.b_kind)


class Segmenter:
    """Turns each frame's labels into events: one edge per touch, written early, applied in cause-and-effect order.

    The graph applies edges in (t_start_ms, t_end_ms, event_id) order with noisy-OR, so every edge counts as one more
    chance to move allergens, at its start. Writing a touch as several edges would raise both sides once per edge, and
    a contact's allergens would echo back and forth: a washed knife resting on a plate would hold an allergy plate.
    - A touch is a transfer's hits on one pair and action less than merge_gap_ms apart. It is one edge: its first hit,
      written at once as a one-hit event, so a plate carried to the pass by a dirty hand has its edge when it arrives.
    - News. A touch's first hit is news to the entities it can move allergens into. Once all of a frame's hits are
      in, the news crosses at once every other touch seen on such an entity since the new touch may have begun (on
      another pair): each
      writes one edge in this frame, ending a millisecond after the edge that brought the news, so the graph applies
      it after, and only from the side the news came in on, so it adds nothing the touch already moved. That edge is
      news to the far side in turn, the next wave, so news carries on along a chain of things all touching: a dirty
      hand that grabs a spoon resting on a plate dirties the plate. It looks back because a first hit comes only once
      the overlap has lasted contact_min_ms (scoop_min_ms for a scoop), counted from the window's first overlapping
      row across short dropouts, so a touch that ended in that time may have been touching when the new one began.
      InteractionStep passes when that was (touch_began_ms); a caller that can't say gets lead_ms before the hit. A
      touch that was going on but not seen since then carries the news if it is seen again within merge_gap_ms, as
      the same touch. A touch carries each piece of news once per side and per touch it came through, so news from
      parallel paths all gets across, whatever the entities are called. It
      never carries news back into the two entities whose touch made it, so a ring of touching things can't echo it.
      Transfers on the same pair carry nothing new to each other.
    - A later hit surer than the touch's strongest so far (a primary and zone rules answering in turn) writes an edge
      for the difference, (p - p_before) / (1 - p_before), so the touch's edges add up to its surest hit under
      noisy-OR. It is news like a first hit.
    - A wash is one event per rinse, bridged across a loss of up to wash_gap_ms. It counts only once no transfer has
      touched the entity for wash_min_dwell_ms, nor anything overlapped it (a touch the detector loses before it is
      confirmed), so it never comes before a touch during the soak, and a transfer or overlap ends its open wash. A wash that opened after that transfer's touch may have begun is dropped
      instead, since the touch may have come first. A new wash on an entity no transfer has touched since its last
      one is dropped: it can't make it cleaner than the safe reading, and two washes from one rinse would clean twice.
    """

    def __init__(self, settings: InteractionSettings) -> None:
        self.settings = settings
        # How long before its first hit a touch may have begun, when the caller can't say: zone rules confirm a touch
        # only after this much overlap.
        self.lead_ms = max(settings.contact_min_ms, settings.scoop_min_ms)
        # Keep touches this long after they were last seen: news may look back a whole window, and a touch may resume.
        self.keep_ms = max(settings.merge_gap_ms, settings.window_ms + self.lead_ms)
        self._touches: dict[Key, Touch] = {}  # transfers seen within keep_ms
        self._news_count = 0  # news IDs, numbered in the order the news happened
        self._news: dict[int, News] = {}  # news from the last merge_gap_ms, which a touch seen again may still owe
        self._carried: dict[Arrival, set[Key]] = {}  # each of that news's arrivals -> the touches that carried it
        self._washes: dict[str, Segment] = {}  # open washes, by entity
        self._last_touched_ms: dict[str, int] = {}  # entity -> when something last overlapped it, within the dwell
        self._washed: set[str] = set()  # entities washed, and touched by no transfer since

    def with_began(self, judgement: tuple) -> tuple:
        """A judged window as (newest feature row, labels, classifier, the earliest its touch may have begun): as given,
        or lead_ms before the newest row when the caller can't say."""
        if len(judgement) == 4:
            return judgement
        latest, labels, classifier = judgement
        return (latest, labels, classifier, latest["t_ms"] - self.lead_ms)

    def edge_hits(self, latest: InteractionFeatures, labels: Labels, classifier: dict, began_ms: int) -> list[Hit]:
        """The labels that can become edges, one hit per action, each with the earliest its touch may have begun.

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
        return [Hit(a_id, b_id, b_kind, action, t_ms, frame_id, p, classifier, began_ms) for action, p in kept.items()]

    def add_frame(self, t_ms: int, judged: list[tuple]) -> list[InteractionEvent]:
        """Add one frame's judged windows, each (newest feature row, labels, classifier) and optionally the earliest its
        touch may have begun. Returns the events to write now, in edge order: transfer edges and the news they carry
        across, then finished washes.

        Transfers go first, so a transfer on an entity this frame already stops its wash this frame. News spreads once
        all of this frame's hits are in, so touches first seen in the same frame carry each other's news whatever the
        order of their event IDs.
        """
        judged = [self.with_began(judgement) for judgement in judged]
        hits = [hit for judgement in judged for hit in self.edge_hits(*judgement)]
        written = []
        for latest, _, _, began_ms in judged:
            written += self.note_overlap(latest, began_ms)
        for hit in hits:
            if hit.action != "clean.wash":
                written += self.add_transfer(hit)
        written += self.spread_news(t_ms)
        for hit in hits:
            if hit.action == "clean.wash":
                written += self.add_wash(hit)
        written += self.close_stale(t_ms)
        return sorted(written, key=event_order)

    def add_transfer(self, hit: Hit) -> list[InteractionEvent]:
        """One transfer hit: start a touch, add a surer hit to a going one, or nothing. Returns the edge to write now,
        if any, and records the news it brings, for spread_news."""
        written = []
        for entity_id in (hit.a_id, hit.b_id):
            written += self.note_transfer(entity_id, hit.t_ms, hit.began_ms)

        touch = self._touches.get(hit.key)
        if touch is None or hit.t_ms - touch.last_hit.t_ms > self.settings.merge_gap_ms:
            touch = self._touches[hit.key] = Touch(hit.t_ms, hit, hit.probability, hit.classifier)
            probability = hit.probability
        elif hit.probability > touch.probability:
            # Only the difference crosses, so the touch's edges add up to this hit's probability under noisy-OR
            # rather than counting the touch once per hit.
            probability = (hit.probability - touch.probability) / (1.0 - touch.probability)
            touch.last_hit, touch.probability, touch.classifier = hit, hit.probability, hit.classifier
        else:
            touch.last_hit = hit
            return written  # the touch goes on: its edge is already written

        news = self.next_news()
        self._news[news] = News(frozenset((hit.a_id, hit.b_id)), hit.began_ms, hit.t_ms)
        for entity_id in receivers(hit.key, touch.direction):
            self._carried[(entity_id, news, hit.key)] = set()
        return written + [one_hit_event(hit, probability, hit.classifier)]

    def next_news(self) -> int:
        """A new news ID."""
        self._news_count += 1
        return self._news_count

    def spread_news(self, t_ms: int) -> list[InteractionEvent]:
        """Carry news across every touch on an entity it reached that owes it, in waves. Returns the edges that carry
        it.

        A touch owes news if it was going on when the news came (first seen no later), has been seen since the touch
        that made the news may have begun, carries the way the news goes, and doesn't lead back into the entities whose
        touch made it. That takes this frame's news, and news from the last merge_gap_ms for a touch seen again. Wave
        n's edges end n ms after the frame, so the graph applies a chain in order. Each touch carries each (side, news,
        touch it came through) once, so the waves end.
        """
        written = []
        offered = list(self._carried)
        wave = 0
        while offered:
            wave += 1
            carrying: dict[Key, list[Arrival]] = {}
            for arrival in offered:
                for key in self.owing(arrival):
                    self._carried[arrival].add(key)
                    carrying.setdefault(key, []).append(arrival)
            offered = []
            for key, items in carrying.items():
                touch = self._touches[key]
                sides = {entity_id for entity_id, _, _ in items}
                direction = "both" if len(sides) == 2 else ("a_to_b" if key[0] in sides else "b_to_a")
                written.append(carry_event(touch, t_ms, wave, direction))
                for entity_id, news, _ in items:
                    arrival = (key[1] if entity_id == key[0] else key[0], news, key)
                    self._carried.setdefault(arrival, set())
                    offered.append(arrival)
        return written

    def owing(self, arrival: Arrival) -> list[Key]:
        """The touches that owe this arrival of news and haven't carried it: see spread_news."""
        entity_id, news_id, via = arrival
        news = self._news[news_id]
        owing = []
        for key, touch in self._touches.items():
            if key[:2] == via[:2] or entity_id not in key[:2] or key in self._carried[arrival]:
                continue
            far = key[1] if entity_id == key[0] else key[0]
            going_then = touch.started_ms <= news.t_ms and touch.last_hit.t_ms >= news.began_ms
            if going_then and far in receivers(key, touch.direction) and far not in news.entities:
                owing.append(key)
        return owing

    def note_overlap(self, latest: InteractionFeatures, began_ms: int) -> list[InteractionEvent]:
        """Two entities overlapping, a touch or not yet one: each one's wash dwell starts over and its open wash ends
        here, so a wash never comes due, or goes on, while something is on the entity, even one the detector loses
        before its touch is confirmed. Returns any wash written. A row without an overlap (a hand-written one) counts
        as none."""
        overlapping = latest.get("overlap", 0.0) >= self.settings.contact_min_overlap
        if latest["a_id"] == latest["b_id"] or not overlapping:
            return []
        written = []
        for entity_id in (latest["a_id"], latest["b_id"]):
            written += self.end_wash(entity_id, latest["t_ms"], began_ms)
        return written

    def note_transfer(self, entity_id: str, t_ms: int, began_ms: int) -> list[InteractionEvent]:
        """A transfer touched the entity at t_ms (its touch may have begun at began_ms): as for an overlap, and it may be
        washed again, since a transfer may have dirtied it. Returns any wash written."""
        self._washed.discard(entity_id)
        return self.end_wash(entity_id, t_ms, began_ms)

    def end_wash(self, entity_id: str, t_ms: int, began_ms: int) -> list[InteractionEvent]:
        """Something is on the entity at t_ms, since began_ms as far as its window shows: its wash dwell starts over
        and an open wash on it ends here, so no wash spans a touch. Returns that wash's event, if any.

        A wash that opened at or after began_ms is dropped, not written: the touch may have begun before the wash's
        dwell was up, and a wash stamped before it would clean what it brought.
        """
        self._last_touched_ms[entity_id] = t_ms
        wash = self._washes.pop(entity_id, None)
        if wash is None or wash.t_start_ms >= began_ms:
            return []
        return [wash.event()]

    def add_wash(self, hit: Hit) -> list[InteractionEvent]:
        """One wash hit on a self pair: extend the entity's open wash, start one, or drop it. Returns any event to
        write now.

        Dropped while something overlapped the entity less than wash_min_dwell_ms ago, so the dwell counts from the
        later of zone entry (zone rules) and that; and dropped when no transfer touched the entity since its last
        wash.
        """
        entity_id = hit.a_id
        last_touched_ms = self._last_touched_ms.get(entity_id)
        if last_touched_ms is not None and hit.t_ms - last_touched_ms < self.settings.wash_min_dwell_ms:
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
        """Forget every touch not seen within keep_ms (it ended, and no news can reach back to it), close every wash
        with no hit for wash_gap_ms, and forget what can no longer matter. A touch's edges are already written, so only
        washes are written here."""
        s = self.settings
        ended = [key for key, touch in self._touches.items() if t_ms - touch.last_hit.t_ms > self.keep_ms]
        for key in ended:
            del self._touches[key]
        rinsed = [entity_id for entity_id, wash in self._washes.items() if t_ms - wash.t_end_ms > s.wash_gap_ms]
        written = [self._washes.pop(entity_id).event() for entity_id in rinsed]

        # A touch going on when news came, if it is seen again at all, is seen within merge_gap_ms of it.
        old = {news_id for news_id, news in self._news.items() if t_ms - news.t_ms > s.merge_gap_ms}
        for news_id in old:
            del self._news[news_id]
        for arrival in [arrival for arrival in self._carried if arrival[1] in old]:
            del self._carried[arrival]

        settled = [entity for entity, last_ms in self._last_touched_ms.items() if t_ms - last_ms >= s.wash_min_dwell_ms]
        for entity_id in settled:
            del self._last_touched_ms[entity_id]
        return written

    def close_all(self) -> list[InteractionEvent]:
        """Close every open wash (the stream ended), in edge order. Every touch's edges are already written."""
        events = [wash.event() for wash in self._washes.values()]
        self._touches.clear()
        self._news.clear()
        self._carried.clear()
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
            began_ms = touch_began_ms(window.features, self.settings.contact_min_overlap)
            judged.append((window.features[-1], labels, classifier, began_ms))
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
