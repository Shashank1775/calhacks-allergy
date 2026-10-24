"""InteractionFeatures per candidate pair per frame: what zone rules and the benchmark's rule baseline use.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: candidate pairs (candidates.py), observation_frame detections, zone kinds
Emits: interaction_features rows, each validated against the schema
Rules: 6 no allergen fields (the schema refuses extra fields) and zone_kind, never the zone ID; time-based units
       (frame widths, frame widths per second, milliseconds), so features don't change with FPS; 1 a pair keeps its
       dwell through a loss of up to track_gap_ms (as long as vision keeps a track), so one rinse is one wash and a
       touch confirmed after a hand was lost still shows when it began, but unseen time beyond pair_gap_ms isn't
       counted as dwell or rinsing

Units: distances in normalized frame widths (a normalized y difference is height / width widths), speeds in frame
widths per second, overlap as intersection over the smaller box.
"""

from collections import deque
from collections.abc import Sequence

from kitchen.interactions.candidates import Box, zone_kind_of
from kitchen.interactions.settings import InteractionSettings
from kitchen.shared.models import InteractionFeatures, ObservationFrame, validate


def box_center(box: Box) -> tuple[float, float]:
    """The center of a normalized box."""
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def width_distance(dx: float, dy: float, aspect: float) -> float:
    """The length of a normalized (dx, dy) step in frame widths. aspect is frame height / width."""
    return (dx**2 + (dy * aspect) ** 2) ** 0.5


def center_distance(a: Box, b: Box, aspect: float) -> float:
    """Box-center distance in frame widths."""
    (ax, ay), (bx, by) = box_center(a), box_center(b)
    return width_distance(ax - bx, ay - by, aspect)


def overlap_fraction(a: Box, b: Box) -> float:
    """Intersection over the smaller box's area, 0 to 1. A degenerate box (no area) overlaps nothing.

    The smaller box, not the union, so a knife fully inside a big bin scores 1 rather than a few percent.
    """
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    if smaller <= 0:
        return 0.0
    return min(1.0, width * height / smaller)


def point_box_distance(point: tuple[float, float], box: Box, aspect: float) -> float:
    """Distance from a point to the nearest edge of a box in frame widths: 0 inside the box."""
    x, y = point
    dx = max(box[0] - x, 0.0, x - box[2])
    dy = max(box[1] - y, 0.0, y - box[3])
    return width_distance(dx, dy, aspect)


def unseen_ms(rows: Sequence[InteractionFeatures], pair_gap_ms: int) -> int:
    """The time a pair's rows (oldest first) went unseen: every gap between rows longer than pair_gap_ms.

    A shorter gap is a missed detection or two and counts as seen. A longer one is a loss vision bridged (its track
    kept its ID), when nothing shows the pair was overlapping or in the sink.
    """
    gaps = [later["t_ms"] - earlier["t_ms"] for earlier, later in zip(rows, rows[1:], strict=False)]
    return sum(gap for gap in gaps if gap > pair_gap_ms)


def pair_ended(pair: tuple[str, str], missing_ms: int, in_view: set[str], settings: InteractionSettings) -> bool:
    """Whether a pair missing from candidates for missing_ms has ended, so its history starts over if it comes back.
    in_view is the entities seen in the latest frame the pair was missing from.

    Missing for longer than track_gap_ms, it has: vision ends a lost track by then. Missing for longer than
    pair_gap_ms while both entities are in view, it has too: they moved apart, so a touch after that is a new one.
    Otherwise one of them is out of view (a hand lost over a spoon, a knife in the sink under a hand), and the pair is
    kept. A self pair has only one entity, so a box jittering across the sink's edge doesn't split a rinse.
    """
    if missing_ms > settings.track_gap_ms:
        return True
    a_id, b_id = pair
    apart = a_id != b_id and a_id in in_view and b_id in in_view
    return apart and missing_ms > settings.pair_gap_ms


def wrist_proximity(a: dict, b: dict, detections: list[dict], aspect: float, near: float) -> float | None:
    """Distance from the nearest wrist in the frame to either box of the pair, or None if no wrist is within `near`."""
    distances = [
        min(
            point_box_distance(hand["wrist"][:2], a["box"], aspect),
            point_box_distance(hand["wrist"][:2], b["box"], aspect),
        )
        for hand in detections
        if hand.get("wrist") is not None
    ]
    nearest = min(distances, default=None)
    if nearest is None or nearest > near:
        return None
    return nearest


class FeatureTracker:
    """Turns each frame's candidate pairs into feature rows, remembering what speeds and dwell need.

    It keeps each entity's recent box centers (for speed) and when each pair became a candidate (for dwell), and
    forgets both once they are older than the settings say matter.
    """

    def __init__(self, kinds: dict[str, str], settings: InteractionSettings, aspect: float) -> None:
        self.kinds = kinds
        self.settings = settings
        self.aspect = aspect
        self._centers: dict[str, deque[tuple[int, float, float]]] = {}
        # pair -> (first t_ms, last t_ms) in range, and the time unseen that doesn't count toward dwell
        self._pair_seen: dict[tuple[str, str], tuple[int, int, int]] = {}
        self._in_view: set[str] = set()  # the entities seen in the previous frame

    def update(self, frame: ObservationFrame, pairs: list[tuple[dict, dict]]) -> list[InteractionFeatures]:
        """Record this frame's detections and return one validated feature row per candidate pair."""
        t_ms = frame["t_ms"]
        speeds = self.track_speeds(t_ms, frame["detections"])
        rows = []
        for a, b in pairs:
            row = {
                "t_ms": t_ms,
                "frame_id": frame["frame_id"],
                "a_id": a["entity_id"],
                "b_id": b["entity_id"],
                "a_kind": a["kind"],
                "b_kind": b["kind"],
                "distance": center_distance(a["box"], b["box"], self.aspect),
                "overlap": overlap_fraction(a["box"], b["box"]),
                # a is inside the zone b occupies: a tool in a bin's slot, or a washed tool in the wash zone.
                "inside_zone": a.get("zone") is not None and a.get("zone") == b.get("zone"),
                "zone_kind": zone_kind_of(b.get("zone"), self.kinds),
                "speed_a": speeds[a["entity_id"]],
                "speed_b": speeds[b["entity_id"]],
                "dwell_ms": self.track_dwell(t_ms, a["entity_id"], b["entity_id"]),
                "wrist_proximity": wrist_proximity(a, b, frame["detections"], self.aspect, self.settings.wrist_near),
            }
            rows.append(validate(row, "interaction_features"))
        self._in_view = {detection["entity_id"] for detection in frame["detections"]}
        self.forget_stale_pairs(t_ms)
        return rows

    def track_speeds(self, t_ms: int, detections: list[dict]) -> dict[str, float]:
        """Each detection's box-center speed in frame widths per second, averaged over speed_lookback_ms.

        An entity seen for the first time has speed 0. Entities not seen within the lookback are forgotten.
        """
        lookback = self.settings.speed_lookback_ms
        speeds = {}
        for detection in detections:
            history = self._centers.setdefault(detection["entity_id"], deque())
            history.append((t_ms, *box_center(detection["box"])))
            while t_ms - history[0][0] > lookback:
                history.popleft()
            then_ms, then_x, then_y = history[0]
            _, now_x, now_y = history[-1]
            elapsed_s = (t_ms - then_ms) / 1000
            moved = width_distance(now_x - then_x, now_y - then_y, self.aspect)
            speeds[detection["entity_id"]] = moved / elapsed_s if elapsed_s > 0 else 0.0

        for entity_id in [key for key, history in self._centers.items() if t_ms - history[-1][0] > lookback]:
            del self._centers[entity_id]
        return speeds

    def track_dwell(self, t_ms: int, a_id: str, b_id: str) -> int:
        """How long the pair has been a candidate, across gaps until the pair ends (pair_ended).

        A gap up to pair_gap_ms (a missed detection or two) counts. A longer one (a knife in the sink with a hand over
        it) is bridged but not counted, since nothing shows the knife was under the water then. A pair back in the
        first frame after it ended (seen apart in the frame before) starts over here.
        """
        pair = (a_id, b_id)
        first, last, unseen = self._pair_seen.get(pair, (t_ms, t_ms, 0))
        gap = t_ms - last
        if pair_ended(pair, gap, self._in_view, self.settings):
            first, unseen = t_ms, 0
        elif gap > self.settings.pair_gap_ms:
            unseen += gap
        self._pair_seen[pair] = (first, t_ms, unseen)
        return t_ms - first - unseen

    def forget_stale_pairs(self, t_ms: int) -> None:
        """Drop the pairs that ended (pair_ended); if they come back, their dwell starts over."""
        stale = [
            pair
            for pair, (_, last, _) in self._pair_seen.items()
            if pair_ended(pair, t_ms - last, self._in_view, self.settings)
        ]
        for pair in stale:
            del self._pair_seen[pair]
