"""Run a LayaParser over the controlled demo notes and report what it got right.

Owner: orders (agents)
Step:  orders
Reads: kitchen/orders/eval/demo_notes.json; a LayaParser (the keyword fake, or the fine-tuned Laya)
Emits: a PASS/FAIL line per note, and a non-zero exit code if anything failed
Rules: a missed allergen fails; an extra allergen at 0.5 confidence or more fails; a wrong severity fails
       unless the parser asks about it (severity sets the ticket's pass_threshold)

    uv run python -m kitchen.orders.eval.run_demo                                  # the keyword fake
    uv run python -m kitchen.orders.eval.run_demo --model data/models/laya-v1      # the fine-tuned model
"""

import argparse
import json
import sys
from pathlib import Path

from kitchen.orders.laya_parser import FakeLayaParser, LocalLayaParser

DEMO_NOTES = Path(__file__).with_name("demo_notes.json")
SURE = 0.5  # a listed allergen at or above this counts as the parser saying "yes"


def check_note(parser, item):
    """Pass if every expected allergen is listed with the right severity (or a severity question) and nothing else
    is listed with confidence."""
    result = parser.parse_note(item["note"], source=item["source"])
    found = {c["allergen_unit_id"]: c for c in result["constraints"]}
    asked_severity = {
        result["constraints"][q["target"]["index"]]["allergen_unit_id"]
        for q in result["questions"]
        if q["target"]["kind"] == "constraint" and q["target"]["field"] == "severity"
    }

    missed = [u for u in item["forbidden"] if u not in found]  # the parser drops anything below 0.15
    extra = [u for u, c in found.items() if u not in item["forbidden"] and c["confidence"]["allergen_unit_id"] >= SURE]
    wrong_severity = [
        u
        for u in item["forbidden"]
        if u in found and found[u]["severity"] != item["severity"] and u not in asked_severity
    ]
    ok = not missed and not extra and not wrong_severity
    summary = {u: (c["confidence"]["allergen_unit_id"], c["severity"]) for u, c in found.items()}
    return ok, f"found {summary}, missed {missed}, extra {extra}, wrong severity {wrong_severity}"


def run(parser):
    """Check every demo note; return the number of failures."""
    notes = json.loads(DEMO_NOTES.read_text())["notes"]
    failures = 0
    for item in notes:
        ok, detail = check_note(parser, item)
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {item['id']:16} {detail}")
    print(f"{failures} failure(s) out of {len(notes)}")
    return failures


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arg_parser.add_argument("--model", help="folder with the fine-tuned Laya checkpoint (default: the keyword fake)")
    args = arg_parser.parse_args()
    parser = LocalLayaParser(args.model) if args.model else FakeLayaParser()
    sys.exit(1 if run(parser) else 0)


if __name__ == "__main__":
    main()
