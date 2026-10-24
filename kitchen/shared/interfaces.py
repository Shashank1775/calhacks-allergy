"""Every interface in contracts/apis.md as a typing.Protocol, plus the exceptions they raise.

Owner: platform (agents)
Step:  shared
Reads: contracts/apis.md, kitchen.shared.models
Emits: StateClient, EntityStore, ProvenanceGraph, InteractionClassifier, LayaParser, Transcriber,
       Speaker, GateDriver, Clock; StateUnavailable, GraphUnavailable, LayaParserUnavailable
Rules: signatures match contracts/apis.md exactly (a test checks method names); no implementations here;
       a missing method is a contracts PR, not a local addition

Built so far: the three exceptions. The Protocols are not written yet.
"""


# The names below are fixed by contracts/apis.md, so they skip ruff's "end exception names in Error" rule.


class StateUnavailable(Exception):  # noqa: N818
    """The database can't be reached. On the verdict path this becomes HOLD (safety rule: fail closed)."""


class GraphUnavailable(Exception):  # noqa: N818
    """The provenance graph failed. The verdict policy turns this into HOLD (safety rule: fail closed)."""


class LayaParserUnavailable(Exception):  # noqa: N818
    """Laya couldn't parse a note. The tablet falls back to manual entry; nothing guesses (safety rule: fail closed)."""
