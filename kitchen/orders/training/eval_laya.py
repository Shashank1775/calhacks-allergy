"""Evaluate a Laya checkpoint on test rows: accuracy, calibration, and dangerous misses.

Owner: orders (agents)
Step:  orders (training the ticket-note Laya model)
Reads: a checkpoint folder; a test.jsonl of {state, questions, gold} rows from build_dataset.py
Emits: a JSON report (default <model>/eval_<data stem>.json)
Rules: the number that matters most is dangerous misses: a real allergen (gold P >= 0.85) that the model
       scores below the drop threshold, so the parser would leave it out without asking

    CUDA_VISIBLE_DEVICES=2 python eval_laya.py --model ~/laya/runs/v1 --data ~/laya/data/full/test.jsonl
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import laya
import numpy as np

DROP_THRESHOLD = 0.15  # below this the adapter leaves the unit out entirely
ASK_THRESHOLD = 0.85  # below this the adapter asks a one-tap question


def gold_vector(question, gold):
    """Gold probabilities in option order."""
    probs = gold["probabilities"]
    if question["type"] == "noul":
        return [probs["false"], probs["true"]]
    return [probs[str(i)] for i in range(len(question["criteria"]))]


def predicted_vector(question, answer):
    """Predicted probabilities in option order."""
    if question["type"] == "noul":
        return [1.0 - answer["noul"], answer["noul"]]
    return [answer["probabilities"][str(i)] for i in range(len(question["criteria"]))]


def expected_calibration_error(confidences, correct, bins=10):
    """Standard ECE: weighted gap between confidence and accuracy over equal-width bins."""
    confidences, correct = np.array(confidences), np.array(correct, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        in_bin = (confidences > low) & (confidences <= high)
        if in_bin.any():
            total += in_bin.mean() * abs(confidences[in_bin].mean() - correct[in_bin].mean())
    return float(total)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", required=True, help="a test.jsonl of {state, questions, gold} rows")
    parser.add_argument("--out", default=None, help="report path (default: <model>/eval_<data stem>.json)")
    args = parser.parse_args()

    agent = laya.load(args.model, device="cuda")
    rows = [json.loads(line) for line in Path(args.data).read_text().splitlines() if line.strip()]

    by_kind = defaultdict(list)  # question kind -> [(predicted, gold)]
    dangerous = []
    for row in rows:
        answers = agent.predict(row["state"], row["questions"])["answers"]
        for qid, question in row["questions"].items():
            kind = qid.split("_")[0]  # forbid, severity, traces or exception
            predicted = predicted_vector(question, answers[qid])
            gold = gold_vector(question, row["gold"][qid])
            by_kind[kind].append((predicted, gold))
            if kind == "forbid" and gold[-1] >= ASK_THRESHOLD and predicted[-1] < DROP_THRESHOLD:
                dangerous.append(
                    {
                        "id": row["id"],
                        "question": qid,
                        "gold": round(gold[-1], 3),
                        "predicted": round(predicted[-1], 3),
                        "state": row["state"],
                    }
                )

    report = {"model": args.model, "data": args.data, "rows": len(rows), "kinds": {}}
    for kind, pairs in sorted(by_kind.items()):
        right = [int(np.argmax(p)) == int(np.argmax(g)) for p, g in pairs]
        confidence = [max(p) for p, _ in pairs]
        entry = {
            "n": len(pairs),
            "accuracy": round(float(np.mean(right)), 4),
            "ece": round(expected_calibration_error(confidence, right), 4),
        }
        if len(pairs[0][0]) == 2:
            entry["mean_abs_error"] = round(float(np.mean([abs(p[1] - g[1]) for p, g in pairs])), 4)
        report["kinds"][kind] = entry

    true_allergens = sum(1 for p, g in by_kind.get("forbid", []) if g[-1] >= ASK_THRESHOLD)
    asked_or_kept = sum(1 for p, g in by_kind.get("forbid", []) if g[-1] >= ASK_THRESHOLD and p[-1] >= DROP_THRESHOLD)
    report["safety"] = {
        "true_allergens": true_allergens,
        "dangerous_misses": len(dangerous),
        "kept_or_asked_rate": round(asked_or_kept / true_allergens, 4) if true_allergens else None,
    }
    report["dangerous_examples"] = dangerous[:50]

    out = Path(args.out) if args.out else Path(args.model) / f"eval_{Path(args.data).stem}.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "dangerous_examples"}, indent=2))


if __name__ == "__main__":
    main()
