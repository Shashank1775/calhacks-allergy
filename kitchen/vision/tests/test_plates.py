"""Tests for "start plate" binding: exactly one plate at the plating spot binds; anything else is refused.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.plates, tests/fixtures/zones.sample.json, FakeStateClient
Emits: nothing
Rules: fail closed: a stale or future frame, 0 plates, 2+ plates or a plate bound to another ticket are refused, never
       guessed or rebound
"""

from pathlib import Path

import pytest

from kitchen.shared.fakes import FakeStateClient
from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import PlateBinding, validate
from kitchen.vision.common import BindRefused
from kitchen.vision.plates import plates_at_spot, start_plate
from kitchen.vision.zones import load_zones

ZONES = load_zones(Path(__file__).parent / "fixtures" / "zones.sample.json")

# The test frame is stamped 5000 and taps come at 6000, exactly this old: the oldest frame that may still bind.
MAX_AGE_MS = 1000


def detection(entity_id, kind, zone):
    """One observation detection in the given zone."""
    return {
        "track_id": 1,
        "entity_id": entity_id,
        "kind": kind,
        "class_name": "bowl" if kind in ("plate", "bin") else "knife",
        "box": [0.4, 0.45, 0.5, 0.55],
        "confidence": 0.9,
        "zone": zone,
        "origin": "clean_stack",
    }


def frame(*detections, t_ms=5000):
    """A valid ObservationFrame holding `detections`, stamped `t_ms`."""
    return validate(
        {"frame_id": 7, "t_ms": t_ms, "camera_id": "cam0", "detections": list(detections)}, "observation_frame"
    )


def refusal(state, observation, ticket_id="t7", t_ms=6000):
    """The BindRefused code start_plate raises."""
    with pytest.raises(BindRefused) as refused:
        start_plate(state, observation, ZONES, ticket_id, t_ms, MAX_AGE_MS)
    return refused.value.code


def test_plates_start_binds_the_one_plate_at_the_spot():
    state = FakeStateClient()
    observation = frame(
        detection("plate-1", "plate", "plating"),
        detection("plate-2", "plate", "pass"),
        detection("plate-3", "plate", None),
        detection("bin-1", "bin", "slot-1"),
        detection("tool-knife-1", "tool", "plating"),
    )
    assert start_plate(state, observation, ZONES, "t7", 6000, MAX_AGE_MS) == "plate-1"
    assert state.get_plate_binding("plate-1") == PlateBinding("plate-1", "t7", 6000)
    assert state.get_plate_binding("plate-2") is None


@pytest.mark.safety
def test_plates_start_refuses_before_the_first_frame():
    state = FakeStateClient()
    assert refusal(state, None) == "no_plate_at_spot"


@pytest.mark.safety
@pytest.mark.parametrize("tap_t_ms", [6001, 605000], ids=["just-too-old", "camera-stalled"])
def test_plates_start_refuses_a_stale_frame(tap_t_ms):
    # A stalled camera's last frame may show a plate that has since been swapped for another guest's.
    state = FakeStateClient()
    observation = frame(detection("plate-1", "plate", "plating"))
    assert refusal(state, observation, t_ms=tap_t_ms) == "no_plate_at_spot"
    assert state.get_plate_binding("plate-1") is None


@pytest.mark.safety
@pytest.mark.parametrize("tap_t_ms", [4999, 1000])
def test_plates_start_refuses_a_frame_stamped_after_the_tap(tap_t_ms):
    state = FakeStateClient()
    observation = frame(detection("plate-1", "plate", "plating"))
    assert refusal(state, observation, t_ms=tap_t_ms) == "no_plate_at_spot"
    assert state.get_plate_binding("plate-1") is None


def test_plates_start_binds_from_a_frame_stamped_at_the_tap():
    state = FakeStateClient()
    observation = frame(detection("plate-1", "plate", "plating"))
    assert start_plate(state, observation, ZONES, "t7", 5000, MAX_AGE_MS) == "plate-1"


@pytest.mark.safety
def test_plates_start_refuses_with_no_plate_at_the_spot():
    state = FakeStateClient()
    observation = frame(
        detection("plate-2", "plate", "pass"),
        detection("plate-3", "plate", None),
        detection("tool-knife-1", "tool", "plating"),
    )
    assert refusal(state, observation) == "no_plate_at_spot"
    assert state.get_plate_binding("plate-2") is None
    assert state.get_plate_binding("plate-3") is None


@pytest.mark.safety
def test_plates_start_refuses_with_two_plates_at_the_spot():
    state = FakeStateClient()
    observation = frame(detection("plate-1", "plate", "plating"), detection("plate-2", "plate", "plating"))
    assert refusal(state, observation) == "multiple_plates_at_spot"
    assert state.get_plate_binding("plate-1") is None
    assert state.get_plate_binding("plate-2") is None


@pytest.mark.safety
def test_plates_start_counts_detections_so_a_duplicated_id_is_refused():
    state = FakeStateClient()
    observation = frame(detection("plate-1", "plate", "plating"), detection("plate-1", "plate", "plating"))
    assert refusal(state, observation) == "multiple_plates_at_spot"
    assert state.get_plate_binding("plate-1") is None


@pytest.mark.safety
def test_plates_start_with_no_plating_spot_zone_refuses():
    state = FakeStateClient()
    observation = frame(detection("plate-1", "plate", "plating"))
    no_spot = [zone for zone in ZONES if zone.kind != "plating_spot"]
    with pytest.raises(BindRefused) as refused:
        start_plate(state, observation, no_spot, "t7", 6000, MAX_AGE_MS)
    assert refused.value.code == "no_plate_at_spot"


@pytest.mark.safety
def test_plates_start_never_rebinds_a_plate_bound_to_another_ticket():
    state = FakeStateClient()
    state.bind_plate("plate-1", "t3", 1000)
    observation = frame(detection("plate-1", "plate", "plating"))
    assert refusal(state, observation, ticket_id="t7") == "plate_already_bound"
    assert state.get_plate_binding("plate-1") == PlateBinding("plate-1", "t3", 1000)


def test_plates_start_again_for_the_same_ticket_returns_the_same_plate():
    state = FakeStateClient()
    observation = frame(detection("plate-1", "plate", "plating"))
    later = frame(detection("plate-1", "plate", "plating"), t_ms=8500)
    assert start_plate(state, observation, ZONES, "t7", 6000, MAX_AGE_MS) == "plate-1"
    assert start_plate(state, later, ZONES, "t7", 9000, MAX_AGE_MS) == "plate-1"
    assert state.get_plate_binding("plate-1") == PlateBinding("plate-1", "t7", 6000)


@pytest.mark.safety
def test_plates_start_passes_state_unavailable_through():
    state = FakeStateClient()
    state.set_unavailable()
    with pytest.raises(StateUnavailable):
        start_plate(state, frame(detection("plate-1", "plate", "plating")), ZONES, "t7", 6000, MAX_AGE_MS)


@pytest.mark.safety
def test_plates_start_does_not_bind_when_the_binding_check_fails():
    state = FakeStateClient()
    state.fail_next(1)
    with pytest.raises(StateUnavailable):
        start_plate(state, frame(detection("plate-1", "plate", "plating")), ZONES, "t7", 6000, MAX_AGE_MS)
    assert state.get_plate_binding("plate-1") is None


def test_plates_at_spot_lists_only_plates_in_a_plating_spot():
    observation = frame(detection("plate-1", "plate", "plating"), detection("bin-1", "bin", "plating"))
    assert plates_at_spot(observation, ZONES) == ["plate-1"]
