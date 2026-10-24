"""Tests for kitchen/shared/models.py: every contract fixture against validate(), and Action against the schema.

Owner: platform (agents)
Step:  shared
Reads: contracts/fixtures/valid, contracts/fixtures/invalid, contracts/schemas/common.schema.json
Emits: nothing
Rules: every invalid fixture guards a safety rule (contracts/README.md), so those tests are marked safety; the blind
       classifier ones (allergen fields, object classes) are the ones the classifier depends on
"""

import json
import typing

import jsonschema
import pytest

from kitchen.shared.models import CONTRACTS_DIR, SCHEMAS_DIR, Action, validate

VALID = sorted((CONTRACTS_DIR / "fixtures" / "valid").glob("*/*.json"))
INVALID = sorted((CONTRACTS_DIR / "fixtures" / "invalid").glob("*/*.json"))


def fixture_id(path):
    """`schema/name` for a fixture path, so a failing test names the fixture."""
    return f"{path.parent.name}/{path.stem}"


def without_comment(record):
    """The fixture minus its "$comment", which explains why it must fail but isn't part of the record."""
    return {key: value for key, value in record.items() if key != "$comment"}


@pytest.mark.parametrize("path", VALID, ids=fixture_id)
def test_every_valid_fixture_validates(path):
    record = json.loads(path.read_text())
    assert validate(record, path.parent.name) is record


@pytest.mark.safety
@pytest.mark.parametrize("path", INVALID, ids=fixture_id)
def test_every_invalid_fixture_is_rejected(path):
    """Includes the blind-classifier fixtures: features with an allergen field and clips with an object class."""
    record = without_comment(json.loads(path.read_text()))
    with pytest.raises(jsonschema.ValidationError):
        validate(record, path.parent.name)


def test_the_blind_classifier_fixtures_are_among_the_invalid_ones():
    """Guards the safety tests above against a fixture being renamed out from under them."""
    names = {fixture_id(path) for path in INVALID}
    assert {
        "interaction_features/has-allergen-field",
        "interaction_clip/has-object-class",
        "interaction_clip/label-has-allergen",
    } <= names


def test_validate_resolves_cross_file_refs():
    """A verdict's chain points at chain_step.schema.json, whose action points at common.schema.json."""
    verdict = json.loads((CONTRACTS_DIR / "fixtures" / "valid" / "verdict" / "hold-peanut.json").read_text())
    verdict["chain"][0]["action"] = "transfer.teleport"
    with pytest.raises(jsonschema.ValidationError):
        validate(verdict, "verdict")


def test_validate_refuses_an_unknown_schema_name():
    with pytest.raises(ValueError, match="no schema named"):
        validate({}, "laya_request")


def test_action_matches_the_schema_enum():
    common = json.loads((SCHEMAS_DIR / "common.schema.json").read_text())
    assert list(typing.get_args(Action)) == common["$defs"]["action"]["enum"]
