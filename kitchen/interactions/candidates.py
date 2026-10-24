"""Candidate pairs per frame: hand-tool, tool-bin, tool-plate, hand-bin, hand-plate, within a distance or overlap cutoff.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: observation_frame detections; zone kinds (zone ID -> kind, from the zones file or StateClient.list_zones)
Emits: (a, b) detection pairs for features.py, the actor (hand or tool) always as a
Rules: 6 pairs come from geometry, kind and zone kind only, never from what an entity carries; a wash is a self-loop,
       so a tool or hand inside a wash zone is also paired with itself, and only that self pair can be judged a wash

A pair's order is fixed by kind: a hand comes before a tool, and either comes before a bin or plate. So a is the thing
that moves and b the thing it reaches, which is what interaction_features.inside_zone and the scoop direction (bin to
tool, b to a) assume.
"""

from kitchen.shared.models import Zone

# The five kinds of pair worth judging, as (a kind, b kind). Bins and plates don't move on their own, and two tools or
# two hands rarely carry food between them, so those pairs are left out to keep the per-frame work small.
PAIR_KINDS = {("hand", "tool"), ("tool", "bin"), ("tool", "plate"), ("hand", "bin"), ("hand", "plate")}

# What can be washed. Plates and bins leave the line to be washed, so a wash seen mid-shift is a tool or a hand.
WASHABLE_KINDS = ("hand", "tool")

# A normalized [x1, y1, x2, y2] box.
Box = list[float]


def zone_kinds(zones: list[Zone]) -> dict[str, str]:
    """Map each zone ID to its kind. Features only ever see the kind, never the ID (blind classifier)."""
    return {zone.zone_id: zone.kind for zone in zones}


def zone_kind_of(zone_id: str | None, kinds: dict[str, str]) -> str:
    """The kind of the zone a detection is in: "none" outside every zone, "other" for a zone the calibration lacks.

    An unknown zone ID never becomes a wash or a bin slot, so a calibration mismatch can't invent a wash.
    """
    if zone_id is None:
        return "none"
    return kinds.get(zone_id, "other")


def box_gap(a: Box, b: Box, aspect: float) -> float:
    """The gap between two boxes' edges in frame widths: 0 when they touch or overlap.

    aspect is frame height / width: a normalized y difference is that many widths.
    """
    gap_x = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    gap_y = max(0.0, max(a[1], b[1]) - min(a[3], b[3])) * aspect
    return (gap_x**2 + gap_y**2) ** 0.5


def ordered_pair(first: dict, second: dict) -> tuple[dict, dict] | None:
    """Return the two detections as (a, b) if their kinds make a candidate pair, else None."""
    if (first["kind"], second["kind"]) in PAIR_KINDS:
        return first, second
    if (second["kind"], first["kind"]) in PAIR_KINDS:
        return second, first
    return None


def candidate_pairs(
    detections: list[dict], kinds: dict[str, str], max_gap: float, aspect: float
) -> list[tuple[dict, dict]]:
    """Every pair worth judging in one frame, sorted by (a_id, b_id) so a replay judges them in the same order.

    A pair is a candidate when its kinds are one of PAIR_KINDS and the boxes are within max_gap frame widths. A tool
    or hand inside a wash zone is also paired with itself, so zone rules can judge a wash as a self-loop.
    """
    pairs = []
    for i, first in enumerate(detections):
        for second in detections[i + 1 :]:
            pair = ordered_pair(first, second)
            if pair is not None and box_gap(pair[0]["box"], pair[1]["box"], aspect) <= max_gap:
                pairs.append(pair)

    for detection in detections:
        if detection["kind"] in WASHABLE_KINDS and zone_kind_of(detection.get("zone"), kinds) == "wash":
            pairs.append((detection, detection))

    return sorted(pairs, key=lambda pair: (pair[0]["entity_id"], pair[1]["entity_id"]))
