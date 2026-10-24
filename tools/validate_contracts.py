#!/usr/bin/env python3
"""Validate everything under contracts/.

Checks:
  - every schema is valid JSON Schema (draft 2020-12)
  - fixtures/valid/<schema>/*.json validate, fixtures/invalid/<schema>/*.json do not
  - scenarios/*.json validate against scenario.schema.json and are internally consistent
  - every allergen unit ID used anywhere exists in allergen_units.json

Usage: uv run python tools/validate_contracts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    sys.exit("missing dependency: run `uv sync`")

ROOT = Path(__file__).resolve().parent.parent / "contracts"
errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        fail(f"{path.relative_to(ROOT)}: invalid JSON: {e}")
        return None


def strip_comment(doc):
    return {k: v for k, v in doc.items() if k != "$comment"} if isinstance(doc, dict) else doc


def collect_unit_ids(node, found: set[str]) -> None:
    """Every value under a key that names an allergen unit, plus probability-map keys."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "allergen_unit_id" and isinstance(v, str):
                found.add(v)
            elif k in ("forbidden", "allergens_reached", "allergen_unit_ids") and isinstance(v, list):
                found.update(x for x in v if isinstance(x, str))
            elif k == "probabilities" and isinstance(v, dict):
                found.update(v.keys())
            collect_unit_ids(v, found)
    elif isinstance(node, list):
        for item in node:
            collect_unit_ids(item, found)


def main() -> int:
    # schemas
    schemas: dict[str, dict] = {}
    for path in sorted((ROOT / "schemas").glob("*.schema.json")):
        doc = load(path)
        if doc is None:
            continue
        try:
            Draft202012Validator.check_schema(doc)
        except Exception as e:  # noqa: BLE001
            fail(f"schemas/{path.name}: not a valid schema: {e}")
            continue
        if doc.get("$id") != path.name:
            fail(f"schemas/{path.name}: $id must equal the file name")
        schemas[path.name.removesuffix(".schema.json")] = doc

    registry = Registry().with_resources(
        (f"{name}.schema.json", Resource.from_contents(doc)) for name, doc in schemas.items()
    )

    def validator(name: str) -> Draft202012Validator:
        return Draft202012Validator(schemas[name], registry=registry)

    # allergen units
    units_doc = load(ROOT / "allergen_units.json") or {"units": []}
    unit_ids = [u["id"] for u in units_doc["units"]]
    if len(unit_ids) != len(set(unit_ids)):
        fail("allergen_units.json: duplicate unit IDs")
    known_units = set(unit_ids)
    used_units: set[str] = set()

    # fixtures
    n_fixtures = 0
    for expect_valid in (True, False):
        base = ROOT / "fixtures" / ("valid" if expect_valid else "invalid")
        for path in sorted(base.glob("*/*.json")):
            rel = path.relative_to(ROOT)
            schema_name = path.parent.name
            if schema_name not in schemas:
                fail(f"{rel}: no schema named {schema_name}")
                continue
            doc = load(path)
            if doc is None:
                continue
            n_fixtures += 1
            errs = list(validator(schema_name).iter_errors(strip_comment(doc)))
            if expect_valid:
                collect_unit_ids(doc, used_units)
                for e in errs:
                    fail(f"{rel}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}")
            elif not errs:
                fail(f"{rel}: expected to be rejected by {schema_name}, but it validated")

    # scenarios
    scenario_paths = sorted((ROOT / "scenarios").glob("*.json"))
    for path in scenario_paths:
        rel = path.relative_to(ROOT)
        doc = load(path)
        if doc is None:
            continue
        for e in validator("scenario").iter_errors(doc):
            fail(f"{rel}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}")
        if doc.get("scenario_id") != path.stem:
            fail(f"{rel}: scenario_id must equal the file name")
        collect_unit_ids(doc, used_units)

        event_ids = [e.get("event_id") for e in doc.get("events", [])]
        if len(event_ids) != len(set(event_ids)):
            fail(f"{rel}: duplicate event_id")
        for e in doc.get("events", []):
            if e.get("t_end_ms", 0) < e.get("t_start_ms", 0):
                fail(f"{rel}: event {e.get('event_id')} ends before it starts")
            if e.get("action") == "clean.wash" and e.get("a_id") != e.get("b_id"):
                fail(f"{rel}: wash event {e.get('event_id')} must be a self-loop (a_id == b_id)")
        tickets = {t["ticket_id"] for t in doc.get("tickets", [])}
        bound = {b["plate_id"]: b["ticket_id"] for b in doc.get("plate_bindings", [])}
        for p in doc.get("plates_at_pass", []):
            if p["ticket_id"] not in tickets:
                fail(f"{rel}: plate {p['plate_id']} at pass for unknown ticket {p['ticket_id']}")
            if bound.get(p["plate_id"]) != p["ticket_id"]:
                fail(f"{rel}: plate {p['plate_id']} reaches the pass without a binding to {p['ticket_id']}")
        for x in doc.get("expected", []):
            for eid in x.get("chain_event_ids", []):
                if eid not in event_ids:
                    fail(f"{rel}: expected chain references unknown event {eid}")
            if x.get("decision") == "pass" and x.get("reason_codes"):
                fail(f"{rel}: expected pass for {x['plate_id']} cannot have reason codes")

    for unit in sorted(used_units - known_units):
        fail(f"allergen unit {unit!r} is used but not defined in allergen_units.json")

    if errors:
        print(f"contracts INVALID ({len(errors)} problem(s)):")
        for msg in errors:
            print(f"  - {msg}")
        return 1
    print(
        f"contracts OK: {len(schemas)} schemas, {n_fixtures} fixtures, "
        f"{len(scenario_paths)} scenarios, {len(known_units)} allergen units"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
