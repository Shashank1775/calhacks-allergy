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

    # One recheck per edge: after the scoop alone the plate is still clean; after the contact it holds.
    first, *rechecks = shift.state.list_verdicts()
    assert (first["trigger"], first["decision"]) == ("plate_at_pass", "pass")
    assert [verdict["trigger"] for verdict in rechecks] == ["late_edge", "late_edge"]
    assert rechecks[-1]["decision"] == "hold"
    assert [step["event_id"] for step in rechecks[-1]["chain"]] == ["e1", "e2"]
    assert shift.speaker.lines == ["Hold one, peanut"]
