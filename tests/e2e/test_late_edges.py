"""An edge written after a plate was judged: the plate is judged again and the gate follows (#183).

Owner: platform (agents)
Step:  end-to-end harness
Reads: the `kitchen` fixture (tests/e2e/conftest.py); the partner's ProvenanceGraph (kitchen.graph), which builds
       from the edges actually recorded; golden scenario knife-carries-peanut-to-plate
Emits: nothing
Rules: late changes recheck everything; fail closed: a plate that passed before its contamination was written must
       not keep its PASS
"""

import pytest

from kitchen.graph import ProvenanceGraph
from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS, read_scenario

KNIFE_SCENARIO = read_scenario(
    next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == "knife-carries-peanut-to-plate")
)


@pytest.mark.safety
def test_a_contact_written_after_the_plate_passed_turns_the_gate_to_hold(kitchen):
    """The knife scooped satay and touched the plate before it reached the pass, but interactions wrote both edges
    after the plate was judged. The plate passed on an empty history; once the edges land it holds, the gate closes
    and the hold is spoken."""
    shift = kitchen(dict(KNIFE_SCENARIO, events=[]), graph=ProvenanceGraph)
    shift.at(3000)
    shift.plate_reaches_pass("plate-t1")
    assert shift.tick() == "pass"

    shift.at(3500)
    for edge in KNIFE_SCENARIO["events"]:
        shift.state.record_interaction(edge)
    assert shift.tick() == "hold"

    # One recheck per edge: after the scoop alone the plate is still clean, which its PASS already says, so only the
    # recheck after the contact is written.
    first, recheck = shift.state.list_verdicts()
    assert (first["trigger"], first["decision"]) == ("plate_at_pass", "pass")
    assert (recheck["trigger"], recheck["decision"]) == ("late_edge", "hold")
    assert [step["event_id"] for step in recheck["chain"]] == ["e1", "e2"]
    assert shift.speaker.lines == ["Hold one, peanut"]

    # Another touch on the held plate changes nothing, so it's neither written nor spoken again.
    shift.at(4000)
    touch = {**KNIFE_SCENARIO["events"][1], "event_id": "e3", "t_start_ms": 3800, "t_end_ms": 3900}
    shift.state.record_interaction(touch)
    assert shift.tick() == "hold"
    assert len(shift.state.list_verdicts()) == 2
    assert shift.speaker.lines == ["Hold one, peanut"]
