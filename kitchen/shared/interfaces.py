"""Every interface in contracts/apis.md as a typing.Protocol, plus the exceptions they raise.

Owner: platform (agents)
Step:  shared
Reads: contracts/apis.md, kitchen.shared.models
Emits: StateClient, EntityStore, ProvenanceGraph, InteractionClassifier, LayaParser, Transcriber,
       Speaker, GateDriver, Clock; StateUnavailable, GraphUnavailable, LayaParserUnavailable
Rules: signatures match contracts/apis.md exactly (a test checks method names); no implementations here;
       a missing method is a contracts PR, not a local addition
Tasks: platform-03

Not built yet.
"""
