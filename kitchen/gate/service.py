"""Gate service on the Pi: a pure gate state machine, plus the loop that feeds it verdicts and health and drives the
driver and speaker.

Owner: outputs (agents)
Step:  gate and screens
Reads: StateClient every tick (zones, the plate at the pass, its ticket and verdicts), vision GET /health
Emits: GateDriver.show, Speaker.say
Rules: fail closed: fault on silence, stale health or any exception; only a PASS for the current revision opens

The loop polls rather than subscribes: each tick reads everything afresh, so a database that stops answering shows up
at once as fault, and nothing depends on a change event arriving. The StateClient must time out its own calls (raise
StateUnavailable); a call that hangs would freeze the gate in its last state.

Demo: `uv run python -m kitchen.gate.service --fake knife-carries-peanut-to-plate` replays a golden scenario's
verdicts through FakeGate and FakeSpeaker.
"""

import argparse
import logging
import sys
from dataclasses import dataclass

from kitchen.gate.voice import spoken_line

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
    crowded: bool = False  # two or more plates at the pass


def gate_state(inputs: GateInputs, settings: GateSettings) -> tuple[str, int | None]:
    """The gate's state and the ticket number to show, following the state-machine table in kitchen/gate/README.md.

    Checked in this order, so a doubt always wins over an open: stale inputs give fault; two plates at the pass give
    hold (the arm can't let one through without the other); no plate at the pass or no verdict yet gives idle
    (closed); only a PASS for this plate at its ticket's current revision gives pass; everything else (a HOLD, an older
    or unknown revision, a verdict for another plate) gives hold.
    """
    if inputs.state_age_ms is None or inputs.state_age_ms > settings.state_limit_ms:
        return "fault", None
    if inputs.health_age_ms is None or inputs.health_age_ms > settings.health_limit_ms:
        return "fault", None
    if inputs.crowded:
        return "hold", None
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


def latest_verdict(verdicts: list[dict], plate_id: str) -> dict | None:
    """The plate's most recent verdict: the highest decided_t_ms, and the one recorded last on a tie."""
    latest = None
    for verdict in verdicts:
        if verdict["plate_id"] == plate_id and (latest is None or verdict["decided_t_ms"] >= latest["decided_t_ms"]):
            latest = verdict
    return latest


def read_pass(state) -> dict:
    """What's at the pass right now, read from the database: plate, its latest verdict, and its ticket.

    Returns the GateInputs fields other than the ages. A plate with no ticket has no verdicts the gate can trust, so
    it reads as no verdict (idle, closed).
    """
    pass_zones = {zone.zone_id for zone in state.list_zones() if zone.kind == "pass"}
    plates = [plate for plate in state.list_entities(kind="plate") if plate["zone"] in pass_zones]
    view = {"plate_id": None, "verdict": None, "current_revision": None, "ticket_number": None, "crowded": False}
    if len(plates) > 1:
        view["crowded"] = True
        return view
    if not plates or plates[0].get("ticket_id") is None:
        view["plate_id"] = plates[0]["entity_id"] if plates else None
        return view

    plate = plates[0]
    view["plate_id"] = plate["entity_id"]
    view["verdict"] = latest_verdict(state.list_verdicts(plate["ticket_id"]), plate["entity_id"])
    ticket = state.get_ticket(plate["ticket_id"])
    if ticket is not None:
        view["current_revision"] = ticket["revision"]
        view["ticket_number"] = ticket["number"]
    return view


class GateService:
    """The gate loop: call tick() a few times a second. Each tick reads the pass, shows the state, and speaks holds.

    `read_health` returns vision's /health `last_frame_t_ms`. Vision's clock isn't ours, so health is judged by when
    that value last changed, measured on our own clock.
    """

    def __init__(self, state, driver, speaker, clock, read_health, settings: GateSettings | None = None):
        self.state = state
        self.driver = driver
        self.speaker = speaker
        self.clock = clock
        self.read_health = read_health
        self.settings = settings or GateSettings()
        self._state_ok_ms: int | None = None
        self._last_frame_t_ms: int | None = None
        self._frame_changed_ms: int | None = None
        self._spoken: set[str] = set()

    def tick(self) -> str:
        """Read the database and vision health, show the gate state, then speak a new hold. Returns the state shown."""
        now = self.clock.now_ms()
        self._check_health(now)
        try:
            view = read_pass(self.state)
            self._state_ok_ms = now
            state_age = 0
        except Exception:  # fail closed: StateUnavailable or anything else shows fault this tick
            logger.exception("gate couldn't read the pass, showing fault")
            view = {"plate_id": None, "verdict": None, "current_revision": None, "ticket_number": None}
            state_age = None

        health_age = None if self._frame_changed_ms is None else now - self._frame_changed_ms
        inputs = GateInputs(state_age_ms=state_age, health_age_ms=health_age, **view)
        shown = drive(self.driver, inputs, self.settings)
        if shown == "hold":
            self._speak(inputs.verdict, inputs.ticket_number)
        return shown

    def _check_health(self, now: int) -> None:
        """Note when vision's last_frame_t_ms last changed. A failed read leaves it unchanged, so health goes stale."""
        try:
            frame_t_ms = self.read_health()
        except Exception:
            logger.exception("vision health unreadable")
            return
        if frame_t_ms != self._last_frame_t_ms:
            self._last_frame_t_ms = frame_t_ms
            self._frame_changed_ms = now

    def _speak(self, verdict: dict | None, ticket_number: int | None) -> None:
        """Say a hold verdict's line once. Voice is never on the path to HOLD: the gate is already shown, and errors here
        are only logged."""
        if verdict is None or ticket_number is None or verdict["verdict_id"] in self._spoken:
            return
        try:
            line = spoken_line(verdict, ticket_number)
            if line is not None:
                self.speaker.say(line)
            self._spoken.add(verdict["verdict_id"])
        except Exception:
            logger.exception("couldn't speak verdict %s", verdict["verdict_id"])


def replay_scenario(name: str, tick_ms: int = 250, quiet: bool = False):
    """Run a golden scenario through the gate loop with FakeGate and FakeSpeaker, on scenario time. Returns the gate.

    Verdicts come from FakeVerdictFeed (the verdict team's fake), and plates are moved into a pass zone when the
    scenario says they reach it.
    """
    from kitchen.gate.driver import FakeGate
    from kitchen.gate.voice import FakeSpeaker
    from kitchen.shared.clock import FakeClock
    from kitchen.shared.fakes import GOLDEN_SCENARIO_PATHS, FakeStateClient, load_scenario, scenario_timeline
    from kitchen.shared.models import EntityState, Zone
    from kitchen.verdict.service import FakeVerdictFeed

    path = next(path for path in GOLDEN_SCENARIO_PATHS if path.stem == name)
    state = FakeStateClient()
    scenario = load_scenario(state, path)
    state.set_zones([Zone(zone_id="pass", kind="pass", polygon=[(0.8, 0.0), (1.0, 0.0), (1.0, 1.0)])])
    feed = FakeVerdictFeed(state, scenario)
    clock = FakeClock()
    gate, speaker = FakeGate(quiet=quiet), FakeSpeaker(quiet=quiet)
    service = GateService(state, gate, speaker, clock, read_health=clock.now_ms)

    timeline = scenario_timeline(scenario)
    end = max(item.t_ms for item in timeline) + tick_ms
    for t in range(0, end + 1, tick_ms):
        clock.set(t)
        for item in timeline:
            if t - tick_ms < item.t_ms <= t:
                if item.kind == "amendment":
                    state.put_ticket(item.record)
                elif item.kind == "plate_at_pass":
                    state.move_entity(EntityState(item.record["plate_id"], 1, t, "pass", None, {}, False))
        feed.play_until(t)
        service.tick()
    return gate, speaker


def main(argv: list[str] | None = None) -> int:
    """CLI: `--fake <scenario>` replays a golden scenario through FakeGate and FakeSpeaker and prints each state."""
    parser = argparse.ArgumentParser(description="Gate service. Only the fake replay runs until the Pi is wired.")
    parser.add_argument(
        "--fake", metavar="SCENARIO", required=True, help="golden scenario name, e.g. wash-resets-knife"
    )
    args = parser.parse_args(argv)
    replay_scenario(args.fake)
    return 0


if __name__ == "__main__":
    sys.exit(main())
