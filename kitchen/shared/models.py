"""GENERATED from contracts/schemas by tools/gen_models.py. Never edit by hand; change the schema and regenerate.

Owner: platform (agents)
Step:  shared
Reads: contracts/schemas/*.schema.json
Emits: Pydantic v2 models (extra="forbid") for every schema: Ticket, ParseResult, AllergenProfile, Entity,
       ObservationFrame, InteractionFeatures, InteractionClip, InteractionEvent, AllergenReach, ChainStep,
       Verdict, Scenario; plus the table dataclasses Zone, PlateBinding and ChangeEvent (contracts/state-tables.md)
Rules: every valid fixture parses and every invalid fixture is rejected (blind classifier: no allergen fields in
       InteractionFeatures); CI fails if this file differs from a fresh regeneration

Not built yet.
"""
