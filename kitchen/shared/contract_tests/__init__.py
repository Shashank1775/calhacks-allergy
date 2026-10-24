"""Reusable pytest suites, one per interface, that every implementation (fake or real) must pass.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.interfaces, contracts/fixtures, contracts/scenarios
Emits: suites for StateClient, EntityStore, ProvenanceGraph and InteractionClassifier
Rules: a suite tests behavior promised in contracts/apis.md and nothing else; the fake and the real implementation
       run the same suite, so a disagreement is a bug in one of them

What each suite checks:
  StateClient            every method; idempotent writes by primary key; list_interactions in t_start_ms order;
                         first_seen_t_ms and origin never change; every ticket revision kept; StateUnavailable on
                         failure; subscribe() delivers inserts and updates
  EntityStore            ingest() then snapshot() reflects every frame; entities never deleted; first_seen_t_ms
                         and origin never change (known-clean source); start_sync() writes to the given StateClient
  ProvenanceGraph        reach() returns valid AllergenReach with time-forward chains (time runs forward);
                         unknown_ancestors() reports unwashed origin-unknown entities (known-clean source); every golden
                         scenario in contracts/scenarios/ gives the expected allergens; GraphUnavailable on failure
  InteractionClassifier  output actions and probabilities are valid; input is InteractionFeatures only
                         (blind classifier); name and version are set

How the partner runs a suite against real code:
  Each suite is a parametrized pytest module that takes an implementation factory as a fixture. The partner adds a
  conftest.py fixture in their own folder (kitchen/entities, kitchen/graph, kitchen/lstm or database/) that returns
  their real implementation, then runs e.g. `uv run pytest kitchen/graph -m contract`. Platform never edits partner
  folders; the partner never edits these suites (ask platform in an issue).

Not built yet.
"""
