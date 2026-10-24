"""Build Laya training rows (train / val / test JSONL) from labeled ticket notes.

Owner: orders (agents)
Step:  orders (training the ticket-note Laya model)
Reads: labeled notes (data/laya_training/raw/**/*.jsonl, gitignored), labeled as in labeling_guide.md
Emits: {id, state, questions, gold} rows that laya.train.finetune reads, plus manifest.json
Rules: questions and states come from kitchen/orders/laya_parser.py, so training and live use always match

    uv run python -m kitchen.orders.training.build_dataset data/laya_training/raw data/laya_training/rows
"""

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from kitchen.orders.laya_parser import SEVERITIES, UNITS, note_questions, note_state

SPLITS = (("test", 0.08), ("val", 0.05))  # the rest is train
ABSENT_P = 0.02  # target for "not mentioned", so no target is a hard 0


def noul_gold(p_true):
    """Soft target for a yes/no question."""
    return {"probabilities": {"false": 1.0 - p_true, "true": p_true}}


def severity_gold(severity, confidence):
    """Soft target for severity: the labeled level gets the confidence, its neighbors share the rest."""
    index = SEVERITIES.index(severity)
    neighbors = [i for i in (index - 1, index + 1) if 0 <= i < len(SEVERITIES)]
    probs = {str(i): 0.0 for i in range(len(SEVERITIES))}
    probs[str(index)] = confidence
    for i in neighbors:
        probs[str(i)] = (1.0 - confidence) / len(neighbors)
    return {"probabilities": probs}


def candidate_units(output):
    """Units offered as answers to an "is it this allergen?" question ("nuts": tree nuts or peanuts)."""
    found = set()
    for question in output["questions"]:
        if question["target"]["field"] != "allergen_unit_id":
            continue
        for option in question["options"]:
            values = option["value"] if isinstance(option["value"], list) else [option["value"]]
            found.update(v for v in values if v in UNITS)
    return found


def note_row(example):
    """One labeled note as a Laya row. Follow-up questions are only trained for restricted allergens."""
    output = example["output"]
    constraints = {c["allergen_unit_id"]: c for c in output["constraints"]}
    exceptions = {e["allergen_unit_id"]: e for e in output["exceptions"]}
    candidates = candidate_units(output)

    questions, gold = {}, {}
    for unit in UNITS:
        asked = note_questions(unit)
        if unit in constraints:
            p_forbid = constraints[unit]["confidence"]["allergen_unit_id"]
        elif unit in candidates:
            p_forbid = 0.5  # ambiguous in the note, so the model should be unsure
        else:
            p_forbid = ABSENT_P
        questions[f"forbid_{unit}"] = asked[f"forbid_{unit}"]
        gold[f"forbid_{unit}"] = noul_gold(p_forbid)
        if unit not in constraints:
            continue

        constraint = constraints[unit]
        p_trace = constraint["confidence"]["trace_sensitive"]
        p_exception = exceptions[unit]["confidence"] if unit in exceptions else 0.05
        follow_ups = {
            "severity": severity_gold(constraint["severity"], constraint["confidence"]["severity"]),
            "traces": noul_gold(p_trace if constraint["trace_sensitive"] else 1.0 - p_trace),
            "exception": noul_gold(p_exception),
        }
        for kind, target in follow_ups.items():
            questions[f"{kind}_{unit}"] = asked[f"{kind}_{unit}"]
            gold[f"{kind}_{unit}"] = target

    state = note_state(example["input"]["note"], example["input"].get("source"))
    return {"id": example["id"], "state": state, "questions": questions, "gold": gold}


def split_of(key):
    """Deterministic split from a hash of the normalized note, so a note never lands in two splits."""
    bucket = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    edge = 0.0
    for name, share in SPLITS:
        edge += share
        if bucket < edge:
            return name
    return "train"


def main():
    source_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {"train": [], "val": [], "test": []}
    seen, duplicates = set(), 0

    for path in sorted(source_dir.rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            example = json.loads(line)
            if example.get("task") != "parse_note":
                continue  # older data also held bin labels, which Laya no longer reads
            key = re.sub(r"[^a-z0-9]+", " ", example["input"]["note"].lower()).strip()
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            rows[split_of(key)].append(note_row(example))

    for split, items in rows.items():
        (out_dir / f"{split}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items))
    manifest = {
        "notes": {split: len(items) for split, items in rows.items()},
        "questions": {split: sum(len(r["questions"]) for r in items) for split, items in rows.items()},
        "duplicates_dropped": duplicates,
        "forbidden_units": dict(
            Counter(
                k[len("forbid_") :]
                for r in rows["train"]
                for k, g in r["gold"].items()
                if k.startswith("forbid_") and g["probabilities"]["true"] >= 0.5
            )
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
