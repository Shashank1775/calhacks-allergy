"""Verdict service: on plate-at-pass, a ticket amendment or a late edge, query the graph, decide, and record the verdict.
Also FakeVerdictFeed, which replays scenario verdicts on a Clock for outputs to build against.

Owner: verdict (agents)
Step:  verdict
Reads: StateClient (ticket, entity zone, plate_binding); ProvenanceGraph (incl. subgraph); LayaPlateCheck; Clock
Emits: verdict via StateClient.record_verdict
Rules: fail closed: StateUnavailable, GraphUnavailable, LayaCheckUnavailable or a stale plate holds;
       late changes recheck everything: amendments and late edges recompute from full history

Triggers are table changes (contracts/apis.md): a new entity_state row that moves a plate into a zone of kind "pass"
judges it with trigger plate_at_pass, and a ticket update to a higher revision rejudges every plate that already has
a verdict for that ticket, with trigger ticket_amended. A new interaction edge the plate's latest verdict didn't account
for rejudges it, with trigger late_edge: an edge that starts at or before that verdict (interactions writes an edge
after classifying it, so the last contact before the pass can land after the plate was judged), or one that touches the
plate while it waits at the pass. Every judgment reads the full history again; nothing is carried over from an earlier
verdict. Laya's plate check reads the graph's subgraph for the plate and can only add holds.
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


def answer(verdict: dict) -> tuple:
    """What a verdict tells the kitchen: its ticket and revision, its decision, and which reasons (by code and
    allergen). Two verdicts with the same answer show and say the same thing on the gate and the expo board."""
    reasons = sorted((reason["code"], reason.get("allergen_unit_id", "")) for reason in verdict["reasons"])
    return (verdict["ticket_id"], verdict["ticket_revision"], verdict["decision"], tuple(reasons))


class VerdictService:
    """Judges plates as they reach the pass, and again when their ticket is amended.

    Call start() to subscribe. Verdicts the database refused (StateUnavailable) wait in `pending`, and amended tickets
    whose plates couldn't be listed wait in `amended`; retry() handles both, and every trigger runs it, so a HOLD made
    while the database was down still lands. A service loop should also call retry() now and then.

    Late edges: each judged plate has a reach cutoff, the later of its last plate_at_pass or ticket_amended judgment
    and the end of the last edge touching it. A new edge rechecks the plate if it touches it, or if it starts at or
    before the cutoff. That's enough: an edge that doesn't touch the plate can only reach it through a later edge into
    it, and whichever of the two lands last triggers a recheck that reads the full history. A recheck whose answer
    matches the plate's latest recorded verdict writes nothing, so a held plate isn't recorded and spoken again on every
    touch.
    """

    def __init__(self, state, graph, plate_check, clock, settings: VerdictSettings | None = None):
        self.state = state
        self.graph = graph
        self.plate_check = plate_check
        self.clock = clock
        self.settings = settings or VerdictSettings()
        self.pending: list[dict] = []
        self.amended: set[str] = set()
        self._reach_cutoff_t_ms: dict[str, int] = {}  # per judged plate, see the class docstring
        self._recorded_answer: dict[str, tuple] = {}  # per plate, answer() of its latest recorded verdict
        self._late_rechecks = 0  # numbers each late-edge verdict_id
        self._at_pass: set[str] = set()
        self._newest_row: dict[str, tuple[int, int]] = {}  # per plate, (t_ms, version) of the store's newest row
        self._unsubscribe = []

    def start(self) -> None:
        """Subscribe to entity_state, ticket and interaction changes. The callbacks never raise into the writer.

        Verdicts already in the database (a restarted service) seed each plate's reach cutoff and recorded answer, so a
        late edge still rechecks a plate judged before the restart.
        """
        for verdict in sorted(self.state.list_verdicts(), key=lambda verdict: verdict["decided_t_ms"]):
            plate_id = verdict["plate_id"]
            if verdict["trigger"] != "late_edge":
                cutoff = max(self._reach_cutoff_t_ms.get(plate_id, verdict["decided_t_ms"]), verdict["decided_t_ms"])
                self._reach_cutoff_t_ms[plate_id] = cutoff
            self._recorded_answer[plate_id] = answer(verdict)
        self._unsubscribe = [
            self.state.subscribe("entity_state", self.on_entity_state),
            self.state.subscribe("ticket", self.on_ticket),
            self.state.subscribe("interaction", self.on_interaction),
        ]

    def stop(self) -> None:
        """Unsubscribe."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []

    def on_entity_state(self, change) -> None:
        """Judge a plate when its new state puts it in a pass zone and it wasn't there before.

        Only the entity store's newest row says where a plate is. A row the graph wrote (caused_by set) copies a zone
        forward, possibly a stale one (contracts/state-tables.md: the store owns zone, the graph owns allergens), and a
        store row older by (t_ms, version) than one already seen was written late. Either one, read as a move, could
        take a judged plate out of the pass and have the next real row judge it twice, so both are skipped. Skipping
        them never skips a judgment: the store's own row for a plate reaching the pass is still its newest.
        """
        try:
            row = change.row
            if not row.entity_id.startswith("plate-") or row.caused_by is not None:
                return
            newest = self._newest_row.get(row.entity_id)
            if newest is not None and (row.t_ms, row.version) < newest:
                return
            self._newest_row[row.entity_id] = (row.t_ms, row.version)
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

    def on_interaction(self, change) -> None:
        """Rejudge every plate a new edge could reach that its latest verdict didn't account for (trigger late_edge)."""
        try:
            if change.kind == "insert":
                for plate_id in self.plates_reached_late(change.row):
                    self.judge(plate_id, "late_edge")
        except Exception:
            logger.exception("verdict trigger failed on interaction %s", change.row)
        self.retry()

    def plates_reached_late(self, edge: dict) -> list[str]:
        """The judged plates `edge` touches, or starts at or before the reach cutoff of. Moves the cutoff of each plate
        it touches to the edge's end. An unimportant edge carries nothing, so it reaches no plate."""
        if edge["action"] == "unimportant":
            return []
        touched = {edge["a_id"], edge["b_id"]}
        for plate_id in touched & self._reach_cutoff_t_ms.keys():
            self._reach_cutoff_t_ms[plate_id] = max(self._reach_cutoff_t_ms[plate_id], edge["t_end_ms"])
        return sorted(
            plate_id
            for plate_id, cutoff_t_ms in self._reach_cutoff_t_ms.items()
            if plate_id in touched or edge["t_start_ms"] <= cutoff_t_ms
        )

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
        if trigger != "late_edge":
            self._reach_cutoff_t_ms[plate_id] = max(self._reach_cutoff_t_ms.get(plate_id, now), now)
        try:
            inputs = self.read_inputs(plate_id, now)
        except Exception as error:  # fail closed: read_inputs guards its own reads; this catches anything it missed
            logger.exception("reading verdict inputs for %s failed, holding", plate_id)
            inputs = self.blank_inputs()
            inputs["problems"].append(error_reason(error))
        # Late-edge rechecks can land in the same millisecond as the verdict they replace, and as each other (one per
        # edge). A numbered suffix keeps the database from dropping one as a duplicate verdict_id, the last included.
        suffix = ""
        if trigger == "late_edge":
            self._late_rechecks += 1
            suffix = f"-late{self._late_rechecks}"
        verdict = decide(
            verdict_id=f"v-{plate_id}-r{inputs['revision']}-{now}{suffix}",
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
        if trigger == "late_edge" and self._recorded_answer.get(plate_id) == answer(verdict):
            return verdict  # nothing new to show or say
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
            else:
                # Only a verdict that landed counts as said, so a dropped one can't silence a later recheck.
                self._recorded_answer[self.pending[0]["plate_id"]] = answer(self.pending[0])
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
