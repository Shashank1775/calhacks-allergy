"""Gate service on the Pi: a pure gate state machine, plus the loop that feeds it verdicts and health and drives the
driver and speaker.

Owner: outputs (agents)
Step:  gate and screens
Reads: StateClient.subscribe('verdict'), ticket revisions, the plate at the pass, vision GET /health
Emits: GateDriver.show, Speaker.say
Rules: fail closed: fault on silence, stale health or any exception; only a PASS for the current revision opens

Built so far: the state machine (gate_state) and drive(), which shows it on a GateDriver and turns any exception into
fault. The loop on StateClient comes next (it needs FakeStateClient).
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateSettings:
    """How long the gate trusts its inputs. Safety constants: logged at startup by the service."""

    # The database (and with it the verdict feed) must have answered within this long.
    state_limit_ms: int = 2000
    # Vision's /health last_frame_t_ms must have advanced within this long.
    health_limit_ms: int = 1000


@dataclass(frozen=True)
class GateInputs:
    """Everything the gate looks at, read fresh each tick by the service loop.

    `verdict` is the latest verdict for the plate at the pass, `current_revision` and `ticket_number` come from that
    plate's ticket. Ages are milliseconds since the database last answered and since vision last produced a frame;
    None means never.
    """

    plate_id: str | None
    verdict: dict | None
    current_revision: int | None
    ticket_number: int | None
    state_age_ms: int | None
    health_age_ms: int | None


def gate_state(inputs: GateInputs, settings: GateSettings) -> tuple[str, int | None]:
    """The gate's state and the ticket number to show, following the state-machine table in kitchen/gate/README.md.

    Checked in this order, so a doubt always wins over an open: stale inputs give fault; no plate at the pass or no
    verdict yet gives idle (closed); only a PASS for this plate at its ticket's current revision gives pass;
    everything else (a HOLD, an older or unknown revision, a verdict for another plate) gives hold.
    """
    if inputs.state_age_ms is None or inputs.state_age_ms > settings.state_limit_ms:
        return "fault", None
    if inputs.health_age_ms is None or inputs.health_age_ms > settings.health_limit_ms:
        return "fault", None
    if inputs.plate_id is None or inputs.verdict is None:
        return "idle", None

    verdict = inputs.verdict
    is_current = inputs.current_revision is not None and verdict["ticket_revision"] == inputs.current_revision
    if verdict["decision"] == "pass" and verdict["plate_id"] == inputs.plate_id and is_current:
        return "pass", inputs.ticket_number
    return "hold", inputs.ticket_number


def drive(driver, inputs: GateInputs, settings: GateSettings) -> str:
    """Compute the gate state and show it on the driver. Returns the state shown.

    Any exception, in the state machine or in the driver, shows fault instead (fail closed). If showing fault fails
    too, that error propagates and the service stops, which also stops the gate from opening.
    """
    try:
        state, ticket_number = gate_state(inputs, settings)
        driver.show(state, ticket_number)
        return state
    except Exception:
        logger.exception("gate error, showing fault")
        driver.show("fault", None)
        return "fault"
