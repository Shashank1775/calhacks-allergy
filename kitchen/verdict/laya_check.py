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

MAX_EDGES = 20  # about what fits in Laya's 512 tokens; a longer history can't be read whole, so the check refuses

ACTION_WORDS = {"transfer.contact": "contact", "transfer.scoop": "scoop", "transfer.wipe": "wipe"}


# ---------- the plate's subgraph as text (the model's input) ----------


def node_text(node):
    """One entity and what it carried at the start, e.g. "bin-1 (satay sauce): peanuts 0.98"."""
    name = f"{node['entity_id']} ({node['label']})"
    start = node["start"]
    if start["unknown"]:
        return f"{name}: unknown history, may carry anything"
    carried = [f"{UNIT_NAMES[u]} {p:.2f}" for u, p in sorted(start["probabilities"].items()) if p > 0]
    return f"{name}: {', '.join(carried) if carried else 'clean'}"


def event_text(number, edge):
    """One interaction, e.g. "2. 2.0s contact tool-knife-1 <-> plate-t1 (p 0.90)"."""
    seconds = edge["t_start_ms"] / 1000
    a, b, p = edge["a_id"], edge["b_id"], edge["probability"]
    if edge["action"] == "clean.wash":
        return f"{number}. {seconds:.1f}s wash {a} (p {p:.2f})"
    flow = {"a_to_b": f"{a} -> {b}", "b_to_a": f"{b} -> {a}", "both": f"{a} <-> {b}"}[edge["direction"]]
    return f"{number}. {seconds:.1f}s {ACTION_WORDS[edge['action']]} {flow} (p {p:.2f})"


def time_order(edges):
    """Edges in time order. A wash tied with a transfer goes first, so a tie never erases contamination (OQ-17)."""
    return sorted(edges, key=lambda e: (e["t_start_ms"], e["t_end_ms"], e["action"] != "clean.wash", e["event_id"]))


def plate_state(subgraph):
    """The state the plate model reads (same shape as in training)."""
    edges = [e for e in time_order(subgraph["edges"]) if e["action"] != "unimportant"]
    return {
        "plate": subgraph["plate_id"],
        "start": " | ".join(node_text(n) for n in subgraph["nodes"]),
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
    carried = {}
    for node in subgraph["nodes"]:
        start = node["start"]
        carried[node["entity_id"]] = {u: 1.0 if start["unknown"] else start["probabilities"].get(u, 0.0) for u in UNITS}

    for edge in time_order(subgraph["edges"]):
        a, b, p = edge["a_id"], edge["b_id"], edge["probability"]
        if edge["action"] == "unimportant":
            continue
        if edge["action"] == "clean.wash":
            carried[a] = {u: (1.0 - p) * carried[a][u] for u in UNITS}
            continue
        before = {a: dict(carried[a]), b: dict(carried[b])}  # a "both" edge moves allergens both ways at once
        flows = {"a_to_b": [(a, b)], "b_to_a": [(b, a)], "both": [(a, b), (b, a)]}[edge["direction"]]
        for src, dst in flows:
            for u in UNITS:
                carried[dst][u] = 1.0 - (1.0 - carried[dst][u]) * (1.0 - p * before[src][u])

    return carried[subgraph["plate_id"]]


# ---------- what the verdict does with the answer ----------


def laya_hold_reasons(reached, forbidden, pass_threshold):
    """Hold reasons from Laya's answer: one "laya_flagged" reason per forbidden allergen with P >= 1 - pass_threshold.

    That is the same flag rule the verdict applies to the graph's numbers (kitchen/verdict/README.md), so an
    anaphylactic ticket (0.99) flags anything Laya puts at 1% or more. The verdict policy appends these reasons to
    its own. Laya can only add reasons; an empty list never removes one.
    """
    return [
        {"code": "laya_flagged", "allergen_unit_id": u, "detail": f"Laya: P(reached the plate) = {reached[u]:.3f}"}
        for u in sorted(forbidden)
        if reached[u] >= 1.0 - pass_threshold
    ]


def check_size(subgraph):
    """Refuse a history too long for the model to read whole (fail closed)."""
    if len(subgraph["edges"]) > MAX_EDGES:
        raise LayaCheckUnavailable(f"{len(subgraph['edges'])} edges is more than the {MAX_EDGES} the model can read")


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
        check_size(subgraph)
        if not forbidden:
            return {}
        questions = {f"reached_{u}": reached_question(u) for u in forbidden}
        try:
            answers = self._agent.predict(plate_state(subgraph), questions)["answers"]
            reached = {u: answers[f"reached_{u}"]["noul"] for u in forbidden}
        except Exception as error:
            raise LayaCheckUnavailable(f"the plate model failed to answer: {error}") from error
        if not all(0.0 <= p <= 1.0 for p in reached.values()):  # also False for NaN
            raise LayaCheckUnavailable(f"impossible probability from the plate model: {reached}")
        return reached


class FakeLayaCheck:
    """LayaPlateCheck that answers with the exact noisy-OR result: a perfect Laya, for tests and offline demos."""

    version = "fake"

    def check(self, subgraph, forbidden):
        """P(reached the plate) for each forbidden allergen, computed exactly."""
        check_size(subgraph)
        reached = exact_reach(subgraph)
        return {u: round(reached[u], 4) for u in forbidden}
