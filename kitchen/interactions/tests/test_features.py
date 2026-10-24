"""Features: schema-valid, allergen-blind, zone kind not zone ID, and the same at any frame rate.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.features, kitchen.interactions.candidates, scenes
Emits: pytest tests
Rules: 6 no allergen fields (the schema refuses extras), zone_kind never the zone ID; units don't depend on FPS
"""

import json

import jsonschema
import pytest

from kitchen.interactions.candidates import candidate_pairs, zone_kinds
from kitchen.interactions.features import FeatureTracker, center_distance, overlap_fraction
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import KNIFE, SCENES, ZONES
from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.models import CONTRACTS_DIR, schema_validator, validate
from kitchen.vision.common import VisionSettings

SETTINGS = InteractionSettings()
KINDS = zone_kinds(ZONES)
ASPECT = 9 / 16


def feature_rows(frames: list[dict]) -> list[dict]:
    """Every feature row the scene's frames produce, in order."""
    tracker = FeatureTracker(KINDS, SETTINGS, ASPECT)
    rows = []
    for frame in frames:
        pairs = candidate_pairs(frame["detections"], KINDS, SETTINGS.candidate_max_gap, ASPECT)
        rows += tracker.update(frame, pairs)
    return rows


def row_at(rows: list[dict], t_ms: int, a_id: str, b_id: str) -> dict:
    """The pair's feature row at t_ms."""
    return next(row for row in rows if (row["t_ms"], row["a_id"], row["b_id"]) == (t_ms, a_id, b_id))


@pytest.mark.parametrize("name", sorted(SCENES))
def test_features_contract_every_row_validates(name):
    rows = feature_rows(SCENES[name].frames())

    for row in rows:
        validate(row, "interaction_features")


@pytest.mark.safety
def test_features_contract_schema_refuses_an_allergen_field():
    row = contract_fixture("interaction_features", "knife-in-bin")
    invalid = (CONTRACTS_DIR / "fixtures" / "invalid" / "interaction_features" / "has-allergen-field.json").read_text()

    with pytest.raises(jsonschema.ValidationError):
        validate({**row, "peanut": 0.9}, "interaction_features")
    with pytest.raises(jsonschema.ValidationError):
        validate(json.loads(invalid), "interaction_features")


@pytest.mark.safety
def test_features_carry_only_the_schema_fields_and_the_zone_kind_not_its_id():
    rows = feature_rows(SCENES["scoop_then_plate"].frames())
    allowed = set(schema_validator("interaction_features").schema["properties"])

    for row in rows:
        assert set(row) <= allowed
        assert "slot-1" not in row.values()  # the bin slot's ID never reaches a classifier
    assert row_at(rows, 800, KNIFE, "bin-1")["zone_kind"] == "bin_slot"


def test_a_knife_inside_a_bin_overlaps_it_fully_and_is_inside_its_zone():
    row = row_at(feature_rows(SCENES["scoop_then_plate"].frames()), 800, KNIFE, "bin-1")

    assert row["overlap"] == 1.0
    assert row["inside_zone"] is True
    assert row["distance"] == pytest.approx(0.0)


def test_geometry_is_in_frame_widths():
    # Centers 0.2 apart vertically on a 16:9 frame are 0.1125 widths apart.
    assert center_distance([0.1, 0.1, 0.2, 0.2], [0.1, 0.3, 0.2, 0.4], ASPECT) == pytest.approx(0.1125)
    assert overlap_fraction([0.1, 0.1, 0.2, 0.2], [0.15, 0.1, 0.5, 0.5]) == pytest.approx(0.5)
    assert overlap_fraction([0.1, 0.1, 0.1, 0.2], [0.0, 0.0, 1.0, 1.0]) == 0.0  # a box with no area


@pytest.mark.parametrize("fps", [15, 30, 60])
def test_speed_is_frame_widths_per_second_at_any_frame_rate(fps):
    tracker = FeatureTracker(KINDS, SETTINGS, ASPECT)
    speeds = []
    for frame_id in range(fps):  # one second, moving right at 0.3 frame widths per second
        t_ms = round(frame_id * 1000 / fps)
        x = 0.2 + 0.3 * t_ms / 1000
        detections = [{"entity_id": KNIFE, "kind": "tool", "box": [x, 0.4, x + 0.03, 0.48]}]
        speeds.append(tracker.track_speeds(t_ms, detections)[KNIFE])

    assert speeds[0] == 0.0  # first sighting: no motion yet
    assert speeds[-1] == pytest.approx(0.3)


def test_features_are_stable_across_15_30_and_60_fps():
    at_rate = {
        fps: row_at(feature_rows(SCENES["scoop_then_plate"].frames(fps)), 1200, KNIFE, "bin-1") for fps in (15, 30, 60)
    }

    for fps in (15, 60):
        for field in ("distance", "overlap", "speed_a", "speed_b"):
            assert at_rate[fps][field] == pytest.approx(at_rate[30][field], abs=0.02), (fps, field)
        # Dwell starts at the first frame in range, which can land up to one 15 FPS frame later.
        assert abs(at_rate[fps]["dwell_ms"] - at_rate[30]["dwell_ms"]) <= 67


def test_dwell_survives_a_short_detector_miss_and_restarts_after_a_long_one():
    tracker = FeatureTracker(KINDS, SETTINGS, ASPECT)
    knife = {"entity_id": KNIFE, "kind": "tool", "box": [0.4, 0.4, 0.43, 0.48], "zone": None}
    plate = {"entity_id": "plate-t1", "kind": "plate", "box": [0.4, 0.4, 0.56, 0.56], "zone": "plating"}

    def dwell_at(t_ms: int) -> int:
        frame = {"frame_id": t_ms, "t_ms": t_ms, "detections": [knife, plate]}
        return tracker.update(frame, [(knife, plate)])[0]["dwell_ms"]

    assert dwell_at(0) == 0
    assert dwell_at(100) == 100
    assert dwell_at(100 + SETTINGS.pair_gap_ms) == 100 + SETTINGS.pair_gap_ms  # missed for pair_gap_ms: kept
    assert dwell_at(200 + 2 * SETTINGS.pair_gap_ms) == 0  # missed for longer: starts over


@pytest.mark.safety
def test_a_self_pair_in_the_sink_bridges_a_loss_as_long_as_the_tracker_keeps_its_id_without_counting_it():
    """One rinse must stay one wash (two would clean twice): a knife unseen in the sink for up to wash_gap_ms keeps
    its dwell. The unseen time isn't counted as rinsing, though, since the knife may not have been under the water."""
    tracker = FeatureTracker(KINDS, SETTINGS, ASPECT)
    knife = {"entity_id": KNIFE, "kind": "tool", "box": [0.15, 0.78, 0.18, 0.86], "zone": "wash"}

    def dwell_at(t_ms: int) -> int:
        frame = {"frame_id": t_ms, "t_ms": t_ms, "detections": [knife]}
        return tracker.update(frame, [(knife, knife)])[0]["dwell_ms"]

    gap = SETTINGS.wash_gap_ms
    assert dwell_at(0) == 0
    assert dwell_at(100) == 100
    assert dwell_at(100 + gap) == 100  # unseen for wash_gap_ms: kept, but not counted
    assert dwell_at(200 + gap) == 200
    assert dwell_at(201 + 2 * gap) == 0  # unseen for longer: starts over


def test_the_wash_gap_covers_the_vision_trackers_lost_track_horizon():
    """Vision keeps an entity's ID through a loss of up to track_lost_ms; a wash must bridge at least that long."""
    assert SETTINGS.wash_gap_ms >= VisionSettings().track_lost_ms


def test_wrist_proximity_is_a_distance_near_a_wrist_and_null_otherwise():
    hand_rows = feature_rows(SCENES["hand_touches_plate"].frames())
    knife_rows = feature_rows(SCENES["hover"].frames())

    assert row_at(hand_rows, 600, "hand-p1-right", "plate-t1")["wrist_proximity"] == 0.0
    assert all(row["wrist_proximity"] is None for row in knife_rows)  # no hand in the hover scene
