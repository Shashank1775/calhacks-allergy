"""Bin allergen profiles for the demo, hard-coded. Edit DEMO_BINS to match the bins on the counter.

Owner: orders (agents)
Step:  orders
Reads: DEMO_BINS below; contracts/allergen_units.json (the allergen unit IDs)
Emits: one AllergenProfile per bin via StateClient.put_bin_profile (bin_profile table), with source "manual"
Rules: allergens, not contacts: allergen unit IDs only, checked at startup so a typo fails loudly;
       a bin you aren't sure about gets "unknown": True, which counts as carrying every allergen
"""

import json
import re
from pathlib import Path

ALLERGEN_UNITS_FILE = Path(__file__).resolve().parents[2] / "contracts" / "allergen_units.json"

# Bin entity IDs come from vision: bin-4 is whatever sits in taped slot 4.
# "allergens" maps an allergen unit ID to how sure we are the bin contains it; leave a unit out for "none".
DEMO_BINS = {
    "bin-1": {"label": "satay sauce", "allergens": {"peanut": 0.98, "soy": 0.9}},
    "bin-2": {"label": "butter", "allergens": {"milk": 0.99}},
    "bin-3": {"label": "steamed rice", "allergens": {}},
    "bin-4": {"label": "shrimp", "allergens": {"shellfish": 0.99}},
    "bin-5": {"label": "hummus", "allergens": {"sesame": 0.97}},
}


def allergen_unit_ids():
    """Every allergen unit ID from contracts/allergen_units.json, the source of truth.

    (laya_parser.UNITS is fixed by training; a test checks it matches this list.)
    """
    return [unit["id"] for unit in json.loads(ALLERGEN_UNITS_FILE.read_text())["units"]]


def check_bins(bins):
    """Raise ValueError on a bad bin ID, an unknown allergen unit or a probability outside 0..1."""
    units = set(allergen_unit_ids())
    for entity_id, info in bins.items():
        if not re.fullmatch(r"bin-[a-z0-9-]+", entity_id):
            raise ValueError(f"{entity_id}: bin IDs look like bin-4")
        for unit, p in info["allergens"].items():
            if unit not in units:
                raise ValueError(f"{entity_id}: {unit!r} is not an allergen unit; use one of {sorted(units)}")
            if not 0.0 <= p <= 1.0:
                raise ValueError(f"{entity_id}: {unit} probability {p} is outside 0..1")


def bin_profiles(bins=DEMO_BINS):
    """Each bin's AllergenProfile (contracts/schemas/allergen_profile.schema.json), keyed by entity ID."""
    check_bins(bins)
    return {
        entity_id: {
            "probabilities": dict(info["allergens"]),
            "unknown": info.get("unknown", False),
            "source": "manual",
        }
        for entity_id, info in bins.items()
    }


def save_bins(state, bins=DEMO_BINS):
    """Write every bin's profile. Run at shift start: each shift starts with an empty database."""
    profiles = bin_profiles(bins)
    for entity_id, profile in profiles.items():
        state.put_bin_profile(entity_id, profile)
    return profiles
