"""Scenes whose edges, run through the partner's provenance graph, must leave the plate carrying the allergen.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.events; kitchen.graph.graph.time_order and kitchen.graph.propagate (main's graph math);
       FakeStateClient, FakeClock; scenes; the anaphylactic ticket fixture
Emits: pytest tests
Rules: 1 fail closed: one rinse is one wash, and a wash never erases what reached an entity after it entered the
       sink; 5 time runs forward: edge order follows cause and effect, and a transfer reaches the database the frame
       it is first seen, so a plate at the pass is judged with it

Each scene is one a review found letting a plate PASS, plus one a demo found holding a plate that should pass (a
touch written as several edges). The graph applies edges in (t_start_ms, t_end_ms) order with noisy-OR, so these
tests check the edges the step writes, through the graph that reads them.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kitchen.graph.graph import time_order
from kitchen.graph.propagate import propagate
from kitchen.interactions.events import EventWriter, InteractionStep, Segmenter, replay
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


@pytest.mark.parametrize("fps", [15, 30, 60])
def test_a_washed_knife_resting_on_a_plate_is_one_touch_so_an_allergy_plate_can_pass(fps):
    """A knife scoops from the peanut bin, is rinsed for 4 s, then rests on a plate for 2 s. That is one touch and
    one edge: the plate gets 80% of the 5% the wash left, about 0.04, under an allergy ticket's 0.05 flag, while an
    anaphylactic ticket's 0.01 still holds. Written as several edges, the knife and the plate would raise each other
    past 0.05 the longer it lay there."""
    knife = Track(
        KNIFE,
        "tool",
        (
            (0, BIN_AT),
            (600, BIN_AT),
            (1000, WASH_AT),
            (5000, WASH_AT),
            (5400, PLATE_AT),
            (7400, PLATE_AT),
            (7700, (0.8, 0.5)),
            (8000, (0.8, 0.5)),
        ),
    )
    frames = render([still("bin-1", "bin", BIN_AT, 8000), still("plate-t1", "plate", PLATE_AT, 8000), knife], 8000, fps)

    events = replay(frames, ZONES, SETTINGS)

    assert [event["action"] for event in events if event["b_id"] == "plate-t1"] == ["transfer.contact"]
    assert ANAPHYLACTIC_FLAG <= peanut_after(events, "plate-t1") < ALLERGY_FLAG


@pytest.mark.safety
def test_a_wash_never_erases_a_touch_that_came_during_the_soak():
    """A knife soaks in the sink from 0. At 2.0-2.4 s a hand that just touched the peanut bin grabs it there; the knife
    leaves the sink at 5.2 s and is laid on a plate. It spent 2.8 s in the sink after the dirty touch, under the 3 s a
    wash needs, so no wash may come between that touch and the plate."""
    hand = Track(
        HAND,
        "hand",
        (
            (0, BIN_AT),
            (500, BIN_AT),
            (2000, (0.19, 0.82)),
            (2400, (0.19, 0.82)),
            (2600, (0.6, 0.85)),
            (8000, (0.6, 0.85)),
        ),
    )
    leave = 2400 + SETTINGS.wash_min_dwell_ms - 200
    knife = Track(
        KNIFE,
        "tool",
        (
            (0, WASH_AT),
            (leave, WASH_AT),
            (leave + 400, PLATE_AT),
            (leave + 1100, PLATE_AT),
            (leave + 1400, (0.8, 0.5)),
            (8000, (0.8, 0.5)),
        ),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, 8000), still("plate-t1", "plate", PLATE_AT, 8000), hand, knife], 8000
    )

    events = replay(frames, ZONES, SETTINGS)

    touches = [event for event in events if (event["a_id"], event["b_id"]) == (HAND, KNIFE)]
    washes = [event for event in events if event["action"] == "clean.wash" and event["a_id"] == KNIFE]
    assert touches
    assert washes == []
    assert peanut_after(events, "plate-t1") >= 0.5  # 0.8 x 0.8 x 0.8; a wash anywhere before the plate leaves 0.03


@pytest.mark.safety
@pytest.mark.parametrize("fps", [15, 30, 60])
@pytest.mark.parametrize("soaking", [KNIFE, HAND])
@pytest.mark.parametrize("early_ms", [0, 30, 70])
def test_a_wash_that_comes_due_while_a_touch_is_being_confirmed_never_erases_it(early_ms, soaking, fps):
    """Something dirty soaks in the sink; something clean starts touching it early_ms before its 3 s dwell is up, and
    is then laid on (or lays it on) a plate. The touch is confirmed only 200 ms into the overlap, after the wash label
    comes, but it began before. The wash must not be stamped before the touch: the plate keeps the peanut."""
    end = 9000
    grab = 933 + 3000 - early_ms  # the soaker's first frame in the sink is 933 at 15, 30 and 60 FPS
    if soaking == KNIFE:  # a peanut knife soaks; a clean hand takes it, then rests on the plate
        knife = Track(KNIFE, "tool", ((0, BIN_AT), (600, BIN_AT), (1000, WASH_AT), (end, WASH_AT)))
        hand = Track(
            HAND,
            "hand",
            (
                (0, (0.6, 0.85)),
                (grab - 200, (0.6, 0.85)),
                (grab, (0.19, 0.82)),
                (grab + 200, (0.19, 0.82)),
                (grab + 500, (0.5, 0.55)),
                (grab + 750, (0.5, 0.55)),
                (grab + 1050, (0.5, 0.8)),
                (end, (0.5, 0.8)),
            ),
        )
    else:  # a hand that touched the peanut bin soaks; it takes a clean knife, which is laid on the plate
        hand = Track(HAND, "hand", ((0, BIN_AT), (600, BIN_AT), (1000, (0.12, 0.82)), (end, (0.12, 0.82))))
        knife = Track(
            KNIFE,
            "tool",
            (
                (0, (0.6, 0.85)),
                (grab - 200, (0.6, 0.85)),
                (grab, (0.14, 0.82)),
                (grab + 200, (0.14, 0.82)),
                (grab + 500, PLATE_AT),
                (grab + 750, PLATE_AT),
                (grab + 1050, (0.8, 0.5)),
                (end, (0.8, 0.5)),
            ),
        )
    frames = render(
        [still("bin-1", "bin", BIN_AT, end), still("plate-t1", "plate", PLATE_AT, end), knife, hand], end, fps
    )

    events = replay(frames, ZONES, SETTINGS)

    grabbed = min(event["t_start_ms"] for event in events if {event["a_id"], event["b_id"]} == {HAND, KNIFE})
    washes = [event for event in events if event["action"] == "clean.wash" and event["a_id"] == soaking]
    assert all(wash["t_start_ms"] > grabbed for wash in washes)
    assert peanut_after(events, "plate-t1") >= 0.5


@pytest.mark.safety
def test_a_wash_never_comes_due_while_a_hand_the_detector_then_loses_is_on_the_knife():
    """At 15 FPS a clean hand is on a soaking peanut knife at 3800 and 3867, 70 to 130 ms before its wash comes due,
    then the detector misses the hand for 333 ms, longer than pair_gap_ms, so the loss doesn't count as overlap and the
    touch is confirmed only at about 4267. Anything overlapping the knife restarts its wash dwell, so no wash comes due
    under the hand, and the hand, then the plate, keep the peanut."""
    end = 9000
    grab = 3933 - 130
    knife = Track(KNIFE, "tool", ((0, BIN_AT), (600, BIN_AT), (1000, WASH_AT), (end, WASH_AT)))
    hand = Track(
        HAND,
        "hand",
        (
            (0, (0.6, 0.85)),
            (grab - 200, (0.6, 0.85)),
            (grab, (0.19, 0.82)),
            (grab + 800, (0.19, 0.82)),
            (grab + 1100, (0.5, 0.55)),
            (grab + 1350, (0.5, 0.55)),
            (grab + 1650, (0.5, 0.8)),
            (end, (0.5, 0.8)),
        ),
        unseen=((3900, 4180),),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, end), still("plate-t1", "plate", PLATE_AT, end), knife, hand], end, 15
    )

    events = replay(frames, ZONES, SETTINGS)

    grabbed = min(event["t_start_ms"] for event in events if {event["a_id"], event["b_id"]} == {HAND, KNIFE})
    washes = [event for event in events if event["action"] == "clean.wash" and event["a_id"] == KNIFE]
    assert all(wash["t_start_ms"] > grabbed for wash in washes)
    assert peanut_after(events, "plate-t1") >= 0.5


@pytest.mark.safety
@pytest.mark.parametrize("fps", [30, 60])
def test_a_wash_that_comes_due_while_a_hand_on_the_knife_is_unseen_never_erases_the_touch(fps):
    """A clean hand takes a soaking peanut knife 70 ms before its wash comes due, then is unseen for 283 ms. Zone
    rules bridge the dropout and confirm the touch only when the hand is back, counting its overlap from before the
    wash came due, so the wash must not be stamped before it: the hand and then the plate keep the peanut."""
    end = 9000
    grab = 933 + 3000 - 70
    knife = Track(KNIFE, "tool", ((0, BIN_AT), (600, BIN_AT), (1000, WASH_AT), (end, WASH_AT)))
    hand = Track(
        HAND,
        "hand",
        (
            (0, (0.6, 0.85)),
            (grab - 200, (0.6, 0.85)),
            (grab, (0.19, 0.82)),
            (grab + 600, (0.19, 0.82)),
            (grab + 900, (0.5, 0.55)),
            (grab + 1150, (0.5, 0.55)),
            (grab + 1450, (0.5, 0.8)),
            (end, (0.5, 0.8)),
        ),
        unseen=((3950, 4233),),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, end), still("plate-t1", "plate", PLATE_AT, end), knife, hand], end, fps
    )

    events = replay(frames, ZONES, SETTINGS)

    grabbed = min(event["t_start_ms"] for event in events if {event["a_id"], event["b_id"]} == {HAND, KNIFE})
    washes = [event for event in events if event["action"] == "clean.wash" and event["a_id"] == KNIFE]
    assert all(wash["t_start_ms"] > grabbed for wash in washes)
    assert peanut_after(events, "plate-t1") >= 0.5


@pytest.mark.safety
@pytest.mark.parametrize("fps", [30, 60])
@pytest.mark.parametrize("unseen", ["tool-spoon-1", HAND])
def test_a_spoon_lifted_as_it_is_grabbed_then_unseen_still_carries_the_hand_onto_the_plate(unseen, fps):
    """A hand that touched the peanut bin grabs a spoon resting on a plate and lifts it at once; then the spoon (or the
    hand) is unseen for 283 ms. Zone rules bridge the dropout and confirm the grab only afterwards, counting it from
    before the lift, when the spoon was still on the plate: the plate gets the peanut."""
    lift = 1400
    gone = ((1450, 1733),)
    spoon = Track(
        "tool-spoon-1",
        "tool",
        ((0, (0.58, 0.5)), (lift, (0.58, 0.5)), (lift + 300, (0.8, 0.5)), (3000, (0.8, 0.5))),
        unseen=gone if unseen == "tool-spoon-1" else (),
    )
    hand = Track(
        HAND,
        "hand",
        (
            (0, BIN_AT),
            (600, BIN_AT),
            (1000, (0.62, 0.2)),
            (1400, (0.62, 0.5)),
            (lift + 300, (0.84, 0.5)),
            (3000, (0.84, 0.5)),
        ),
        unseen=gone if unseen == HAND else (),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, 3000), still("plate-t1", "plate", PLATE_AT, 3000), spoon, hand], 3000, fps
    )

    events = replay(frames, ZONES, SETTINGS)

    assert not any(event["a_id"] == HAND and event["b_id"] == "plate-t1" for event in events)  # never touched it
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


def grabbed_spoon_frames(lift: int, gone: tuple, unseen: str, fps: int) -> list[dict]:
    """A hand that touched the peanut bin comes down on the handle of a spoon resting on a clean plate's rim at 1.4 s
    and lifts it off at lift, the spoon (or the hand) unseen during the spans in gone. The hand never touches the
    plate."""
    end = 3500
    spoon = Track(
        "tool-spoon-1",
        "tool",
        ((0, (0.58, 0.5)), (lift, (0.58, 0.5)), (lift + 300, (0.8, 0.5)), (end, (0.8, 0.5))),
        unseen=gone if unseen == "tool-spoon-1" else (),
    )
    hand = Track(
        HAND,
        "hand",
        (
            (0, BIN_AT),
            (600, BIN_AT),
            (1000, (0.62, 0.2)),
            (1400, (0.62, 0.5)),
            (lift, (0.62, 0.5)),
            (lift + 300, (0.84, 0.5)),
            (end, (0.84, 0.5)),
        ),
        unseen=gone if unseen == HAND else (),
    )
    return render(
        [still("bin-1", "bin", BIN_AT, end), still("plate-t1", "plate", PLATE_AT, end), spoon, hand], end, fps
    )


@pytest.mark.safety
@pytest.mark.parametrize("fps", [15, 30, 60])
@pytest.mark.parametrize("unseen", ["tool-spoon-1", HAND])
@pytest.mark.parametrize("dropout_ms", [400, 900])
def test_a_grab_confirmed_after_a_long_dropout_still_carries_the_hand_onto_the_plate(dropout_ms, unseen, fps):
    """A dirty hand grabs a spoon resting on a plate and lifts it at once; then the spoon (or the hand) is unseen for
    dropout_ms, longer than pair_gap_ms but within vision's track_lost_ms, so the grab is confirmed only after the
    dropout, with the spoon long off the plate. The pair keeps its window through the loss, so the grab still shows
    it began while the spoon was on the plate: the plate gets the peanut."""
    frames = grabbed_spoon_frames(1400, ((1450, 1450 + dropout_ms),), unseen, fps)

    events = replay(frames, ZONES, SETTINGS)

    assert not any(event["a_id"] == HAND and event["b_id"] == "plate-t1" for event in events)  # never touched it
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
@pytest.mark.parametrize("fps", [15, 30, 60])
@pytest.mark.parametrize("unseen", ["tool-spoon-1", HAND])
def test_a_spoon_that_leaves_the_plate_during_a_long_dropout_still_carries_the_hand_onto_it(unseen, fps):
    """A dirty hand grabs a spoon resting on a plate; 50 ms later the hand (or the spoon) is unseen for 400 ms, and
    the spoon is lifted off the plate during that time. The grab is confirmed after the dropout, but it began while
    the spoon was still on the plate: the plate gets the peanut."""
    frames = grabbed_spoon_frames(1650, ((1450, 1850),), unseen, fps)

    events = replay(frames, ZONES, SETTINGS)

    assert not any(event["a_id"] == HAND and event["b_id"] == "plate-t1" for event in events)  # never touched it
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
@pytest.mark.parametrize("fps", [15, 30, 60])
@pytest.mark.parametrize("hold_ms", [700, 150, 100, 0])
def test_a_held_spoon_carries_a_dirty_hand_onto_the_plate_it_rests_on(hold_ms, fps):
    """A spoon rests on a clean plate. A hand that just touched the peanut bin grabs only the spoon's handle and lifts
    it off hold_ms later: hand -> spoon -> plate. The hand's touch is confirmed only 200 ms into the grab, so a spoon
    lifted sooner has already left the plate by then; the touch began while it was still there, so the plate still
    gets the peanut."""
    lift = 1400 + hold_ms
    spoon = Track(
        "tool-spoon-1", "tool", ((0, (0.58, 0.5)), (lift, (0.58, 0.5)), (lift + 300, (0.8, 0.5)), (3000, (0.8, 0.5)))
    )
    hand = Track(
        HAND,
        "hand",
        (
            (0, BIN_AT),
            (600, BIN_AT),
            (1000, (0.62, 0.2)),
            (1400, (0.62, 0.5)),
            (lift, (0.62, 0.5)),
            (lift + 300, (0.84, 0.5)),
            (3000, (0.84, 0.5)),
        ),
    )
    frames = render(
        [still("bin-1", "bin", BIN_AT, 3000), still("plate-t1", "plate", PLATE_AT, 3000), spoon, hand], 3000, fps
    )

    events = replay(frames, ZONES, SETTINGS)

    assert not any(event["a_id"] == HAND and event["b_id"] == "plate-t1" for event in events)  # never touched it
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
@pytest.mark.parametrize(
    ("fps", "stay_ms"), [(15, 400), (30, 400), (60, 400), (15, 150), (30, 150), (60, 150), (15, 100), (30, 100)]
)
def test_a_hand_on_the_rim_that_slides_onto_the_bin_dirties_the_plate(fps, stay_ms):
    """A hand steadies a plate by its rim, slides onto the edge of the peanut bin beside it while still on the rim,
    stays stay_ms, then lifts off. The hand-plate contact began first, but the bin's peanut reaches the plate through
    the hand while both touch, even when the hand leaves the rim in the frame the hand-bin contact is confirmed."""
    beside = (0.67, 0.5)  # box [0.60, 0.74]; the plate's is [0.42, 0.58]
    hand = Track(
        HAND,
        "hand",
        (
            (0, (0.55, 0.5)),
            (300, (0.55, 0.5)),
            (400, (0.59, 0.5)),
            (400 + stay_ms, (0.59, 0.5)),
            (550 + stay_ms, (0.59, 0.15)),
            (2000, (0.59, 0.15)),
        ),
    )
    frames = render([still("plate-t1", "plate", PLATE_AT, 2000), still("bin-1", "bin", beside, 2000), hand], 2000, fps)

    events = replay(frames, ZONES, SETTINGS)

    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
@pytest.mark.parametrize(("fps", "late_ms"), [(12, 0), (24, 0), (30, 0), (30, 4)])
def test_a_hand_that_jumps_from_the_rim_onto_the_bin_between_frames_dirties_the_plate(fps, late_ms):
    """A hand rests on a plate's rim, then is on the peanut bin beside it in the next frame: to cross the gap it must
    have touched both at once. The hand-bin contact is confirmed contact_min_ms into the overlap, which at 12 or 24
    FPS, or with one frame a few ms late, is more than contact_min_ms after the hand was last seen on the rim. News
    still crosses the rim touch, so the plate gets the peanut."""
    beside = (0.67, 0.5)  # box [0.60, 0.74]; the plate's is [0.42, 0.58]
    hand = Track(
        HAND,
        "hand",
        ((0, (0.565, 0.5)), (700, (0.565, 0.5)), (701, (0.625, 0.5)), (1500, (0.625, 0.5)), (1600, (0.625, 0.2))),
    )
    frames = render([still("plate-t1", "plate", PLATE_AT, 2000), still("bin-1", "bin", beside, 2000), hand], 2000, fps)
    jumped = next(index for index, frame in enumerate(frames) if frame["t_ms"] > 700)
    frames = [
        {**frame, "t_ms": frame["t_ms"] + late_ms} if index == jumped else frame for index, frame in enumerate(frames)
    ]

    events = replay(frames, ZONES, SETTINGS)

    assert not any(event["b_id"] == "plate-t1" and event["a_id"] != HAND for event in events)
    assert peanut_after(events, "plate-t1", start={"bin-1": {"peanut": 1.0}}) >= ANAPHYLACTIC_FLAG


@pytest.mark.safety
@pytest.mark.parametrize("lift_ms", [None, 2100, 1800, 1500])
def test_a_touch_two_steps_from_the_plate_reaches_it_while_everything_still_touches(lift_ms):
    """A clean hand holds a spoon that rests on a plate's rim; at 1.2 s the hand also reaches the peanut bin beside
    it, and at lift_ms (or never) lifts the spoon off. bin -> hand -> spoon -> plate: the bin's news crosses the
    hand's grip, which is news to the spoon, which crosses the spoon on the plate, while all three still touch."""
    plate = still("plate-t1", "plate", (0.45, 0.5), 3000)  # box [0.37, 0.53]
    beside = still("bin-1", "bin", (0.66, 0.5), 3000)  # box [0.59, 0.73]; the hand's [0.54, 0.60] reaches it at 1.2 s
    if lift_ms is None:
        spoon = still("tool-spoon-1", "tool", (0.54, 0.5), 3000)  # box [0.525, 0.555], over the plate's rim
        hand = Track(HAND, "hand", ((0, (0.56, 0.5)), (1000, (0.56, 0.5)), (1200, (0.57, 0.5)), (3000, (0.57, 0.5))))
    else:
        up = lift_ms + 200
        spoon = Track(
            "tool-spoon-1", "tool", ((0, (0.54, 0.5)), (lift_ms, (0.54, 0.5)), (up, (0.54, 0.25)), (3000, (0.54, 0.25)))
        )
        hand = Track(
            HAND,
            "hand",
            (
                (0, (0.56, 0.5)),
                (1000, (0.56, 0.5)),
                (1200, (0.57, 0.5)),
                (lift_ms, (0.57, 0.5)),
                (up, (0.57, 0.25)),
                (3000, (0.57, 0.25)),
            ),
        )
    frames = render([plate, spoon, hand, beside], 3000)

    events = replay(frames, ZONES, SETTINGS)

    assert not any({event["a_id"], event["b_id"]} == {HAND, "plate-t1"} for event in events)  # never touched it
    assert peanut_after(events, "plate-t1") >= ALLERGY_FLAG


@pytest.mark.safety
@pytest.mark.parametrize("fps", [15, 30, 60])
def test_a_hand_that_lets_go_of_the_handle_for_a_moment_still_carries_the_bin_onto_the_plate(fps):
    """A clean hand takes the handle of a spoon resting on a plate's rim, slides onto the peanut bin's edge and loses
    the handle for 200 ms, takes it again, and lifts the spoon off at 1.7 s. Its grip is still the same touch when it
    comes back, so the bin's news that reached the hand meanwhile crosses it, and then the spoon on the plate."""
    plate = still("plate-t1", "plate", (0.45, 0.5), 2900)
    spoon = Track(
        "tool-spoon-1", "tool", ((0, (0.54, 0.5)), (1700, (0.54, 0.5)), (1900, (0.54, 0.25)), (2900, (0.54, 0.25)))
    )
    hand = Track(
        HAND,
        "hand",
        (
            (0, (0.56, 0.3)),
            (600, (0.56, 0.3)),
            (1000, (0.56, 0.5)),
            (1070, (0.56, 0.5)),
            (1100, (0.585, 0.5)),  # onto the bin's edge, off the spoon's handle
            (1300, (0.585, 0.5)),
            (1330, (0.57, 0.5)),  # back on the handle, still on the bin
            (1700, (0.57, 0.5)),
            (1900, (0.57, 0.25)),
            (2900, (0.57, 0.25)),
        ),
    )
    frames = render([plate, spoon, hand, still("bin-1", "bin", (0.66, 0.5), 2900)], 2900, fps)

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


# Entities for the renaming test, each with a second name that sorts the other way round among its kind, so every
# tie between edges stamped alike (one frame, one wave) is broken the other way.
NAMES = {
    "hand-p1-left": "hand-p9-right",
    "hand-p2-right": "hand-p8-left",
    "tool-spoon-1": "tool-whisk-9",
    "tool-knife-1": "tool-fork-9",
    "bin-1": "bin-9",
    "bin-2": "bin-8",
    "plate-t1": "plate-t9",
    "plate-t2": "plate-t8",
}
TOUCHABLE = [
    (a_id, b_id)
    for a_id in NAMES
    for b_id in NAMES
    if (a_id.split("-")[0], b_id.split("-")[0])
    in {("hand", "tool"), ("hand", "bin"), ("hand", "plate"), ("tool", "bin"), ("tool", "plate")}
]
TOUCHES = st.lists(
    st.tuples(st.integers(1, 300), st.sets(st.tuples(st.sampled_from(TOUCHABLE), st.sampled_from([0.8, 0.9])))),
    max_size=40,
)


def carried_by_name(frames: list, names: dict) -> dict:
    """What each entity may carry after the frames' contact hits, under the given names, by original name."""
    segmenter = Segmenter(SETTINGS)
    events, t_ms = [], 0
    for gap, hits in frames:
        t_ms += gap
        judged = [
            (
                {
                    "t_ms": t_ms,
                    "frame_id": t_ms,
                    "a_id": names[a_id],
                    "b_id": names[b_id],
                    "b_kind": b_id.split("-")[0],
                },
                [("transfer.contact", p)],
                {"name": "zone_rules", "version": "test"},
            )
            for (a_id, b_id), p in sorted(hits)
        ]
        events += segmenter.add_frame(t_ms, judged)
    events += segmenter.close_all()
    start = {names["bin-1"]: {"unknown": False, "probabilities": {"peanut": 1.0}}}
    entities = {
        names[node]: {
            "allergen_profile": start.get(names[node], {"unknown": False, "probabilities": {}}),
            "origin": "shift_start",
        }
        for node in NAMES
    }
    carried = propagate(entities, sorted(events, key=time_order)).probabilities
    return {node: carried[names[node]]["peanut"] for node in NAMES}


@pytest.mark.safety
@settings(max_examples=150, deadline=None)
@given(TOUCHES)
def test_what_reaches_each_entity_does_not_depend_on_what_the_entities_are_called(frames):
    """Edges stamped alike apply in event-ID order, and IDs hold the entities' names. Renaming every entity so the
    order flips must not change which entities the peanut reaches, so a name can never let a plate PASS."""
    as_named = carried_by_name(frames, {node: node for node in NAMES})
    renamed = carried_by_name(frames, NAMES)

    assert {node for node, p in as_named.items() if p > 0} == {node for node, p in renamed.items() if p > 0}


@pytest.mark.safety
@pytest.mark.parametrize(("holder", "steadier"), [("hand-p2-right", "hand-p1-left"), ("hand-p1-left", "hand-p2-right")])
def test_news_by_parallel_paths_all_gets_across_whichever_hand_is_named_first(holder, steadier):
    """A knife rests in a clean bin; one hand holds the knife with its palm on the bin's rim, the other steadies the
    bin. A third hand that touched a may-contain bin (peanut 0.03) grabs the knife at 1023: its news reaches the clean
    bin directly from the knife and again through the holding hand. Both routes cross the steadying hand's touch, so
    when it later touches the plate, the plate holds an anaphylactic ticket whichever hand's ID sorts first."""
    dirty = "hand-p3-right"
    contact = [("transfer.contact", 0.8)]
    segmenter = Segmenter(SETTINGS)
    events = []
    for t_ms in range(0, 3003, 33):
        pairs = []
        if t_ms <= 300:
            pairs.append((dirty, "bin-2"))
        if t_ms <= 2000:
            pairs += [(KNIFE, "bin-3"), (holder, KNIFE), (holder, "bin-3"), (steadier, "bin-3")]
        if 1023 <= t_ms <= 1300:
            pairs.append((dirty, KNIFE))
        if 2500 <= t_ms <= 2800:
            pairs.append((steadier, "plate-t1"))
        judged = [
            (
                {"t_ms": t_ms, "frame_id": t_ms // 33, "a_id": a_id, "b_id": b_id, "b_kind": b_id.split("-")[0]},
                contact,
                {"name": "zone_rules", "version": "test"},
            )
            for a_id, b_id in pairs
        ]
        events += segmenter.add_frame(t_ms, judged)

    assert peanut_after(events, "plate-t1", start={"bin-2": {"peanut": 0.03}}) >= ANAPHYLACTIC_FLAG
