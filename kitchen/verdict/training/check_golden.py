"""Run a plate-check model over the golden scenarios and say whether it flags exactly the expected allergens.

Owner: verdict (agents)
Step:  verdict (checking the plate-check Laya model before a demo)
Reads: contracts/scenarios/*.json; a plate-check checkpoint (or the exact fake without --model)
Emits: a PASS/FAIL line per expected verdict, and a non-zero exit code if any failed
Rules: flags use the verdict's own rule (laya_hold_reasons: P >= 1 - pass_threshold)

    uv run --with laya==0.4.14 python -m kitchen.verdict.training.check_golden --model data/models/laya-plate-v2
"""

import argparse
import sys

from kitchen.verdict.laya_check import FakeLayaCheck, LocalLayaCheck, laya_hold_reasons
from kitchen.verdict.training.plate_data import golden_rows


def run(check):
    """Check every golden row; return the number of failures."""
    failures = 0
    for row in golden_rows():
        reached = check.check(row["subgraph"], row["forbidden"])
        flagged = [r["allergen_unit_id"] for r in laya_hold_reasons(reached, row["forbidden"], row["pass_threshold"])]
        expected = sorted(set(row["expected_reached"]) & set(row["forbidden"]))
        ok = flagged == expected
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {row['id']:42} P(reached) {reached}  flagged {flagged}")
    print(f"{failures} failure(s)")
    return failures


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arg_parser.add_argument("--model", help="plate-check checkpoint folder (default: the exact fake)")
    args = arg_parser.parse_args()
    check = LocalLayaCheck(args.model) if args.model else FakeLayaCheck()
    sys.exit(1 if run(check) else 0)


if __name__ == "__main__":
    main()
