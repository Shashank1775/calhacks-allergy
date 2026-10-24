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
from kitchen.vision.plates import plates_at_spot, plates_last_seen_at_spot, start_plate
from kitchen.vision.zones import load_zones

ZONES = load_zones(Path(__file__).parent / "fixtures" / "zones.sample.json")

# The test frame is stamped 5000 and taps come at 6000, exactly this old: the oldest frame that may still bind.
MAX_AGE_MS = 1000
RECENT_MS = 1000  # the tracker's lost buffer: a plate last seen at the spot this recently may still be there


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


def bind(state, observations, ticket_id="t7", t_ms=6000, zones=ZONES):
    """start_plate with the test limits; `observations` is one frame or a list of them, oldest first."""
    if isinstance(observations, dict):
        observations = [observations]
    return start_plate(state, observations, zones, ticket_id, t_ms, MAX_AGE_MS, RECENT_MS)


def refusal(state, observations, ticket_id="t7", t_ms=6000):
    """The BindRefused code start_plate raises."""
    with pytest.raises(BindRefused) as refused:
        bind(state, observations, ticket_id, t_ms)
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
    assert bind(state, observation) == "plate-1"
    assert state.get_plate_binding("plate-1") == PlateBinding("plate-1", "t7", 6000)
    assert state.get_plate_binding("plate-2") is None


@pytest.mark.safety
def test_plates_start_refuses_before_the_first_frame():
    state = FakeStateClient()
    assert refusal(state, []) == "no_plate_at_spot"


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
    assert bind(state, observation, t_ms=5000) == "plate-1"


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
        bind(state, observation, zones=no_spot)
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
    assert bind(state, observation) == "plate-1"
    assert bind(state, later, t_ms=9000) == "plate-1"
    assert state.get_plate_binding("plate-1") == PlateBinding("plate-1", "t7", 6000)


@pytest.mark.safety
def test_plates_start_passes_state_unavailable_through():
    state = FakeStateClient()
    state.set_unavailable()
    with pytest.raises(StateUnavailable):
        bind(state, frame(detection("plate-1", "plate", "plating")))


@pytest.mark.safety
def test_plates_start_does_not_bind_when_the_binding_check_fails():
    state = FakeStateClient()
    state.fail_next(1)
    with pytest.raises(StateUnavailable):
        bind(state, frame(detection("plate-1", "plate", "plating")))
    assert state.get_plate_binding("plate-1") is None


def test_plates_at_spot_lists_only_plates_in_a_plating_spot():
    observation = frame(detection("plate-1", "plate", "plating"), detection("bin-1", "bin", "plating"))
    assert plates_at_spot(observation, ZONES) == ["plate-1"]


@pytest.mark.safety
def test_plates_start_refuses_when_a_second_plate_was_missed_in_the_latest_frame():
    """Two plates sit on the spot; the detector misses plate-2 for one frame. Binding plate-1 then would be a guess."""
    state = FakeStateClient()
    both = frame(detection("plate-1", "plate", "plating"), detection("plate-2", "plate", "plating"), t_ms=4967)
    one_missed = frame(detection("plate-1", "plate", "plating"))
    assert refusal(state, [both, one_missed]) == "multiple_plates_at_spot"
    assert state.get_plate_binding("plate-1") is None


def test_plates_start_binds_when_the_other_plate_was_seen_leaving_the_spot():
    state = FakeStateClient()
    both = frame(detection("plate-1", "plate", "plating"), detection("plate-2", "plate", "plating"), t_ms=4900)
    moved_off = frame(detection("plate-1", "plate", "plating"), detection("plate-2", "plate", "pass"), t_ms=4967)
    only_one = frame(detection("plate-1", "plate", "plating"))
    assert bind(state, [both, moved_off, only_one]) == "plate-1"


def test_plates_start_forgets_a_plate_last_seen_at_the_spot_before_the_recent_window():
    state = FakeStateClient()
    long_ago = frame(detection("plate-2", "plate", "plating"), t_ms=5000 - RECENT_MS - 1)
    now = frame(detection("plate-1", "plate", "plating"))
    assert bind(state, [long_ago, now]) == "plate-1"


def test_plates_last_seen_at_spot_uses_each_plates_latest_sighting():
    earlier = frame(detection("plate-1", "plate", "plating"), detection("plate-2", "plate", "pass"), t_ms=4000)
    later = frame(detection("plate-1", "plate", "pass"), detection("plate-3", "plate", "plating"), t_ms=4500)
    assert plates_last_seen_at_spot([earlier, later], ZONES) == {"plate-3"}
