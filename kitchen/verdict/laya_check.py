"""Laya's final plate check: a second fine-tuned Laya model reads the plate's subgraph and says, for each forbidden
allergen, how likely it reached the plate. It can only add holds, never remove one.

Owner: verdict (agents)
Step:  verdict
Reads: PlateSubgraph from ProvenanceGraph.subgraph (contracts/schemas/plate_subgraph.schema.json); the ticket's
       forbidden allergens
Emits: {allergen_unit_id: P(reached the plate)}; laya_hold_reasons() turns that into verdict hold reasons
Rules: fail closed: any failure raises LayaCheckUnavailable, which holds the plate;
       stricter only: Laya's answer can add a hold reason, and nothing here can remove one or cause a pass; a history
       too long to read is shortened only in ways that keep or raise every allergen's reach (shorten)

The plate model is separate from the ticket-note model (kitchen/orders/laya_parser.py): different job, different
training data. The wording below is what it was fine-tuned on. Don't edit it without retraining.
"""

from pathlib import Path

from kitchen.shared.interfaces import LayaCheckUnavailable

UNITS = ["milk", "egg", "fish", "shellfish", "tree_nut", "peanut", "wheat", "soy", "sesame"]

# Plain names for the plate model's input text and questions.
UNIT_NAMES = {
    "milk": "dairy",
    "egg": "egg",
    "fish": "fish",
    "shellfish": "shellfish",
    "tree_nut": "tree nuts",
    "peanut": "peanuts",
    "wheat": "wheat",
    "soy": "soy",
    "sesame": "sesame",
}

# The longest history the model reads. It was trained on up to 10 edges; 15 is the owner's call, so 11 to 15 edges are
# longer than anything it saw. Laya is stricter only: a wrong answer there can add a HOLD but never cause a PASS, and
# the graph's own reachability still decides. A longer history is shortened to fit (shorten).
MAX_EDGES = 15

# If laya has to cut the input to fit its 512 tokens (folded starting values make the text longer), the check folds
# the history further, to these many events, and asks again; if it still doesn't fit, it holds (LocalLayaCheck.check).
SHORTER_WINDOWS = (10, 5)

ACTION_WORDS = {"transfer.contact": "contact", "transfer.scoop": "scoop", "transfer.wipe": "wipe"}


# ---------- the plate's history ----------


def time_order(edges):
    """Edges in time order; a wash tied with a transfer goes first (the partner's rule, AGENTS.md OQ-17)."""
    return sorted(edges, key=lambda e: (e["t_start_ms"], e["t_end_ms"], e["action"] != "clean.wash", e["event_id"]))


def flows(edge):
    """The (source, destination) pairs an edge can move allergens along."""
    a, b = edge["a_id"], edge["b_id"]
    return {"a_to_b": [(a, b)], "b_to_a": [(b, a)], "both": [(a, b), (b, a)]}[edge["direction"]]


def on_path(subgraph):
    """The edges on some time-forward path into the plate (washes of entities on those paths included), in order.

    Walks the time-ordered edges backwards. `deadline[e]` is the position of e's last edge on a path to the plate, so
    an edge only counts if it happened before that. The graph should send only these, but anything extra is dropped
    here, so the model always reads exactly what it was trained on.
    """
    ordered = [e for e in time_order(subgraph["edges"]) if e["action"] != "unimportant"]
    deadline = {subgraph["plate_id"]: len(ordered)}
    kept = []
    for position in range(len(ordered) - 1, -1, -1):
        edge = ordered[position]
        if edge["action"] == "clean.wash":
            if position < deadline.get(edge["a_id"], -1):
                kept.append(edge)
            continue
        counts = False
        for src, dst in flows(edge):
            if position < deadline.get(dst, -1):
                deadline[src] = max(deadline.get(src, -1), position)
                counts = True
        if counts:
            kept.append(edge)
    return list(reversed(kept))


def readable_history(subgraph, limit=MAX_EDGES):
    """The on-path edges and the nodes they need, shortened to at most `limit` events, or LayaCheckUnavailable if a
    node is missing. A history that already fits is read exactly as it is."""
    edges = on_path(subgraph)
    nodes = {n["entity_id"]: n for n in subgraph["nodes"]}
    needed = [subgraph["plate_id"]] + [entity for e in edges for entity in (e["a_id"], e["b_id"])]
    missing = sorted({entity for entity in needed if entity not in nodes})
    if missing:
        raise LayaCheckUnavailable(f"the subgraph has edges but no node for {missing}")
    if len(edges) > limit:
        edges, nodes = shorten(edges, nodes, limit)
    return edges, nodes


# ---------- shortening a long history ----------


def shorten(edges, nodes, limit):
    """At most `limit` events that carry at least as much of every allergen onto the plate as `edges` do.

    First, transfers whose sources carry nothing at that moment are dropped: they move nothing, so no reach changes.
    Washes are kept. If that's still too long, the oldest events are folded into what each entity carried after them,
    rounded up to the 2 decimals the model reads, and the model reads those as starting values. Noisy-OR only grows
    when a starting value grows, so every allergen's reach stays the same or goes up (toward HOLD).

    Merging repeated touches into one edge isn't used: it would lower reach. From a source carrying 0.9, two 0.8
    touches give 1 - 0.28 ** 2 = 0.92, and a single edge can give at most 0.9.
    """
    carried = starting_values(nodes)
    moving = []
    for edge in edges:
        if edge["action"] == "clean.wash" or moves_something(edge, carried):
            moving.append(edge)
        apply_edge(edge, carried)
    if len(moving) <= limit:
        return moving, nodes

    folded, kept = moving[:-limit], moving[-limit:]
    start = starting_values(nodes)
    after = carry(folded, nodes)
    return kept, {
        entity: node if after[entity] == start[entity] else folded_node(node, after[entity])
        for entity, node in nodes.items()
    }


def moves_something(edge, carried):
    """Whether a transfer's sources carry any allergen at this moment, so it could move something."""
    return any(carried[src][u] > 0 for src, _ in flows(edge) for u in UNITS)


def folded_node(node, carried):
    """The node starting with what it carried after the folded events, rounded up, never below what it carried."""
    probabilities = {u: round_up(p) for u, p in carried.items() if p > 0}
    return dict(node, start={"probabilities": probabilities, "unknown": False, "source": "propagated"})


def round_up(p):
    """p rounded up to 2 decimals, the precision the model reads. Never below p, even after float rounding."""
    hundredths = round(p * 100)  # the nearest hundredth, one higher if that is below p
    if hundredths / 100 < p:
        hundredths += 1
    return hundredths / 100


# ---------- the history as text (the model's input) ----------


def node_text(node):
    """One entity and what it carried at the start, e.g. "bin-1 (satay sauce): peanuts 0.98"."""
    name = f"{node['entity_id']} ({node['label']})"
    start = node["start"]
    if start["unknown"]:
        return f"{name}: unknown history, may carry anything"
    carried = [f"{UNIT_NAMES[u]} {p:.2f}" for u, p in sorted(start["probabilities"].items()) if p > 0]
    return f"{name}: {', '.join(carried) if carried else 'clean'}"


def event_text(number, edge):
    """One interaction, numbered in time order, e.g. "2. contact tool-knife-1 <-> plate-t1 (p 0.90)"."""
    a, p = edge["a_id"], edge["probability"]
    if edge["action"] == "clean.wash":
        return f"{number}. wash {a} (p {p:.2f})"
    src_dst = {"a_to_b": "->", "b_to_a": "<-", "both": "<->"}[edge["direction"]]
    return f"{number}. {ACTION_WORDS[edge['action']]} {a} {src_dst} {edge['b_id']} (p {p:.2f})"


def plate_state(subgraph, limit=MAX_EDGES):
    """The state the plate model reads: who carried what at the start, then up to `limit` on-path events in order."""
    edges, nodes = readable_history(subgraph, limit)
    mentioned = [entity for e in edges for entity in (e["a_id"], e["b_id"])] + [subgraph["plate_id"]]
    shown = list(dict.fromkeys(mentioned))  # each entity once, in the order the events first mention it
    return {
        "plate": subgraph["plate_id"],
        "start": " | ".join(node_text(nodes[entity]) for entity in shown),
        "events": " | ".join(event_text(i, e) for i, e in enumerate(edges, start=1)) or "none",
    }


def reached_question(unit):
    """The typed question asked about one allergen."""
    return {
        "type": "noul",
        "instructions": f"Following the events in time order, could {UNIT_NAMES[unit]} have reached the plate?",
        "criteria": {
            "false": "no chain of transfers in time order carries it onto the plate, or a wash removed it first",
            "true": "a chain of transfers in time order carries it onto the plate",
        },
    }


# ---------- the exact answer (training labels and the fake) ----------


def exact_reach(subgraph):
    """P(each allergen reached the plate) by noisy-OR along time-ordered edges, with full transfer (k = 1).

    Same math the partner's graph uses (AGENTS.md, Graph math), with the most conservative efficiency. Used to label
    training data and by FakeLayaCheck; the verdict's own reachability still comes from the partner's graph. It reads
    the same (possibly shortened) history the model reads.
    """
    edges, nodes = readable_history(subgraph)
    return carry(edges, nodes)[subgraph["plate_id"]]


def starting_values(nodes):
    """What each entity carried at the start: its profile, or every allergen at 1 for an unknown history."""
    return {
        entity: {u: 1.0 if node["start"]["unknown"] else node["start"]["probabilities"].get(u, 0.0) for u in UNITS}
        for entity, node in nodes.items()
    }


def carry(edges, nodes):
    """What every entity carries after `edges` in order, starting from its starting values."""
    carried = starting_values(nodes)
    for edge in edges:
        apply_edge(edge, carried)
    return carried


def apply_edge(edge, carried):
    """Apply one edge to `carried` in place: a wash keeps (1 - p) of everything, a transfer moves by noisy-OR.

    An allergen the source doesn't carry is skipped, not multiplied by 1: 1 - (1 - x) can differ from x in the last
    bit, and skipping keeps "an edge that moves nothing changes nothing" exactly true, which shorten relies on.
    """
    p = edge["probability"]
    if edge["action"] == "clean.wash":
        carried[edge["a_id"]] = {u: (1.0 - p) * carried[edge["a_id"]][u] for u in UNITS}
        return
    before = {entity: dict(carried[entity]) for entity in (edge["a_id"], edge["b_id"])}  # "both" moves at once
    for src, dst in flows(edge):
        for u in UNITS:
            if before[src][u] > 0:
                carried[dst][u] = 1.0 - (1.0 - carried[dst][u]) * (1.0 - p * before[src][u])


# ---------- what the verdict does with the answer ----------


def laya_hold_reasons(reached, forbidden, pass_threshold):
    """Hold reasons from Laya's answer: one "laya_flagged" reason per forbidden allergen with 1 - P <= pass_threshold.

    That is the same flag rule the verdict applies to the graph's numbers (kitchen/verdict/README.md), so an
    anaphylactic ticket (0.99) flags anything Laya puts at 1% or more. The verdict policy appends these reasons to
    its own. Laya can only add reasons; an empty list never removes one.
    """
    return [
        {"code": "laya_flagged", "allergen_unit_id": u, "detail": f"Laya: P(reached the plate) = {reached[u]:.3f}"}
        for u in sorted(forbidden)
        if 1.0 - reached[u] <= pass_threshold
    ]


# ---------- the real model and the fake ----------


def was_cut(usage):
    """Whether laya cut part of the input to fit its 512 tokens, so the model never read all of the history.

    Any of laya's three signs counts, and a missing one raises (KeyError), so an answer we can't vouch for holds.
    """
    return bool(usage["truncated"]) or usage["state_tokens_dropped"] > 0 or bool(usage["truncated_questions"])


class LocalLayaCheck:
    """LayaPlateCheck backed by the fine-tuned plate model, loaded from a local folder."""

    def __init__(self, model_dir):
        try:
            import laya  # heavy (torch); only needed when the real model is used

            self._agent = laya.load(str(model_dir))
        except Exception as error:
            raise LayaCheckUnavailable(f"could not load the plate model from {model_dir}: {error}") from error
        self.version = Path(model_dir).name

    def check(self, subgraph, forbidden):
        """P(reached the plate) for each forbidden allergen. Any failure or odd answer raises LayaCheckUnavailable."""
        try:
            state = plate_state(subgraph)
            if len(forbidden) == 0:
                return {}
            questions = {f"reached_{u}": reached_question(u) for u in forbidden}
            result = self._agent.predict(state, questions)
            for limit in SHORTER_WINDOWS:  # folding further keeps or raises every allergen's reach (shorten)
                if not was_cut(result["usage"]):
                    break
                result = self._agent.predict(plate_state(subgraph, limit), questions)
            if was_cut(result["usage"]):
                raise ValueError(f"the plate's history didn't fit the model's input: {result['usage']}")
            answers = result["answers"]
            reached = {u: float(answers[f"reached_{u}"]["noul"]) for u in forbidden}
            if not all(0.0 <= p <= 1.0 for p in reached.values()):  # also False for NaN
                raise ValueError(f"impossible probability {reached}")
        except LayaCheckUnavailable:
            raise
        except Exception as error:
            raise LayaCheckUnavailable(f"the plate model failed to answer: {error}") from error
        return reached


class FakeLayaCheck:
    """LayaPlateCheck that answers with the exact noisy-OR result: a perfect Laya, for tests and offline demos."""

    version = "fake"

    def check(self, subgraph, forbidden):
        """P(reached the plate) for each forbidden allergen, computed exactly."""
        try:
            reached = exact_reach(subgraph)
            return {u: reached[u] for u in forbidden}
        except LayaCheckUnavailable:
            raise
        except Exception as error:
            raise LayaCheckUnavailable(f"the fake plate check failed: {error}") from error
