"""live_consumer: the slot the local runner's live mode calls with every vision FrameOutput.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.events.live_consumer; kitchen.vision.common.FrameOutput; FakeStateClient, FakeClock; scenes
Emits: pytest tests
Rules: 7 zone rules unless a primary is adopted; 1 never drops an event, and a database outage is raised, not hidden
"""

import numpy as np
import pytest

from kitchen.interactions.events import live_consumer, replay
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import SCENES, ZONES
from kitchen.shared.fakes import FakeClock, FakeStateClient
from kitchen.shared.interfaces import StateUnavailable
from kitchen.vision.common import FrameOutput

MASK = np.zeros((128, 128), dtype=np.float32)


def frame_outputs(name: str) -> list[FrameOutput]:
    """A scene's frames as the frame loop hands them over (a 640 x 480 camera)."""
    return [FrameOutput(observation=frame, change_mask=MASK, width=640, height=480) for frame in SCENES[name].frames()]


def calibrated_state() -> FakeStateClient:
    """A database holding the scenes' zones, as vision's calibration leaves it."""
    state = FakeStateClient()
    state.set_zones(ZONES)
    return state


def run(consume, outputs: list[FrameOutput], clock: FakeClock) -> int:
    """Feed every output at its own time. Returns how many calls raised StateUnavailable."""
    raised = 0
    for output in outputs:
        clock.set(output.observation["t_ms"])
        try:
            consume(output)
        except StateUnavailable:
            raised += 1
    return raised


def test_live_edges_match_the_recorded_replay():
    """Every edge the live slot writes is one the recorded replay of the same frames gives, IDs included, and a
    scoop and a plate contact both land while the scene is still running."""
    state, clock = calibrated_state(), FakeClock()
    outputs = frame_outputs("scoop_then_plate")
    run(live_consumer(state, clock), outputs, clock)

    written = state.list_interactions()
    replayed = replay([output.observation for output in outputs], ZONES, InteractionSettings())
    assert written and all(event in replayed for event in written)
    assert {"transfer.scoop", "transfer.contact"} <= {event["action"] for event in written}


@pytest.mark.safety
def test_zone_rules_answer_by_default():
    """Safety rule 7: with no classifier given, every edge comes from zone rules, never the ConvLSTM."""
    state, clock = calibrated_state(), FakeClock()
    run(live_consumer(state, clock), frame_outputs("scoop_then_plate"), clock)

    assert {event["classifier"]["name"] for event in state.list_interactions()} == {"zone_rules"}


@pytest.mark.safety
def test_a_database_outage_is_raised_and_no_edge_is_dropped():
    """While the database is down the slot raises, so live mode holds every plate, and keeps the edges; once it's
    back they all land."""
    state, clock = calibrated_state(), FakeClock()
    consume = live_consumer(state, clock)
    outputs = frame_outputs("scoop_then_plate")
    consume(outputs[0])  # calibration read while the database is up
    state.set_unavailable()
    raised = run(consume, outputs[1:], clock)

    state.set_unavailable(False)
    clock.advance(InteractionSettings().retry_ms)
    after = {**outputs[-1].observation, "frame_id": outputs[-1].observation["frame_id"] + 1, "t_ms": clock.now_ms()}
    consume(FrameOutput(observation={**after, "detections": []}, change_mask=MASK, width=640, height=480))

    assert raised > 0
    replayed = replay([output.observation for output in outputs], ZONES, InteractionSettings())
    written = state.list_interactions()
    assert written and all(event in replayed for event in written)
    assert {"transfer.scoop", "transfer.contact"} <= {event["action"] for event in written}


def test_an_unreachable_database_at_the_first_frame_raises():
    """The slot can't read the calibration, so it raises instead of running with no zones (no wash could ever be
    seen without the wash zone)."""
    state, clock = calibrated_state(), FakeClock()
    state.set_unavailable()

    with pytest.raises(StateUnavailable):
        live_consumer(state, clock)(frame_outputs("scoop_then_plate")[0])
