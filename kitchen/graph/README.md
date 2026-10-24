# kitchen/graph: Provenance graph (partner)

**Owner:** the partner (human). The first version was written by an agent at the owner's request (2026-10-24), on `partner/provenance-graph`; the partner reviews it and owns it from here. This note says what the rest of the system expects from it and how it's built.

## What agents expect

The interface is [`ProvenanceGraph` in contracts/apis.md](../../contracts/apis.md#provenancegraph): `reach`, `subgraph`, `unknown_ancestors`, `version`. It raises `GraphUnavailable` on any failure. The graph builds itself from `StateClient` (`entity`, `bin_profile`, `plate_binding`, `interaction`), and writes each entity's live allergens as `entity_state` rows (`move_entity`) for the camera overlays.

The graph math the agents assume (from the original spec):

- **Noisy-OR along transfer edges.** For allergen *a* over an edge with probability *p* and action efficiency *k*: `P_dst(a) ← 1 − (1 − P_dst(a)) · (1 − k · p · P_src(a))`. Honor `direction` (`a_to_b`, `b_to_a`, `both`).
- **Wash reset.** A `clean.wash` edge is a self-loop (`a_id == b_id`) and sets `P(a) ← (1 − p) · P(a)` for every unit.
- **Time forward.** Process edges in time order, so every path is time-forward (invariant 5). Ties default to `t_start_ms`, then `t_end_ms`, then `event_id`, and a wash tied with a transfer is applied **before** it, so a tie can never erase contamination (OQ-17, yours to decide).
- **Start states (invariant 8).** Entities with `origin: shift_start` or `clean_stack` start clean. `origin: unknown` starts at probability 1 for every unit until a wash (OQ-13 default; yours to decide).

## What agents will check

These live in `kitchen/shared/contract_tests/` (written by platform). Your graph should pass the same suite the fake passes:

- `reach` results validate against `allergen_reach`; chains run source → plate with strictly increasing `t_ms`.
- `subgraph` validates against `plate_subgraph` and contains only time-forward ancestors.
- Shuffling the input order of edges never changes the result. An edge into a source after that source's outgoing edge never contaminates.
- `unknown_ancestors` lists every unwashed `origin: unknown` ancestor, and nothing else.
- Any internal failure raises `GraphUnavailable`; it never returns an empty "clean" answer.
- All six golden scenarios in [`contracts/scenarios/`](../../contracts/scenarios/) give the expected `allergens_reached` and chains for any `k` in (0, 1].
- Sim's long-shift scenario (≈ 5,000 edges, ≥ 100 plates) for performance.

## How it's built

- `graph.py`: `ProvenanceGraph(state)` over any `StateClient`. Every query reads the shift again (entities, then edges up to `as_of_t_ms`, `unimportant` dropped, sorted with the tie rule above), so there's no cache to go stale. About 90 ms per verdict at 5,000 edges.
- `propagate.py`: the noisy-OR above, with `k = 1` for every action (the most cautious value, and what Laya's exact plate check uses). A `both` edge moves both ways at once, from the state before it.
- `reachability.py`: `reach` (every allergen above 0, with its strongest chain from a bin), `unknown_ancestors`, and `subgraph` (the edges on time-forward paths into the plate, plus a node for each entity they touch).
- **Chains.** A chain starts at a bin. A wash ends the washed entity's chains, since what's left is residue. Allergens from residue or unknown history get an empty chain; their probability still counts, and `unknown_ancestors` names the unknown-history entities.
- **Fail closed.** Any error, or a plate the database doesn't hold, raises `GraphUnavailable`. An edge naming an entity the database doesn't hold treats that entity as unknown history.
- **Not built yet:** `risk.py` (live `entity_state` rows for the overlays).

Tests: `uv run pytest kitchen/graph` (the shared contract suite plus the graph's own), `VERDICT_GOLDEN_GRAPH=real uv run pytest kitchen/verdict`, and `uv run pytest tests/e2e --graph real`.

## Handing off

- Open PRs from branches named `partner/<slug>`. CI lets those branches edit partner-owned folders.
- Any internal layout is fine. The skeleton files here are only suggestions.
- The interface in `contracts/apis.md` is the deal. If you need to change it, tell the Director (an RFC issue), because verdict, outputs and the fakes depend on it.

## What agents build around it

- **verdict:** the policy that calls `reach`, `unknown_ancestors` and `subgraph`, and `ExactLaya`, which wraps `reach`.
- **sim:** corpora whose truth is independent of your code, so they can test it.
- **outputs:** overlays colored from each entity's latest `entity_state` allergens.
- **platform:** the fake, the contract tests, and the end-to-end harness.

Open questions that touch you: OQ-9 (edge direction; interactions is yours too), OQ-13, OQ-17. See [AGENTS.md §11](../../AGENTS.md#11-open-questions).
