"""decide(): turns the ticket and the graph's reachability into PASS or HOLD with every reason. Pure.
Also clean_confidence() and VerdictSettings, the one home for thresholds, timeouts and the staleness window.

Owner: verdict (agents)
Step:  verdict
Reads: ticket revision; allergen_reach; unknown ancestors; input health; VerdictSettings
Emits: verdict (decision, confidence, reasons, flagged, chain, versions)
Rules: fail closed: any error or doubt holds and sets confidence to 0; plain-code verdict, no model in the decision;
       allergen units only (ticket.forbidden); an unknown ancestor holds

The policy, from kitchen/verdict/README.md:
    confidence = max(0, 1 - sum(p[a] for a in forbidden))    p[a] from reach; 0 if absent
               = 0 if any input is unhealthy (error, missing input, stale plate, unknown history)
    flag a forbidden allergen when 1 - p[a] <= pass_threshold
    hold = not healthy or any flag or confidence < pass_threshold
Every hold carries all its reasons, and decision is "pass" exactly when there are none. decide() needs no constants of
its own (the pass threshold comes from the ticket), so VerdictSettings arrives with the verdict service.
"""

import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)

POLICY_VERSION = "0.1.0"

# The reason codes an unhealthy input can carry (verdict.schema.json); the service reports these, decide() adds the rest.
INPUT_PROBLEM_CODES = ("stale_track", "missing_input", "error")


def reach_probabilities(reach: list[dict]) -> dict[str, float]:
    """Each allergen's probability at the plate. If the graph lists one twice, the larger counts (leans to HOLD)."""
    probabilities = {}
    for entry in reach:
        unit = entry["allergen_unit_id"]
        probabilities[unit] = max(probabilities.get(unit, 0.0), entry["probability"])
    return probabilities


def clean_confidence(forbidden: list[str], probabilities: dict[str, float]) -> float:
    """How sure we are the plate carries none of the forbidden allergens, before input health is applied.

    The sum is the safe bound however the allergens depend on each other: one knife can carry peanut and soy together.
    """
    return max(0.0, 1.0 - sum(probabilities.get(unit, 0.0) for unit in forbidden))


def _chain_for(reach: list[dict], units: list[str]) -> list[dict]:
    """The graph's chains for these allergens, in the ticket's order, taken from each allergen's most likely entry."""
    best = {}
    for entry in reach:
        unit = entry["allergen_unit_id"]
        if unit not in best or entry["probability"] > best[unit]["probability"]:
            best[unit] = entry
    return [step for unit in units if unit in best for step in best[unit]["chain"]]


def _verdict(verdict_id, plate_id, trigger, decided_t_ms, ticket_id, revision, threshold, confidence, reasons, **rest):
    """Assemble a verdict record. `rest` holds flagged, chain and versions."""
    return {
        "verdict_id": verdict_id,
        "ticket_id": ticket_id,
        "ticket_revision": revision,
        "plate_id": plate_id,
        "decided_t_ms": decided_t_ms,
        "trigger": trigger,
        "decision": "hold" if reasons else "pass",
        "confidence": confidence,
        "pass_threshold": threshold,
        "reasons": reasons,
        **rest,
    }


def _decide(verdict_id, plate_id, trigger, decided_t_ms, ticket_id, ticket, reach, unknown_ancestors, problems, graph):
    """decide() without the exception guard."""
    reasons = []
    if ticket is None:
        reasons.append({"code": "missing_input", "detail": f"ticket {ticket_id} not found"})
    if reach is None:
        reasons.append({"code": "error", "detail": "graph reachability unavailable"})
    if unknown_ancestors is None:
        reasons.append({"code": "error", "detail": "graph unknown ancestors unavailable"})
    for problem in problems:
        if problem["code"] not in INPUT_PROBLEM_CODES:
            raise ValueError(f"not an input problem: {problem['code']}")
        reasons.append(dict(problem))

    # With no ticket, nothing says what's forbidden or how strict to be: hold at the strictest threshold. The
    # placeholder revision can't open the gate, which only opens on a PASS.
    forbidden = ticket["forbidden"] if ticket else []
    threshold = ticket["pass_threshold"] if ticket else 1.0
    revision = ticket["revision"] if ticket else 1
    probabilities = reach_probabilities(reach or [])

    flagged = [
        {"allergen_unit_id": unit, "probability": probabilities[unit]}
        for unit in forbidden
        if unit in probabilities and 1.0 - probabilities[unit] <= threshold
    ]
    flag_reasons = [{"code": "forbidden_allergen", "allergen_unit_id": item["allergen_unit_id"]} for item in flagged]
    if unknown_ancestors:
        reasons.append({"code": "unknown_history", "detail": ", ".join(sorted(unknown_ancestors))})

    # Fail closed: an unhealthy input means we can't vouch for the plate at all.
    healthy = not reasons
    confidence = clean_confidence(forbidden, probabilities) if healthy else 0.0
    reasons = flag_reasons + reasons
    if confidence < threshold:
        reasons.append({"code": "low_confidence", "detail": f"{confidence:.4g} < {threshold:.4g}"})

    reached = [unit for unit in forbidden if probabilities.get(unit, 0.0) > 0.0]
    return _verdict(
        verdict_id,
        plate_id,
        trigger,
        decided_t_ms,
        ticket_id,
        revision,
        threshold,
        confidence,
        reasons,
        flagged=flagged,
        chain=_chain_for(reach or [], reached),
        versions={"policy": POLICY_VERSION, "graph": graph or "unavailable"},
    )


def decide(
    *,
    verdict_id: str,
    plate_id: str,
    trigger: str,
    decided_t_ms: int,
    ticket_id: str,
    ticket: dict | None,
    reach: list[dict] | None,
    unknown_ancestors: list[str] | None,
    problems: Sequence[dict] = (),
    graph: str | None = None,
) -> dict:
    """PASS or HOLD for one plate against one ticket revision, as a verdict record (verdict.schema.json).

    `ticket`, `reach` and `unknown_ancestors` are None when they couldn't be read; each becomes a hold reason.
    `problems` are the input-health reasons the service found (stale_track, missing_input, error). `graph` is the
    graph's version. Any exception inside, from a bug or a malformed input, gives a HOLD with reason error.
    """
    try:
        return _decide(
            verdict_id, plate_id, trigger, decided_t_ms, ticket_id, ticket, reach, unknown_ancestors, problems, graph
        )
    except Exception as error:  # fail closed: whatever went wrong, the plate holds
        logger.exception("decide() failed for %s, holding", plate_id)
        revision = ticket.get("revision") if isinstance(ticket, dict) else None
        return _verdict(
            verdict_id,
            plate_id,
            trigger,
            decided_t_ms,
            ticket_id,
            revision if isinstance(revision, int) and revision >= 1 else 1,
            1.0,
            0.0,
            [{"code": "error", "detail": f"{type(error).__name__}: {error}"}],
            flagged=[],
            chain=[],
            versions={"policy": POLICY_VERSION, "graph": graph if isinstance(graph, str) else "unavailable"},
        )
