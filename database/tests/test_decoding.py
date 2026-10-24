"""SpacetimeDB's SQL JSON as plain Python: options, products and arrays. No server needed.

Owner: partner (written by an agent at the owner's request)
Step:  database
Reads: database/state_client.py
Emits: nothing
Rules: none of the nine; a decoding slip would hand the verdict path a wrong row, so the shapes are pinned here
"""

from database.state_client import decode, quoted, some, starting_profile

OPTION_STRING = {
    "Sum": {
        "variants": [
            {"name": {"some": "some"}, "algebraic_type": {"String": []}},
            {"name": {"some": "none"}, "algebraic_type": {"Product": {"elements": []}}},
        ]
    }
}
PROBABILITY = {
    "Product": {
        "elements": [
            {"name": {"some": "allergen_unit_id"}, "algebraic_type": {"String": []}},
            {"name": {"some": "probability"}, "algebraic_type": {"F64": []}},
        ]
    }
}


def test_an_option_decodes_to_its_value_or_none():
    assert decode([0, "pass"], OPTION_STRING) == "pass"
    assert decode([1, []], OPTION_STRING) is None


def test_an_array_of_products_decodes_to_dicts_by_field_name():
    assert decode([["peanut", 0.98]], {"Array": PROBABILITY}) == [{"allergen_unit_id": "peanut", "probability": 0.98}]


def test_reducer_options_use_spacetimedbs_json():
    assert some("pass") == {"some": "pass"}
    assert some(None) == {"none": []}


def test_a_quote_in_an_id_stays_inside_the_sql_string():
    assert quoted("t7' OR '1'='1") == "'t7'' OR ''1''=''1'"


def test_only_a_known_clean_origin_starts_clean():
    """Known-clean source: an entity the graph hasn't written yet is clean only if it came from shift start or a
    clean stack; anything else, and every bin, counts as carrying every allergen."""
    assert starting_profile("tool", "shift_start")["unknown"] is False
    assert starting_profile("plate", "clean_stack")["unknown"] is False
    assert starting_profile("tool", "unknown")["unknown"] is True
    assert starting_profile("bin", "shift_start")["unknown"] is True
