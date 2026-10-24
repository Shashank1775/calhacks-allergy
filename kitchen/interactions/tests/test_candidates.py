"""Candidate pairs: only the five pair kinds, within the gap cutoff, actor first; self pairs only for washes.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.candidates
Emits: pytest tests
Rules: 6 pairs from geometry, kind and zone kind only; a wash is a self-loop on a tool or hand in a wash zone
"""

import pytest

from kitchen.interactions.candidates import box_gap, candidate_pairs, zone_kind_of, zone_kinds
from kitchen.interactions.tests.scenes import ZONES

KINDS = zone_kinds(ZONES)
ENTITY_IDS = {"hand": "hand-p1-left", "tool": "tool-knife-2", "bin": "bin-1", "plate": "plate-t3"}


def found(kind: str, box: list[float], zone: str | None = None, entity_id: str | None = None) -> dict:
    """A minimal detection: what candidate_pairs reads."""
    return {"entity_id": entity_id or ENTITY_IDS[kind], "kind": kind, "box": box, "zone": zone}


def pair_ids(pairs) -> list[tuple[str, str]]:
    """The (a_id, b_id) of each pair."""
    return [(a["entity_id"], b["entity_id"]) for a, b in pairs]


@pytest.mark.parametrize(
    ("a_kind", "b_kind"), [("hand", "tool"), ("tool", "bin"), ("tool", "plate"), ("hand", "bin"), ("hand", "plate")]
)
def test_each_pair_kind_is_a_candidate_with_the_actor_first(a_kind, b_kind):
    # Listed b first, so the test also checks the pair is put in actor-first order.
    detections = [found(b_kind, [0.4, 0.4, 0.5, 0.5]), found(a_kind, [0.45, 0.45, 0.55, 0.55])]

    assert pair_ids(candidate_pairs(detections, KINDS, 0.05, 1.0)) == [(ENTITY_IDS[a_kind], ENTITY_IDS[b_kind])]


@pytest.mark.parametrize(("first", "second"), [("hand", "hand"), ("tool", "tool"), ("bin", "plate"), ("bin", "bin")])
def test_other_kind_pairs_are_never_candidates(first, second):
    detections = [found(first, [0.4, 0.4, 0.5, 0.5], entity_id=f"{first}-a"), found(second, [0.4, 0.4, 0.5, 0.5])]

    assert candidate_pairs(detections, KINDS, 0.05, 1.0) == []


def test_gap_cutoff_keeps_near_pairs_and_drops_far_ones():
    plate = found("plate", [0.4, 0.4, 0.5, 0.5])
    near = found("tool", [0.54, 0.4, 0.56, 0.5])  # 0.04 widths right of the plate
    far = found("tool", [0.56, 0.4, 0.58, 0.5], entity_id="tool-knife-3")  # 0.06 widths right

    assert pair_ids(candidate_pairs([plate, near, far], KINDS, 0.05, 1.0)) == [("tool-knife-2", "plate-t3")]


def test_vertical_gaps_are_measured_in_frame_widths():
    # A normalized 0.08 vertical gap on a 16:9 frame is 0.045 widths: inside a 0.05 cutoff.
    assert box_gap([0.4, 0.4, 0.5, 0.5], [0.4, 0.58, 0.5, 0.6], 9 / 16) == pytest.approx(0.045)
    assert box_gap([0.4, 0.4, 0.5, 0.5], [0.45, 0.45, 0.6, 0.6], 9 / 16) == 0.0


@pytest.mark.safety
def test_only_tools_and_hands_in_a_wash_zone_pair_with_themselves():
    detections = [
        found("tool", [0.1, 0.8, 0.13, 0.88], zone="wash"),
        found("hand", [0.15, 0.8, 0.2, 0.85], zone="wash"),
        found("plate", [0.6, 0.8, 0.7, 0.9], zone="wash"),  # far enough from the others to pair with nothing
        found("tool", [0.5, 0.1, 0.53, 0.2], zone="slot-1", entity_id="tool-spoon-4"),
    ]

    pairs = pair_ids(candidate_pairs(detections, KINDS, 0.05, 1.0))

    self_pairs = [pair for pair in pairs if pair[0] == pair[1]]
    assert self_pairs == [("hand-p1-left", "hand-p1-left"), ("tool-knife-2", "tool-knife-2")]


@pytest.mark.safety
def test_a_zone_missing_from_the_calibration_is_never_a_wash():
    assert zone_kind_of("sink-2", KINDS) == "other"
    assert zone_kind_of(None, KINDS) == "none"
    assert candidate_pairs([found("tool", [0.1, 0.8, 0.13, 0.88], zone="sink-2")], KINDS, 0.05, 1.0) == []


def test_pairs_come_out_in_the_same_order_every_time():
    detections = [
        found("tool", [0.4, 0.4, 0.45, 0.5], entity_id="tool-knife-9"),
        found("plate", [0.4, 0.4, 0.5, 0.5]),
        found("tool", [0.42, 0.4, 0.47, 0.5], entity_id="tool-fork-1"),
    ]

    pairs = pair_ids(candidate_pairs(detections, KINDS, 0.05, 1.0))

    assert pairs == [("tool-fork-1", "plate-t3"), ("tool-knife-9", "plate-t3")]
    assert pair_ids(candidate_pairs(list(reversed(detections)), KINDS, 0.05, 1.0)) == pairs
