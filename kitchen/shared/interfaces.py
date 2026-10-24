"""Every interface in contracts/apis.md as a typing.Protocol, plus the exceptions they raise.

Owner: platform (agents)
Step:  shared
Reads: contracts/apis.md, kitchen.shared.models
Emits: StateClient, EntityStore, ProvenanceGraph, PairWindow, InteractionClassifier, LayaParser, Transcriber,
       Speaker, GateDriver, Clock; StateUnavailable, GraphUnavailable, LayaParserUnavailable
Rules: signatures match contracts/apis.md exactly (a test checks method and parameter names); no implementations here;
       a missing method is a contracts PR, not a local addition
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from kitchen.shared.models import (
    Action,
    AllergenProfile,
    AllergenReach,
    ChangeEvent,
    Entity,
    EntityState,
    InteractionClip,
    InteractionEvent,
    InteractionFeatures,
    ObservationFrame,
    ParseResult,
    PlateBinding,
    Ticket,
    Verdict,
    Zone,
)

if TYPE_CHECKING:
    # Only for the PairWindow annotation: numpy isn't a dependency of kitchen.shared itself.
    import numpy as np


# The names below are fixed by contracts/apis.md, so they skip ruff's "end exception names in Error" rule.


class StateUnavailable(Exception):  # noqa: N818
    """The database can't be reached. On the verdict path this becomes HOLD (safety rule: fail closed)."""


class GraphUnavailable(Exception):  # noqa: N818
    """The provenance graph failed. The verdict policy turns this into HOLD (safety rule: fail closed)."""


class LayaParserUnavailable(Exception):  # noqa: N818
    """Laya couldn't parse a note (an empty note too). The Orders API answers 503 laya_unavailable; nothing guesses."""


class StateClient(Protocol):
    """The only way Python talks to SpacetimeDB. Every method raises StateUnavailable when it can't be reached.

    Each write has exactly one calling team (contracts/state-tables.md, Writers) and is idempotent by primary key.
    """

    # writes
    def put_ticket(self, ticket: Ticket) -> None: ...

    def put_bin_profile(self, entity_id: str, profile: AllergenProfile) -> None: ...

    def set_zones(self, zones: list[Zone]) -> None: ...

    def bind_plate(self, plate_id: str, ticket_id: str, t_ms: int) -> None: ...

    def register_entity(self, entity: Entity) -> None: ...

    def move_entity(self, state: EntityState) -> None: ...

    def record_interaction(self, event: InteractionEvent) -> None: ...

    def record_verdict(self, verdict: Verdict) -> None: ...

    # reads
    def get_ticket(self, ticket_id: str) -> Ticket | None: ...

    def get_entity(self, entity_id: str) -> Entity | None: ...

    def list_entities(self, kind: str | None = None) -> list[Entity]: ...

    def list_zones(self) -> list[Zone]: ...

    def get_plate_binding(self, plate_id: str) -> PlateBinding | None: ...

    def list_interactions(self, since_t_ms: int = 0, until_t_ms: int | None = None) -> list[InteractionEvent]: ...

    def list_verdicts(self, ticket_id: str | None = None) -> list[Verdict]: ...

    # change feed
    def subscribe(self, table: str, on_change: Callable[[ChangeEvent], None]) -> Callable[[], None]: ...


class EntityStore(Protocol):
    """The Pi's in-memory view of every entity seen this shift, synced to SpacetimeDB about 5 times a second.

    Never deletes an entity and never changes first_seen_t_ms or origin (safety rule: known-clean source).
    """

    def ingest(self, frame: ObservationFrame) -> None: ...

    def get(self, entity_id: str) -> Entity | None: ...

    def snapshot(self) -> list[Entity]: ...

    def start_sync(self, state: StateClient, hz: float = 5.0) -> None: ...

    def stop_sync(self) -> None: ...

    def last_sync_t_ms(self) -> int | None: ...


class ProvenanceGraph(Protocol):
    """The partner's provenance graph, queried for one plate. Raises GraphUnavailable on any failure (fail closed)."""

    def reach(self, plate_id: str, as_of_t_ms: int) -> list[AllergenReach]: ...

    def unknown_ancestors(self, plate_id: str, as_of_t_ms: int) -> list[str]: ...

    def version(self) -> str: ...


@dataclass(frozen=True)
class PairWindow:
    """One candidate pair over time: what an InteractionClassifier sees, and nothing else (safety rule: blind classifier).

    `features` is what zone rules use. `clip` and `tensor` are what kitchen/shared/clips.build_clip returns: an
    interaction_clip record and its uint8 [T, C, H, W] tensor, which is what the ConvLSTM uses.
    """

    a_id: str
    b_id: str
    features: Sequence[InteractionFeatures]
    clip: InteractionClip
    tensor: np.ndarray


class InteractionClassifier(Protocol):
    """Labels one pair window with actions and probabilities: zone rules (always on) or the partner's ConvLSTM."""

    name: str  # "zone_rules" | "lstm"
    version: str

    def classify(self, window: PairWindow) -> list[tuple[Action, float]]: ...


class LayaParser(Protocol):
    """Turns a ticket note into a ParseResult. Raises LayaParserUnavailable rather than guess."""

    def parse_note(self, note: str, *, source: Literal["typed", "voice"]) -> ParseResult: ...


class Transcriber(Protocol):
    """Speech to text for spoken ticket notes (Cartesia Ink)."""

    def transcribe(self, audio: bytes, *, sample_rate_hz: int) -> str: ...


class Speaker(Protocol):
    """Text to speech at the pass (Cartesia Sonic). Non-blocking; drops stale lines rather than queueing them."""

    def say(self, text: str) -> None: ...


class GateDriver(Protocol):
    """The pass gate: arm, red/green light and buzzer. `fault` holds the arm closed (safety rule: fail closed)."""

    def show(self, state: Literal["idle", "pass", "hold", "fault"], ticket_number: int | None = None) -> None: ...


class Clock(Protocol):
    """t_ms since shift start. Logic takes a Clock instead of reading the wall clock, so replays are deterministic."""

    def now_ms(self) -> int: ...
