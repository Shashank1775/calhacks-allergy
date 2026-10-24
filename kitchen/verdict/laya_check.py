"""Laya's final plate check: a second fine-tuned Laya model reads the plate's subgraph and says, for each forbidden
allergen, how likely it reached the plate. It can only add holds, never remove one.

Owner: verdict (agents)
Step:  verdict
Reads: PlateSubgraph from ProvenanceGraph.subgraph (contracts/schemas/plate_subgraph.schema.json); the ticket's
       forbidden allergens
Emits: {allergen_unit_id: P(reached the plate)}; laya_hold_reasons() turns that into verdict hold reasons
Rules: fail closed: any failure raises LayaCheckUnavailable, which holds the plate;
       stricter only: Laya's answer can add a hold reason, and nothing here can remove one or cause a pass

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

# The longest on-path history in the training data, and well inside Laya's 512 tokens. A longer one was never seen
# in training, so the check refuses it (fail closed) rather than guess.
MAX_EDGES = 10

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


def readable_history(subgraph):
    """The on-path edges and the nodes they need, or LayaCheckUnavailable if it's too long or a node is missing."""
    edges = on_path(subgraph)
    if len(edges) > MAX_EDGES:
        raise LayaCheckUnavailable(
            f"{len(edges)} edges on the plate's paths; the model was trained on up to {MAX_EDGES}"
        )
    nodes = {n["entity_id"]: n for n in subgraph["nodes"]}
    needed = [subgraph["plate_id"]] + [entity for e in edges for entity in (e["a_id"], e["b_id"])]
    missing = sorted({entity for entity in needed if entity not in nodes})
    if missing:
        raise LayaCheckUnavailable(f"the subgraph has edges but no node for {missing}")
    return edges, nodes


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


def plate_state(subgraph):
    """The state the plate model reads: who carried what at the start, then the on-path events in order."""
    edges, nodes = readable_history(subgraph)
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
    training data and by FakeLayaCheck; the verdict's own reachability still comes from the partner's graph.
    """
    edges, nodes = readable_history(subgraph)
    carried = {
        entity: {u: 1.0 if node["start"]["unknown"] else node["start"]["probabilities"].get(u, 0.0) for u in UNITS}
        for entity, node in nodes.items()
    }
    for edge in edges:
        p = edge["probability"]
        if edge["action"] == "clean.wash":
            carried[edge["a_id"]] = {u: (1.0 - p) * carried[edge["a_id"]][u] for u in UNITS}
            continue
        before = {entity: dict(carried[entity]) for entity in (edge["a_id"], edge["b_id"])}  # "both" moves at once
        for src, dst in flows(edge):
            for u in UNITS:
                carried[dst][u] = 1.0 - (1.0 - carried[dst][u]) * (1.0 - p * before[src][u])
    return carried[subgraph["plate_id"]]


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
            answers = self._agent.predict(state, questions)["answers"]
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
