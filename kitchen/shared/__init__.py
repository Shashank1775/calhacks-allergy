"""Shared building blocks every other folder in kitchen/ imports: models, interfaces, clock, config, fakes.

Owner: platform (agents)
Step:  shared
Reads: contracts/schemas, contracts/apis.md, contracts/state-tables.md, contracts/scenarios
Emits: kitchen.shared.models, interfaces, clock, config, fakes, contract_tests, change_mask and clips
Rules: depends on nothing else in kitchen/; records are plain dicts checked with models.validate against the schemas;
       interfaces match contracts/apis.md exactly
"""
