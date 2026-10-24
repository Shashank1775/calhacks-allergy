"""Scenes whose edges, run through the partner's provenance graph, must leave the plate carrying the allergen.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.events; kitchen.graph.graph.time_order and kitchen.graph.propagate (main's graph math);
       FakeStateClient, FakeClock; scenes; the anaphylactic ticket fixture
Emits: pytest tests
Rules: 1 fail closed: one rinse is one wash, and a wash never erases what reached an entity after it entered the
       sink; 5 time runs forward: edge order follows cause and effect, and a transfer reaches the database the frame
       it is first seen, so a plate at the pass is judged with it

Each scene is one a review found letting a plate PASS. The graph applies edges in (t_start_ms, t_end_ms) order with
noisy-OR, so these tests check the edges the step writes, through the graph that reads them.
"""

import pytest

from kitchen.graph.graph import time_order
from kitchen.graph.propagate import propagate
from kitchen.interactions.events import EventWriter, InteractionStep, replay
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import BIN_AT, KNIFE, PASS_AT, PLATE_AT, WASH_AT, ZONES, Track, render, still
from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.fakes import FakeClock, FakeStateClient

SETTINGS = InteractionSettings()
HAND = "hand-p1-right"
PEANUT_BIN = {"bin-1": {"peanut": 1.0}}

# A forbidden allergen is flagged at the plate when its probability is at least 1 - pass_threshold (AGENTS.md, safety
# rule 2): 0.01 for the anaphylactic ticket, 0.05 for an allergy.
ANAPHYLACTIC_FLAG = 1 - contract_fixture("ticket", "peanut-anaphylactic")["pass_threshold"]
ALLERGY_FLAG = 1 - 0.95


def peanut_after(events: list[dict], entity_id: str, start: dict = PEANUT_BIN) -> float:
    """P(peanut) on entity_id after the edges, as main's graph computes it: every entity starts clean but the bins."""
    ids = {event["a_id"] for event in events} | {event["b_id"] for event in events} | {entity_id}
    entities = {
        node: {"allergen_profile": {"unknown": False, "probabilities": start.get(node, {})}, "origin": "shift_start"}
        for node in ids
    }
    return propagate(entities, sorted(events, key=time_order)).probabilities[entity_id]["peanut"]


@pytest.mark.safety
@pytest.mark.parametrize("fps", [15, 30, 60])
def test_one_rinse_with_a_dropout_is_one_wash_and_the_plate_holds(fps):
    """A knife scoops from the peanut bin, is rinsed for 7.5 s while unseen for 400 ms (a hand over it), then touches
    a plate. One wash leaves 5% on the knife, so the plate holds an anaphylactic ticket; a second wash from the same
    rinse would clean twice (0.25%) and let it pass."""
    knife = Track(
        KNIFE,
        "tool",
        (
            (0, BIN_AT),
            (600, BIN_AT),
            (1000, WASH_AT),
            (8500, WASH_AT),
            (9000, PLATE_AT),
            (9600, PLATE_AT),
            (9900, (0.8, 0.5)),
            (10200, (0.8, 0.5)),
        ),
        unseen=((5000, 5400),),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, 10200), still("plate-t1", "plate", PLATE_AT, 10200), knife], 10200, fps
    )

    events = replay(frames, ZONES, SETTINGS)

    assert [event["action"] for event in events].count("clean.wash") == 1
    assert peanut_after(events, "plate-t1") >= ANAPHYLACTIC_FLAG


@pytest.mark.safety
def test_a_wash_never_erases_a_touch_that_came_during_the_soak():
    """A knife soaks in the sink from 0. At 2.0-2.4 s a hand that just touched the peanut bin grabs it there; the knife
    leaves the sink at 3.1 s and is laid on a plate. It spent under a second in the sink after the dirty touch, so no
    wash may come between that touch and the plate."""
    hand = Track(
        HAND,
        "hand",
        (
            (0, BIN_AT),
            (500, BIN_AT),
            (2000, (0.19, 0.82)),
            (2400, (0.19, 0.82)),
            (2600, (0.6, 0.85)),
            (6000, (0.6, 0.85)),
        ),
    )
    knife = Track(
        KNIFE,
        "tool",
        ((0, WASH_AT), (3100, WASH_AT), (3500, PLATE_AT), (4200, PLATE_AT), (4500, (0.8, 0.5)), (6000, (0.8, 0.5))),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, 6000), still("plate-t1", "plate", PLATE_AT, 6000), hand, knife], 6000
    )

    events = replay(frames, ZONES, SETTINGS)

    touches = [event for event in events if (event["a_id"], event["b_id"]) == (HAND, KNIFE)]
    washes = [event for event in events if event["action"] == "clean.wash" and event["a_id"] == KNIFE]
    assert touches
    assert all(wash["t_start_ms"] > max(touch["t_end_ms"] for touch in touches) for wash in washes)
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
def test_a_held_spoon_carries_a_dirty_hand_onto_the_plate_it_rests_on():
    """A spoon rests on a clean plate. A hand that just touched the peanut bin grabs only the spoon's handle and lifts
    it off 700 ms later: hand -> spoon -> plate. The spoon-plate contact that was already open must not be stamped
    before the hand's touch for all of its length."""
    spoon = Track(
        "tool-spoon-1", "tool", ((0, (0.58, 0.5)), (2100, (0.58, 0.5)), (2400, (0.8, 0.5)), (3000, (0.8, 0.5)))
    )
    hand = Track(
        HAND,
        "hand",
        (
            (0, BIN_AT),
            (600, BIN_AT),
            (1000, (0.62, 0.2)),
            (1400, (0.62, 0.5)),
            (2100, (0.62, 0.5)),
            (2400, (0.84, 0.5)),
            (3000, (0.84, 0.5)),
        ),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, 3000), still("plate-t1", "plate", PLATE_AT, 3000), spoon, hand], 3000
    )

    events = replay(frames, ZONES, SETTINGS)

    assert not any(event["a_id"] == HAND and event["b_id"] == "plate-t1" for event in events)  # never touched it
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
def test_a_hand_on_the_rim_that_slides_onto_the_bin_dirties_the_plate():
    """A hand steadies a plate by its rim, slides onto the edge of the peanut bin beside it while still on the rim,
    then lifts off, all within a second. The hand-plate contact began first, but the bin's peanut reaches the plate
    through the hand while both touch."""
    beside = (0.67, 0.5)  # box [0.60, 0.74]; the plate's is [0.42, 0.58]
    hand = Track(
        HAND,
        "hand",
        (
            (0, (0.55, 0.5)),
            (300, (0.55, 0.5)),
            (400, (0.59, 0.5)),
            (800, (0.59, 0.5)),
            (950, (0.59, 0.15)),
            (2000, (0.59, 0.15)),
        ),
    )
    frames = render([still("plate-t1", "plate", PLATE_AT, 2000), still("bin-1", "bin", beside, 2000), hand], 2000)

    events = replay(frames, ZONES, SETTINGS)

    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
def test_a_touch_two_steps_from_the_plate_reaches_it_while_everything_still_touches():
    """A clean hand holds a spoon that rests on a plate's rim; at 1.2 s the hand also reaches the peanut bin beside
    it. bin -> hand -> spoon -> plate: each cut transfer's rest is itself a new touch, so the cut carries on along the
    chain and the plate gets the peanut while all three still touch."""
    plate = still("plate-t1", "plate", (0.45, 0.5), 3000)  # box [0.37, 0.53]
    spoon = still("tool-spoon-1", "tool", (0.54, 0.5), 3000)  # box [0.525, 0.555], over the plate's rim
    hand = Track(HAND, "hand", ((0, (0.56, 0.5)), (1000, (0.56, 0.5)), (1200, (0.57, 0.5)), (3000, (0.57, 0.5))))
    beside = still("bin-1", "bin", (0.66, 0.5), 3000)  # box [0.59, 0.73]; the hand's [0.54, 0.60] reaches it at 1.2 s
    frames = render([plate, spoon, hand, beside], 3000)

    events = replay(frames, ZONES, SETTINGS)

    assert not any({event["a_id"], event["b_id"]} == {HAND, "plate-t1"} for event in events)  # never touched it
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
@pytest.mark.parametrize("fps", [15, 30, 60])
@pytest.mark.parametrize("carry_ms", [600, 800, 1100])
def test_a_plate_carried_to_the_pass_has_its_edge_in_the_database_when_it_arrives(fps, carry_ms):
    """A hand touches the peanut bin, then carries a clean plate to the pass in well under a second. When the plate's
    detection first shows the pass (what the verdict judges on), the hand-plate edge is already written, so the graph
    reading the database right then finds the peanut on the plate."""
    grab, arrive = 1000, 1000 + carry_ms
    end = arrive + 2000
    hand = Track(
        HAND,
        "hand",
        ((0, BIN_AT), (600, BIN_AT), (grab, PLATE_AT), (arrive, PASS_AT), (arrive + 400, PASS_AT), (end, (0.6, 0.95))),
    )
    plate = Track("plate-t1", "plate", ((0, PLATE_AT), (grab, PLATE_AT), (arrive, PASS_AT), (end, PASS_AT)))
    frames = render([still("bin-1", "bin", BIN_AT, end), plate, hand], end, fps)
    state, clock = FakeStateClient(), FakeClock()
    step = InteractionStep(ZONES, settings=SETTINGS)
    writer = EventWriter(state, clock, SETTINGS.retry_ms)

    at_the_pass = None
    for frame in frames:
        clock.set(frame["t_ms"])
        writer.write(step.process(frame))
        plate_now = next(found for found in frame["detections"] if found["entity_id"] == "plate-t1")
        if plate_now["zone"] == "pass":
            at_the_pass = frame["t_ms"]
            break

    assert at_the_pass is not None
    assert peanut_after(state.list_interactions(0, at_the_pass), "plate-t1") >= ALLERGY_FLAG
