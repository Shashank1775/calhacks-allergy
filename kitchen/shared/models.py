"""Records are plain dicts checked against contracts/schemas, plus the table dataclasses from contracts/state-tables.md.

Owner: platform (agents)
Step:  shared
Reads: contracts/schemas/*.schema.json
Emits: validate(record, schema); readable aliases for schema records (Ticket, ParseResult, AllergenProfile, Entity,
       ObservationFrame, InteractionFeatures, InteractionClip, InteractionEvent, AllergenReach, ChainStep, Verdict,
       Scenario) and Action; the table dataclasses Zone, PlateBinding, EntityState and ChangeEvent
Rules: the owner chose plain dicts over generated models (2026-10-23), so the JSON schemas are the only definition of
       a record's shape; every valid fixture passes validate() and every invalid one fails it (blind classifier: no
       allergen fields in InteractionFeatures, no object classes in InteractionClip)
"""

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

# This file is kitchen/shared/models.py, so the repo root is two folders above kitchen/shared/.
CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"

# One alias per schema, named after its title. They are all dicts; the alias says which schema the dict follows.
Ticket: TypeAlias = dict[str, Any]
ParseResult: TypeAlias = dict[str, Any]
AllergenProfile: TypeAlias = dict[str, Any]
Entity: TypeAlias = dict[str, Any]
ObservationFrame: TypeAlias = dict[str, Any]
InteractionFeatures: TypeAlias = dict[str, Any]
InteractionClip: TypeAlias = dict[str, Any]
InteractionEvent: TypeAlias = dict[str, Any]
AllergenReach: TypeAlias = dict[str, Any]
ChainStep: TypeAlias = dict[str, Any]
Verdict: TypeAlias = dict[str, Any]
Scenario: TypeAlias = dict[str, Any]

# The action enum in common.schema.json (a test keeps the two in step).
Action: TypeAlias = Literal["transfer.contact", "transfer.scoop", "transfer.wipe", "clean.wash", "unimportant"]


@functools.cache
def schema_registry() -> Registry:
    """Every schema in contracts/schemas, keyed by its $id, so cross-file $refs (common.schema.json#/...) resolve."""
    documents = [json.loads(path.read_text()) for path in sorted(SCHEMAS_DIR.glob("*.schema.json"))]
    return Registry().with_resources((document["$id"], Resource.from_contents(document)) for document in documents)


@functools.cache
def schema_validator(schema: str) -> Draft202012Validator:
    """The Draft 2020-12 validator for contracts/schemas/<schema>.schema.json, built once and reused."""
    schema_id = f"{schema}.schema.json"
    if schema_id not in schema_registry():
        raise ValueError(f"no schema named {schema!r} in {SCHEMAS_DIR}")
    return Draft202012Validator(schema_registry()[schema_id].contents, registry=schema_registry())


def validate(record: dict, schema: str) -> dict:
    """Check `record` against contracts/schemas/<schema>.schema.json and return it unchanged.

    Raises jsonschema.ValidationError on the first problem, so a malformed record fails loudly where it is made
    instead of somewhere downstream.
    """
    schema_validator(schema).validate(record)
    return record


@dataclass(frozen=True)
class Zone:
    """One row of the zone table: a calibrated region of the camera frame. Written by vision (calibration)."""

    zone_id: str
    kind: Literal["bin_slot", "plating_spot", "wash", "clean_stack", "pass", "other"]
    polygon: list[tuple[float, float]]  # normalized (x, y) points


@dataclass(frozen=True)
class PlateBinding:
    """One row of the plate_binding table: which ticket a plate was started for, and when. Written by vision."""

    plate_id: str
    ticket_id: str
    t_ms: int


@dataclass(frozen=True)
class EntityState:
    """One row of the entity_state table: where an entity was and what it may carry at t_ms.

    Rows are appended, never edited (history is append-only). The database assigns `state_id`, so writers don't
    send it. Written by the partner's entity store (zone, box) and graph (allergens).
    """

    entity_id: str
    version: int
    t_ms: int
    zone: str | None
    box: list[float] | None  # normalized [x1, y1, x2, y2]
    probabilities: dict[str, float]  # allergen unit ID -> probability
    unknown: bool
    caused_by: str | None = None  # the event_id that changed it, or None


@dataclass(frozen=True)
class ChangeEvent:
    """One change delivered by StateClient.subscribe: which table, whether a row was inserted or updated, and the row.

    The row is the table's record: a schema dict (ticket, entity, interaction, verdict, bin_profile) or one of the
    dataclasses above (zone, entity_state, plate_binding).
    """

    table: str
    kind: Literal["insert", "update"]
    row: Any
