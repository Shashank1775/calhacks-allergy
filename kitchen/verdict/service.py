"""Verdict service: on plate-at-pass or a ticket amendment, query the graph, decide, and record the verdict.
Also FakeVerdictFeed, which replays scenario verdicts on a Clock for outputs to build against.

Owner: verdict (agents)
Step:  verdict
Reads: StateClient (ticket, entity zone, plate_binding); ProvenanceGraph (incl. subgraph); LayaPlateCheck; Clock
Emits: verdict via StateClient.record_verdict
Rules: fail closed: StateUnavailable, GraphUnavailable, LayaCheckUnavailable or a stale plate holds;
       late changes recheck everything: amendments recompute from full history

Triggers are table changes (contracts/apis.md): a new entity_state row that moves a plate into a zone of kind "pass"
judges it with trigger plate_at_pass, and a ticket update to a higher revision rejudges every plate that already has
a verdict for that ticket, with trigger ticket_amended. Every judgment reads the full history again; nothing is carried
over from an earlier verdict. Laya's plate check reads the graph's subgraph for the plate and can only add holds.
"""

import logging
from dataclasses import dataclass

from kitchen.shared.fakes.scenarios import read_scenario
from kitchen.shared.interfaces import StateUnavailable
from kitchen.verdict.policy import decide

logger = logging.getLogger(__name__)

# A plate at the pass with no binding has no ticket to judge against. Its HOLD is recorded under this ticket ID, which
# no ticket has, so the gate finds no current revision and holds too.
UNBOUND_TICKET_ID = "t-unbound"


@dataclass(frozen=True)
class VerdictSettings:
    """The verdict service's safety constants. The pass threshold isn't here: orders sets it on each ticket."""

    # A plate whose newest entity_state is older than this when it's judged has lost its track: hold (stale_track).
    stale_track_ms: int = 2000


def error_reason(error: Exception) -> dict:
    """A verdict reason for an exception, naming its type so the expo board shows what failed."""
    return {"code": "error", "detail": f"{type(error).__name__}: {error}"}


class VerdictService:
    """Judges plates as they reach the pass, and again when their ticket is amended.

    Call start() to subscribe. Verdicts the database refused (StateUnavailable) wait in `pending`, and amended tickets
    whose plates couldn't be listed wait in `amended`; retry() handles both, and every trigger runs it, so a HOLD made
    while the database was down still lands. A service loop should also call retry() now and then.
    """

    def __init__(self, state, graph, plate_check, clock, settings: VerdictSettings | None = None):
        self.state = state
        self.graph = graph
        self.plate_check = plate_check
        self.clock = clock
        self.settings = settings or VerdictSettings()
        self.pending: list[dict] = []
        self.amended: set[str] = set()
        self._at_pass: set[str] = set()
        self._unsubscribe = []

    def start(self) -> None:
        """Subscribe to entity_state and ticket changes. The callbacks never raise into the writer."""
        self._unsubscribe = [
            self.state.subscribe("entity_state", self.on_entity_state),
            self.state.subscribe("ticket", self.on_ticket),
        ]

    def stop(self) -> None:
        """Unsubscribe."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []

    def on_entity_state(self, change) -> None:
        """Judge a plate when its new state puts it in a pass zone and it wasn't there before."""
        try:
            row = change.row
            if not row.entity_id.startswith("plate-"):
                return
            pass_zones = {zone.zone_id for zone in self.state.list_zones() if zone.kind == "pass"}
            if row.zone in pass_zones and row.entity_id not in self._at_pass:
                self._at_pass.add(row.entity_id)
                self.judge(row.entity_id, "plate_at_pass")
            elif row.zone not in pass_zones:
                self._at_pass.discard(row.entity_id)
        except Exception:
            logger.exception("verdict trigger failed on entity_state %s", change.row)
        self.retry()

    def on_ticket(self, change) -> None:
        """When a ticket is amended, rejudge every plate already judged for it, from scratch."""
        try:
            if change.kind == "update":
                self.amended.add(change.row["ticket_id"])
        except Exception:
            logger.exception("verdict trigger failed on ticket %s", change.row)
        self.retry()

    def retry(self) -> None:
        """Recheck amended tickets not yet rechecked, then write pending verdicts. Never raises."""
        for ticket_id in sorted(self.amended):
            try:
                judged = {verdict["plate_id"] for verdict in self.state.list_verdicts(ticket_id)}
            except Exception:  # late changes recheck everything: keep it queued rather than drop the amendment
                logger.exception("couldn't list the plates of amended ticket %s, will retry", ticket_id)
                continue
            self.amended.discard(ticket_id)
            for plate_id in sorted(judged):
                self.judge(plate_id, "ticket_amended")
        self.flush()

    def judge(self, plate_id: str, trigger: str) -> dict:
        """Read everything decide() needs, decide, and record the verdict. Returns the verdict."""
        now = self.clock.now_ms()
        try:
            inputs = self.read_inputs(plate_id, now)
        except Exception as error:  # fail closed: read_inputs guards its own reads; this catches anything it missed
            logger.exception("reading verdict inputs for %s failed, holding", plate_id)
            inputs = self.blank_inputs()
            inputs["problems"].append(error_reason(error))
        verdict = decide(
            verdict_id=f"v-{plate_id}-r{inputs['revision']}-{now}",
            plate_id=plate_id,
            trigger=trigger,
            decided_t_ms=now,
            ticket_id=inputs["ticket_id"],
            ticket=inputs["ticket"],
            reach=inputs["reach"],
            unknown_ancestors=inputs["unknown_ancestors"],
            plate_check=inputs["plate_check"],
            problems=inputs["problems"],
            graph=inputs["graph"],
            laya=inputs["laya"],
        )
        if verdict["decision"] == "hold":
            logger.warning("HOLD %s for %s: %s", plate_id, verdict["ticket_id"], verdict["reasons"])
        self.pending.append(verdict)
        self.flush()
        return verdict

    def blank_inputs(self) -> dict:
        """decide()'s inputs before anything is read: every one missing, so on its own it can only hold."""
        return {
            "ticket_id": UNBOUND_TICKET_ID,
            "ticket": None,
            "revision": 0,
            "reach": None,
            "unknown_ancestors": None,
            "problems": [],
            "graph": None,
            "plate_check": None,
            "laya": None,
        }

    def read_inputs(self, plate_id: str, now: int) -> dict:
        """The binding, ticket, plate health, the graph's answers and Laya's plate check, as decide()'s inputs.

        Fail closed: anything that can't be read stays None or adds a problem, and any exception adds an error.
        """
        inputs = self.blank_inputs()
        try:
            binding = self.state.get_plate_binding(plate_id)
            if binding is None:
                # Nothing to ask the graph or Laya: the missing binding alone holds the plate.
                inputs["problems"].append({"code": "missing_input", "detail": f"{plate_id} is not bound to a ticket"})
                inputs["reach"], inputs["unknown_ancestors"], inputs["plate_check"] = [], [], {}
                return inputs
            inputs["ticket_id"] = binding.ticket_id
            inputs["ticket"] = self.state.get_ticket(binding.ticket_id)
            if inputs["ticket"] is None:
                inputs["plate_check"] = {}  # nothing forbidden to check: the missing ticket already holds
            else:
                inputs["revision"] = inputs["ticket"]["revision"]

            plate = self.state.get_entity(plate_id)
            if plate is None:
                inputs["problems"].append({"code": "missing_input", "detail": f"{plate_id} is not a known entity"})
            elif now - plate["last_seen_t_ms"] > self.settings.stale_track_ms:
                inputs["problems"].append(
                    {"code": "stale_track", "detail": f"{plate_id} last seen {plate['last_seen_t_ms']}"}
                )

            # The graph is asked afresh every time, about the plate's whole history (late changes recheck everything).
            inputs["graph"] = self.graph.version()
            inputs["reach"] = self.graph.reach(plate_id, now)
            inputs["unknown_ancestors"] = self.graph.unknown_ancestors(plate_id, now)
            if inputs["ticket"] is not None:
                inputs["plate_check"] = self.read_plate_check(plate_id, now, inputs["ticket"]["forbidden"])
                if inputs["plate_check"] is not None:
                    inputs["laya"] = getattr(self.plate_check, "version", None)  # only a checkpoint that answered
        except Exception as error:  # fail closed: StateUnavailable, GraphUnavailable or anything else holds
            logger.exception("reading verdict inputs for %s failed, holding", plate_id)
            inputs["problems"].append(error_reason(error))
        return inputs

    def read_plate_check(self, plate_id: str, now: int, forbidden: list[str]) -> dict | None:
        """Laya's P(reached) per forbidden allergen from the plate's subgraph, or None if it couldn't answer.

        Asked separately from the graph's other answers, so a Laya failure costs only Laya's input: decide() then
        holds with laya_unavailable. Without a ticket there's nothing to check, and the missing ticket already holds.
        """
        try:
            return self.plate_check.check(self.graph.subgraph(plate_id, now), forbidden)
        except Exception:  # fail closed: LayaCheckUnavailable, GraphUnavailable or anything else
            logger.exception("Laya's plate check failed for %s, holding", plate_id)
            return None

    def flush(self) -> None:
        """Write pending verdicts in order. While the database is down they stay pending for the next flush().

        Any other failure can't be cured by retrying, so that verdict is logged and dropped: left at the head of the
        queue it would block every later verdict, HOLDs included. Without its row the gate stays closed for that plate.
        """
        while self.pending:
            verdict_id = self.pending[0]["verdict_id"]
            try:
                self.state.record_verdict(self.pending[0])
            except StateUnavailable:
                logger.exception("database unavailable, verdict %s will be retried", verdict_id)
                return
            except Exception:
                logger.exception("verdict %s can't be recorded, dropping it so later verdicts still land", verdict_id)
            self.pending.pop(0)


class FakeVerdictFeed:
    """Writes a golden scenario's expected verdicts into a StateClient, so outputs can build against verdict rows.

    No decision logic: each expected entry becomes a verdict with the expected decision, its reason codes and its
    forbidden allergens flagged. Revision 1 is decided when the plate reaches the pass, a later revision when its
    amendment lands. Call play_until(t_ms) as the scenario's clock moves.
    """

    def __init__(self, state, scenario):
        self.state = state
        self.scenario = read_scenario(scenario)
        self.played: set[str] = set()

    def verdicts(self) -> list[dict]:
        """Every verdict the scenario expects, in decided_t_ms order."""
        tickets = {
            (t["ticket_id"], t["revision"]): t for t in self.scenario["tickets"] + self.scenario.get("amendments", [])
        }
        at_pass = {plate["plate_id"]: plate for plate in self.scenario["plates_at_pass"]}
        verdicts = []
        for entry in self.scenario["expected"]:
            plate = at_pass[entry["plate_id"]]
            ticket = tickets[(plate["ticket_id"], entry["ticket_revision"])]
            decided = max(plate["t_ms"], ticket["created_t_ms"])
            verdicts.append(self._verdict(entry, ticket, decided))
        return sorted(verdicts, key=lambda verdict: verdict["decided_t_ms"])

    def play_until(self, t_ms: int) -> None:
        """Record every expected verdict decided at or before t_ms that isn't recorded yet."""
        for verdict in self.verdicts():
            if verdict["decided_t_ms"] <= t_ms and verdict["verdict_id"] not in self.played:
                self.state.record_verdict(verdict)
                self.played.add(verdict["verdict_id"])

    def _verdict(self, entry: dict, ticket: dict, decided: int) -> dict:
        """One expected entry as a verdict record."""
        hold = entry["decision"] == "hold"
        flagged = [unit for unit in ticket["forbidden"] if unit in entry["allergens_reached"]] if hold else []
        codes = entry.get("reason_codes") or (["low_confidence"] if hold else [])
        return {
            "verdict_id": f"v-{entry['plate_id']}-r{entry['ticket_revision']}-{decided}",
            "ticket_id": ticket["ticket_id"],
            "ticket_revision": entry["ticket_revision"],
            "plate_id": entry["plate_id"],
            "decided_t_ms": decided,
            "trigger": "plate_at_pass" if entry["ticket_revision"] == 1 else "ticket_amended",
            "decision": entry["decision"],
            "confidence": 0.0 if hold else 1.0,
            "pass_threshold": ticket["pass_threshold"],
            "reasons": [{"code": code} for code in codes],
            "flagged": [{"allergen_unit_id": unit, "probability": 1.0} for unit in flagged],
            "chain": [],
            "versions": {"policy": "fake-verdict-feed", "graph": "fake-verdict-feed"},
        }
