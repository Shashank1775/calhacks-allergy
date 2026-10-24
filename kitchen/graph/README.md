# kitchen/graph: Provenance graph (partner)

**Owner:** the partner (human). Agents never edit this folder. This note tells you what the agents expect from it and what they're building around it.

## What agents expect

The interface is [`ProvenanceGraph` in contracts/apis.md](../../contracts/apis.md#provenancegraph): `reach`, `subgraph`, `unknown_ancestors`, `version`. It raises `GraphUnavailable` on any failure. The graph builds itself from `StateClient` (`entity`, `bin_profile`, `plate_binding`, `interaction`), and is the single writer of `entity_risk` (`put_entity_risk`) for the camera overlays.

The graph math the agents assume (from the original spec):

- **Noisy-OR along transfer edges.** For allergen *a* over an edge with probability *p* and action efficiency *k*: `P_dst(a) ← 1 − (1 − P_dst(a)) · (1 − k · p · P_src(a))`. Honor `direction` (`a_to_b`, `b_to_a`, `both`).
- **Wash reset.** A `clean.wash` edge is a self-loop (`a_id == b_id`) and sets `P(a) ← (1 − p) · P(a)` for every unit.
- **Time forward.** Process edges in time order, so every path is time-forward (invariant 5). Ties default to `t_start_ms`, then `t_end_ms`, then `event_id`, and a wash tied with a transfer is applied **before** it, so a tie can never erase contamination (OQ-17, yours to decide).
- **Start states (invariant 8).** Entities with `origin: shift_start` or `clean_stack` start clean. `origin: unknown` starts at probability 1 for every unit until a wash (OQ-13 default; yours to decide).

## What agents will check

These live in `kitchen/shared/contract_tests/` (written by platform). Your graph should pass the same suite the fake passes:

- `reach` results validate against `allergen_reach`; chains run source → plate with strictly increasing `t_ms`.
- `subgraph` validates against `laya_request` and contains only time-forward ancestors.
- Shuffling the input order of edges never changes the result. An edge into a source after that source's outgoing edge never contaminates.
- `unknown_ancestors` lists every unwashed `origin: unknown` ancestor, and nothing else.
- Any internal failure raises `GraphUnavailable`; it never returns an empty "clean" answer.
- All six golden scenarios in [`contracts/scenarios/`](../../contracts/scenarios/) give the expected `allergens_reached` and chains for any `k` in (0, 1].
- Sim's long-shift scenario (≈ 5,000 edges, ≥ 100 plates) for performance.

## Until it lands

Agents build against `FakeProvenanceGraph` (platform, `kitchen/shared/fakes/`), which answers from a scenario's ground truth and does no graph math. Verdict's tasks that need your real graph carry the `needs-partner` label.

## Handing off

- Open PRs from branches named `partner/<slug>`. CI lets those branches edit partner-owned folders.
- Any internal layout is fine. The skeleton files here are only suggestions.
- The interface in `contracts/apis.md` is the deal. If you need to change it, tell the Director (an RFC issue), because verdict, outputs and the fakes depend on it.

## What agents build around it

- **verdict:** the policy that calls `reach`, `unknown_ancestors` and `subgraph`, and `ExactLaya`, which wraps `reach`.
- **sim:** corpora whose truth is independent of your code, so they can test it.
- **outputs:** overlays colored from `entity_risk`.
- **platform:** the fake, the contract tests, and the end-to-end harness.

Open questions that touch you: OQ-9 (edge direction, with interactions), OQ-13, OQ-17. See [AGENTS.md §11](../../AGENTS.md#11-open-questions).
