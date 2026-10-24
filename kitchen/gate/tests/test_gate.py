"""Tests for the gate state machine, drive() and FakeGate.

Owner: outputs (agents)
Step:  gate and screens
Reads: kitchen.gate.service, kitchen.gate.driver, contracts/fixtures/valid/verdict/
Emits: nothing
Rules: fail closed and late changes recheck everything (safety tests): closed by default, fault on stale inputs or
       any exception, and only a PASS for the plate at the pass at its ticket's current revision opens
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kitchen.gate.driver import FakeGate
from kitchen.gate.service import GateInputs, GateSettings, drive, gate_state

VERDICTS = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "valid" / "verdict"
PASS = json.loads((VERDICTS / "pass-clean.json").read_text())
HOLD = json.loads((VERDICTS / "hold-peanut.json").read_text())
SETTINGS = GateSettings()

# The plate at the pass with a fresh PASS for its ticket's current revision: the only inputs that open the gate.
OPEN = GateInputs(
    plate_id=PASS["plate_id"],
    verdict=PASS,
    current_revision=PASS["ticket_revision"],
    ticket_number=7,
    state_age_ms=100,
    health_age_ms=100,
)


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        (OPEN, ("pass", 7)),
        (replace(OPEN, verdict=HOLD, plate_id=HOLD["plate_id"]), ("hold", 7)),
        (replace(OPEN, plate_id=None, verdict=None), ("idle", None)),
        (replace(OPEN, verdict=None), ("idle", None)),
        (replace(OPEN, current_revision=PASS["ticket_revision"] + 1), ("hold", 7)),
        (replace(OPEN, current_revision=None), ("hold", 7)),
        (replace(OPEN, plate_id="plate-other"), ("hold", 7)),
        (replace(OPEN, state_age_ms=SETTINGS.state_limit_ms + 1), ("fault", None)),
        (replace(OPEN, health_age_ms=SETTINGS.health_limit_ms + 1), ("fault", None)),
        (replace(OPEN, state_age_ms=None), ("fault", None)),
        (replace(OPEN, health_age_ms=None), ("fault", None)),
    ],
    ids=[
        "current-pass-opens",
        "hold-holds",
        "no-plate-idle",
        "no-verdict-idle",
        "older-revision-holds",
        "unknown-revision-holds",
        "other-plate-holds",
        "silent-database-faults",
        "stale-vision-faults",
        "database-never-answered-faults",
        "vision-never-answered-faults",
    ],
)
def test_gate_transitions(inputs, expected):
    assert gate_state(inputs, SETTINGS) == expected


@pytest.mark.safety
@given(
    decision=st.sampled_from(["pass", "hold"]),
    verdict_revision=st.integers(1, 4),
    current_revision=st.none() | st.integers(1, 4),
    plate_id=st.none() | st.sampled_from([PASS["plate_id"], "plate-other"]),
    has_verdict=st.booleans(),
    state_age_ms=st.none() | st.integers(0, 5000),
    health_age_ms=st.none() | st.integers(0, 5000),
)
def test_gate_opens_only_on_current_pass(
    decision, verdict_revision, current_revision, plate_id, has_verdict, state_age_ms, health_age_ms
):
    """Fail closed and late changes recheck everything: no other combination of inputs opens the arm."""
    verdict = dict(PASS, decision=decision, ticket_revision=verdict_revision) if has_verdict else None
    inputs = GateInputs(plate_id, verdict, current_revision, 7, state_age_ms, health_age_ms)

    state, _ = gate_state(inputs, SETTINGS)

    opens_by_rule = (
        has_verdict
        and decision == "pass"
        and plate_id == PASS["plate_id"]
        and verdict_revision == current_revision
        and state_age_ms is not None
        and state_age_ms <= SETTINGS.state_limit_ms
        and health_age_ms is not None
        and health_age_ms <= SETTINGS.health_limit_ms
    )
    assert (state == "pass") == opens_by_rule


@pytest.mark.safety
def test_gate_stale_fault():
    """Fail closed: a silent database or a stale camera faults the gate even over a current PASS."""
    gate = FakeGate(quiet=True)

    drive(gate, replace(OPEN, state_age_ms=SETTINGS.state_limit_ms + 1), SETTINGS)
    drive(gate, replace(OPEN, health_age_ms=SETTINGS.health_limit_ms + 1), SETTINGS)

    assert gate.calls == [("fault", None), ("fault", None)]


@pytest.mark.safety
def test_gate_exception_fault_from_driver():
    """Fail closed: when the driver throws, drive() shows fault instead."""
    gate = FakeGate(quiet=True)
    gate.error = OSError("servo not responding")

    assert drive(gate, OPEN, SETTINGS) == "fault"
    assert gate.calls == [("fault", None)]


@pytest.mark.safety
def test_gate_exception_fault_from_malformed_verdict():
    """Fail closed: a verdict missing its fields is an exception in the state machine, so the gate faults."""
    gate = FakeGate(quiet=True)

    assert drive(gate, replace(OPEN, verdict={"decision": "pass"}), SETTINGS) == "fault"
    assert gate.state == "fault"


def test_gate_fakes_record_every_call(capsys):
    gate = FakeGate()

    drive(gate, OPEN, SETTINGS)
    drive(gate, replace(OPEN, verdict=None), SETTINGS)

    assert gate.calls == [("pass", 7), ("idle", None)]
    assert capsys.readouterr().out == "[gate] pass #7\n[gate] idle\n"


def test_gate_fakes_reject_unknown_states():
    with pytest.raises(ValueError, match="unknown gate state"):
        FakeGate(quiet=True).show("open")
