"""Generate training rows for Laya's plate check: random small kitchens, each plate's subgraph, and the exact answer.

Owner: verdict (agents)
Step:  verdict (training the plate-check Laya model)
Reads: nothing; kitchens are random but seeded, so every run gives the same rows
Emits: train.jsonl, val.jsonl, test.jsonl of {id, state, questions, gold} rows for laya.train.finetune, plus
       golden.jsonl (the six golden scenarios as rows) and manifest.json
Rules: labels come from exact_reach() in kitchen/verdict/laya_check.py and states from plate_state(), so training
       and live use always match; best case only (clear probabilities, short histories), as the owner asked

    uv run python -m kitchen.verdict.training.plate_data data/laya_training/plates
"""

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from kitchen.verdict.laya_check import UNITS, exact_reach, plate_state, reached_question, time_order

SCENARIOS = Path(__file__).resolve().parents[3] / "contracts" / "scenarios"

BIN_POOL = [
    ("satay sauce", {"peanut": 0.98, "soy": 0.9}),
    ("butter", {"milk": 0.99}),
    ("steamed rice", {}),
    ("shrimp", {"shellfish": 0.99}),
    ("hummus", {"sesame": 0.97}),
    ("caesar dressing", {"egg": 0.95, "milk": 0.95, "fish": 0.9}),
    ("tempura batter", {"wheat": 0.98, "egg": 0.9}),
    ("shredded cheese", {"milk": 0.99}),
    ("tofu", {"soy": 0.98}),
    ("pesto", {"tree_nut": 0.95, "milk": 0.9}),
    ("lettuce", {}),
    ("grilled salmon", {"fish": 0.99}),
    ("bread crumbs", {"wheat": 0.99}),
    ("almond slivers", {"tree_nut": 0.99}),
]
TOOLS = ["knife", "spoon", "tongs", "ladle", "spatula", "whisk"]
GOLD_FLOOR = 0.001  # keep soft targets off exact 0 and 1


# ---------- the plate's subgraph ----------


def ancestry(edges, plate_id):
    """The edges on some time-forward path into the plate, washes of entities on those paths included.

    Walks the time-ordered edges backwards. `deadline[e]` is the position of e's last edge on a path to the plate,
    so an edge only counts if it happened before that.
    """
    ordered = time_order(edges)
    deadline = {plate_id: len(ordered)}
    kept = []
    for position in range(len(ordered) - 1, -1, -1):
        edge = ordered[position]
        a, b = edge["a_id"], edge["b_id"]
        if edge["action"] == "unimportant":
            continue
        if edge["action"] == "clean.wash":
            if position < deadline.get(a, -1):
                kept.append(edge)
            continue
        flows = {"a_to_b": [(a, b)], "b_to_a": [(b, a)], "both": [(a, b), (b, a)]}[edge["direction"]]
        on_path = False
        for src, dst in flows:
            if position < deadline.get(dst, -1):
                deadline[src] = max(deadline.get(src, -1), position)
                on_path = True
        if on_path:
            kept.append(edge)
    return list(reversed(kept))


def make_subgraph(nodes, edges, plate_id, as_of_t_ms, distractors=()):
    """A PlateSubgraph: the plate's ancestry, plus any extra (non-path) edges, and the nodes they mention."""
    kept = ancestry([e for e in edges if e["t_start_ms"] <= as_of_t_ms], plate_id)
    kept_ids = {e["event_id"] for e in kept}
    chosen = time_order(kept + [e for e in distractors if e["event_id"] not in kept_ids])
    mentioned = {plate_id} | {e["a_id"] for e in chosen} | {e["b_id"] for e in chosen}
    return {
        "plate_id": plate_id,
        "as_of_t_ms": as_of_t_ms,
        "nodes": [n for n in nodes if n["entity_id"] in mentioned],
        "edges": chosen,
    }


# ---------- random kitchens ----------


def node(entity_id, kind, label, origin="shift_start", probabilities=None):
    """A subgraph node with its starting allergen profile."""
    if origin == "unknown":
        start = {"probabilities": {}, "unknown": True, "source": "unknown_history"}
    elif kind == "bin":
        start = {"probabilities": dict(probabilities or {}), "unknown": False, "source": "manual"}
    else:
        start = {"probabilities": {}, "unknown": False, "source": "shift_start_clean"}
    return {"entity_id": entity_id, "kind": kind, "label": label, "origin": origin, "start": start}


def random_kitchen(rng):
    """Bins, tools, hands and one plate. Now and then a tool with unknown history."""
    slots = rng.sample(range(1, 9), rng.randint(2, 4))
    bins = []
    for slot in slots:
        label, probabilities = rng.choice(BIN_POOL)
        bins.append(node(f"bin-{slot}", "bin", label, probabilities=probabilities))
    tool_kinds = rng.sample(TOOLS, rng.randint(1, 3))
    tools = [node(f"tool-{kind}-{rng.randint(1, 20)}", "tool", kind) for kind in tool_kinds]
    if rng.random() < 0.15:
        tools[-1] = node(tools[-1]["entity_id"], "tool", tools[-1]["label"], origin="unknown")
    hands = [
        node(f"hand-p1-{side}", "hand", f"{side} hand") for side in rng.sample(["left", "right"], rng.randint(1, 2))
    ]
    plate_origin = "clean_stack" if rng.random() < 0.1 else "shift_start"
    plate = node(f"plate-t{rng.randint(1, 30)}", "plate", "plate", origin=plate_origin)
    return bins, tools, hands, plate


STORY_WEIGHTS = {"carry": 45, "carry_washed": 15, "too_late": 10, "clean_path": 20, "untouched": 10}


def random_steps(rng, bins, tools, hands, plate):
    """One story as (action, a, b, direction) steps, picked so contaminated and clean plates are both common.

    carry: something picks up an allergen and later touches the plate (sometimes handed tool to hand on the way).
    carry_washed: the same, with a wash before it touches the plate. too_late: it touches the plate first.
    clean_path: it only touched clean food. untouched: it never touches the plate.
    """
    story = rng.choices(list(STORY_WEIGHTS), weights=list(STORY_WEIGHTS.values()))[0]
    allergen_bins = [b for b in bins if b["start"]["probabilities"]] or bins
    clean_bins = [b for b in bins if not b["start"]["probabilities"]]

    mover = rng.choice(tools + hands)
    source = rng.choice(clean_bins) if story == "clean_path" and clean_bins else rng.choice(allergen_bins)
    if story == "clean_path" and not clean_bins:
        pickup = []  # nothing clean to touch: the plate gets a clean tool
    elif mover["kind"] == "tool":
        pickup = [("transfer.scoop", mover, source, "b_to_a")]
    else:
        pickup = [("transfer.contact", mover, source, "both")]

    if pickup and mover["kind"] == "tool" and rng.random() < 0.3:  # handed on: the hand touches the tool
        hand = rng.choice(hands)
        pickup.append(("transfer.contact", hand, mover, "both"))
        mover = hand

    if mover["kind"] == "hand" and rng.random() < 0.3:
        touch = [("transfer.wipe", mover, plate, "a_to_b")]
    else:
        touch = [("transfer.contact", mover, plate, "both")]

    if story == "carry_washed":
        return pickup + [("clean.wash", mover, mover, "a_to_b")] + touch
    if story == "too_late":
        return touch + pickup
    if story == "untouched":
        return pickup
    return pickup + touch


def random_noise(rng, bins, tools, hands, plate):
    """One unrelated-looking step: another scoop, contact or wash somewhere in the kitchen."""
    mover = rng.choice(tools + hands)
    kind = rng.choice(["scoop", "bin_contact", "hand_tool", "wash", "plate"])
    if kind == "scoop" and tools:
        return ("transfer.scoop", rng.choice(tools), rng.choice(bins), "b_to_a")
    if kind == "hand_tool" and tools:
        return ("transfer.contact", rng.choice(hands), rng.choice(tools), "both")
    if kind == "wash":
        return ("clean.wash", mover, mover, "a_to_b")
    if kind == "plate" and rng.random() < 0.3:
        return ("transfer.contact", mover, plate, "both")
    return ("transfer.contact", mover, rng.choice(bins), "both")


def random_edges(rng, bins, tools, hands, plate):
    """A story plus 0 to 3 noise steps, as time-ordered interaction events. Returns (edges, as_of_t_ms)."""
    steps = random_steps(rng, bins, tools, hands, plate)
    for _ in range(rng.randint(0, 3)):
        steps.insert(rng.randint(0, len(steps)), random_noise(rng, bins, tools, hands, plate))

    edges, t = [], rng.randint(500, 3000)
    for action, a, b, direction in steps:
        low = 0.9 if action == "clean.wash" else 0.85
        edges.append(
            {
                "event_id": f"e{len(edges) + 1}",
                "t_start_ms": t,
                "t_end_ms": t + rng.randint(200, 600),
                "a_id": a["entity_id"],
                "b_id": b["entity_id"],
                "action": action,
                "probability": round(rng.uniform(low, 0.99), 2),
                "direction": direction,
                "classifier": {"name": "sim_ground_truth", "version": "0.1.0"},
            }
        )
        t += rng.randint(400, 3000)
    return edges, t


def random_row(rng, index):
    """One training row: a random kitchen's plate subgraph, every allergen question, and the exact answers."""
    bins, tools, hands, plate = random_kitchen(rng)
    edges, as_of = random_edges(rng, bins, tools, hands, plate)
    nodes = bins + tools + hands + [plate]
    on_path = {e["event_id"] for e in ancestry(edges, plate["entity_id"])}
    off_path = [e for e in edges if e["event_id"] not in on_path]
    distractors = rng.sample(off_path, min(len(off_path), rng.randint(0, 2))) if rng.random() < 0.3 else []
    subgraph = make_subgraph(nodes, edges, plate["entity_id"], as_of, distractors)
    return row_for(f"plate-{index}", subgraph)


def row_for(row_id, subgraph):
    """A {state, questions, gold} row for one subgraph."""
    reached = exact_reach(subgraph)
    gold = {}
    for unit in UNITS:
        p = min(max(reached[unit], GOLD_FLOOR), 1.0 - GOLD_FLOOR)
        gold[f"reached_{unit}"] = {"probabilities": {"false": 1.0 - p, "true": p}}
    return {
        "id": row_id,
        "state": plate_state(subgraph),
        "questions": {f"reached_{u}": reached_question(u) for u in UNITS},
        "gold": gold,
        "subgraph": subgraph,  # kept for evaluation; laya.train reads only state, questions and gold
    }


# ---------- golden scenarios as subgraphs ----------


def entity_label(entity_id):
    """A readable label from an entity ID: tool-knife-1 -> knife, hand-p1-right -> right hand, plate-t1 -> plate."""
    parts = entity_id.split("-")
    if parts[0] == "tool":
        return parts[1]
    if parts[0] == "hand":
        return f"{parts[-1]} hand"
    return parts[0]


def subgraph_from_scenario(scenario, plate_id, as_of_t_ms):
    """The PlateSubgraph a perfect graph would return for one plate in a golden scenario."""
    late = {e["entity_id"]: e.get("origin", "unknown") for e in scenario.get("late_entities", [])}
    nodes = {
        b["entity_id"]: {
            "entity_id": b["entity_id"],
            "kind": "bin",
            "label": b["label"],
            "origin": "shift_start",
            "start": b["allergen_profile"],
        }
        for b in scenario["bins"]
    }
    for edge in scenario["events"]:
        for entity_id in (edge["a_id"], edge["b_id"]):
            if entity_id not in nodes:
                kind = entity_id.split("-")[0]
                nodes[entity_id] = node(
                    entity_id, kind, entity_label(entity_id), origin=late.get(entity_id, "shift_start")
                )
    if plate_id not in nodes:
        nodes[plate_id] = node(plate_id, "plate", "plate", origin=late.get(plate_id, "shift_start"))
    return make_subgraph(list(nodes.values()), scenario["events"], plate_id, as_of_t_ms)


def golden_rows():
    """One row per expected verdict in each golden scenario, with that ticket revision's forbidden allergens,
    pass threshold, and the allergens the scenario expects to reach the plate."""
    rows = []
    for path in sorted(SCENARIOS.glob("*.json")):
        scenario = json.loads(path.read_text())
        tickets = {(t["ticket_id"], t["revision"]): t for t in scenario["tickets"] + scenario.get("amendments", [])}
        for expected in scenario["expected"]:
            at_pass = next(p for p in scenario["plates_at_pass"] if p["plate_id"] == expected["plate_id"])
            ticket = tickets[(at_pass["ticket_id"], expected["ticket_revision"])]
            subgraph = subgraph_from_scenario(scenario, at_pass["plate_id"], at_pass["t_ms"])
            row = row_for(f"golden-{scenario['scenario_id']}-r{expected['ticket_revision']}", subgraph)
            row.update(
                forbidden=ticket["forbidden"],
                pass_threshold=ticket["pass_threshold"],
                expected_reached=sorted(expected["allergens_reached"]),
            )
            rows.append(row)
    return rows


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arg_parser.add_argument("out_dir")
    arg_parser.add_argument("--count", type=int, default=1600)
    arg_parser.add_argument("--seed", type=int, default=7)
    args = arg_parser.parse_args()

    rng = random.Random(args.seed)
    rows = [random_row(rng, i) for i in range(args.count)]
    splits = {"test": rows[:200], "val": rows[200:300], "train": rows[300:]}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, items in splits.items():
        # laya.train reads only id, state, questions and gold
        lean = [{k: v for k, v in r.items() if k != "subgraph"} for r in items]
        (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lean))
    (out / "golden.jsonl").write_text("".join(json.dumps(r) + "\n" for r in golden_rows()))

    reached = Counter(
        "some allergen reaches the plate"
        if any(g["probabilities"]["true"] >= 0.5 for g in r["gold"].values())
        else "nothing reaches the plate"
        for r in rows
    )
    manifest = {
        "rows": {name: len(items) for name, items in splits.items()},
        "balance": dict(reached),
        "max_edges": max(len(r["subgraph"]["edges"]) for r in rows),
        "seed": args.seed,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
