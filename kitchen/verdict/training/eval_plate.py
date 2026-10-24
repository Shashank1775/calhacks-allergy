"""Evaluate a plate-check Laya checkpoint: accuracy, missed allergens, false flags, and the golden scenarios.

Owner: verdict (agents)
Step:  verdict (training the plate-check Laya model)
Reads: a checkpoint folder; test.jsonl and golden.jsonl from plate_data.py
Emits: a JSON report (default <model>/eval_plate.json)
Rules: the numbers that matter: missed allergens (gold P >= 0.5 but the model says < 0.5), and how close to 0 the
       model scores clean plates, because the verdict flags an allergen at P >= 1 - pass_threshold (0.01 for an
       anaphylactic ticket), so a sloppy "clean" would hold clean plates

    CUDA_VISIBLE_DEVICES=3 python eval_plate.py --model ~/laya/runs/plate-v1 --data ~/laya/data/plates
"""

import argparse
import json
from pathlib import Path

import laya

FLAG_THRESHOLD = 0.5  # "more likely than not reached", for accuracy
PASS_THRESHOLDS = [
    0.80,
    0.90,
    0.95,
    0.99,
]  # the verdict flags at P >= 1 - pass_threshold (kitchen/verdict/laya_check.py)


def read_rows(path):
    """Rows from a JSONL file."""
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def score(agent, rows):
    """Per-question accuracy at FLAG_THRESHOLD, missed allergens and false flags."""
    total = right = 0
    missed, false_flags, errors = [], [], []
    clean_scores, reached_scores = [], []  # model scores where gold is ~0, and where gold is >= 0.5
    for row in rows:
        answers = agent.predict(row["state"], row["questions"])["answers"]
        for key in row["questions"]:
            gold = row["gold"][key]["probabilities"]["true"]
            predicted = answers[key]["noul"]
            total += 1
            right += (gold >= FLAG_THRESHOLD) == (predicted >= FLAG_THRESHOLD)
            errors.append(abs(gold - predicted))
            if gold < 0.01:
                clean_scores.append(predicted)
            elif gold >= FLAG_THRESHOLD:
                reached_scores.append(predicted)
            example = {"id": row["id"], "question": key, "gold": round(gold, 3), "predicted": round(predicted, 3)}
            if gold >= FLAG_THRESHOLD > predicted:
                missed.append({**example, "events": row["state"]["events"]})
            elif predicted >= FLAG_THRESHOLD > gold:
                false_flags.append({**example, "events": row["state"]["events"]})
    clean_scores.sort()
    return {
        "clean_score_percentiles": {
            f"p{q}": round(clean_scores[min(len(clean_scores) - 1, int(len(clean_scores) * q / 100))], 4)
            for q in (50, 90, 99)
        },
        "clean_score_max": round(clean_scores[-1], 4),
        "false_holds_by_pass_threshold": {
            str(t): round(sum(s >= 1 - t for s in clean_scores) / len(clean_scores), 4) for t in PASS_THRESHOLDS
        },
        "reached_score_min": round(min(reached_scores), 4) if reached_scores else None,
        "questions": total,
        "accuracy": round(right / total, 4),
        "mean_abs_error": round(sum(errors) / total, 4),
        "missed_allergens": len(missed),
        "false_flags": len(false_flags),
        "missed_examples": missed[:20],
        "false_flag_examples": false_flags[:20],
    }


def golden(agent, rows):
    """For each golden scenario: Laya's scores for the ticket's forbidden allergens, and whether it flags exactly the
    expected ones at the ticket's own pass threshold (P >= 1 - pass_threshold)."""
    results = []
    for row in rows:
        answers = agent.predict(row["state"], row["questions"])["answers"]
        scores = {u: round(answers[f"reached_{u}"]["noul"], 4) for u in row["forbidden"]}
        flagged = sorted(u for u, p in scores.items() if p >= 1 - row["pass_threshold"])
        expected = sorted(set(row["expected_reached"]) & set(row["forbidden"]))
        results.append(
            {
                "id": row["id"],
                "pass_threshold": row["pass_threshold"],
                "scores": scores,
                "expected": expected,
                "flagged": flagged,
                "ok": flagged == expected,
            }
        )
    return results


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arg_parser.add_argument("--model", required=True)
    arg_parser.add_argument("--data", required=True, help="folder with test.jsonl and golden.jsonl")
    arg_parser.add_argument("--out", default=None)
    args = arg_parser.parse_args()

    agent = laya.load(args.model, device="cuda")
    report = {
        "model": args.model,
        "test": score(agent, read_rows(Path(args.data) / "test.jsonl")),
        "golden": golden(agent, read_rows(Path(args.data) / "golden.jsonl")),
    }
    out = Path(args.out) if args.out else Path(args.model) / "eval_plate.json"
    out.write_text(json.dumps(report, indent=2))
    summary = {k: v for k, v in report["test"].items() if not k.endswith("examples")}
    print(json.dumps({"test": summary, "golden": report["golden"]}, indent=2))


if __name__ == "__main__":
    main()
