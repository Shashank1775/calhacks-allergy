"""GENERATED from contracts/schemas by tools/gen_models.py. Never edit by hand; change the schema (RFC) and regenerate.

Owner: platform (agents)
Step:  shared
Reads: contracts/schemas/*.schema.json
Emits: Pydantic v2 models (extra="forbid") for every schema: Ticket, ParseResult, AllergenProfile, Entity,
       ObservationFrame, InteractionFeatures, InteractionEvent, AllergenReach, ChainStep, LayaRequest,
       LayaResponse, Verdict, MistakePattern, Scenario; plus the table dataclasses Zone, PlateBinding,
       ChefConfirmation and ChangeEvent (contracts/state-tables.md)
Rules: every valid fixture parses and every invalid fixture is rejected (invariant 6: no allergen fields in
       InteractionFeatures); CI fails if this file differs from a fresh regeneration
Tasks: platform-02

Not built yet.
"""
