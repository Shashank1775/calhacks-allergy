# kitchen/verdict: Final analysis (pass or hold)

**Owner:** team `verdict` (agents). This is the safety-critical agent team.

**Pipeline step:** when a plate reaches the pass, or its ticket is amended, decide PASS or HOLD in plain code and explain the chain.

## What happens here

- A bound plate enters the `pass` zone, or its ticket's revision goes up. The service wakes up.
- It asks the partner's graph for exact reachability (`reach`), the plate's unknown-history ancestors (`unknown_ancestors`), and, in `full` mode, the subgraph to send to Laya (`subgraph`).
- `decide()` combines all of that with the ticket: **HOLD** if exact or Laya flags a forbidden allergen, if an unknown ancestor exists, if clean-confidence is below `ticket.pass_threshold`, or if anything failed.
- The verdict and its chain are written atomically (`record_verdict`), so the gate, voice and expo board can react.
- Later (M4), held chains are grouped into Repeated Mistakes with a suggested fix.

## Files

| File | What it does |
|---|---|
| `policy.py` | `decide()`: the one plain-code function that returns PASS or HOLD with every reason. Pure. Also `VerdictSettings`, the single home for thresholds, timeouts and mode |
| `confidence.py` | Clean-confidence from reachability and input health, as defined by [ADR 0010](../../docs/decisions/0010-clean-confidence-and-flag-thresholds.md). Pure and monotone |
| `laya.py` | `LayaClient` adapter (timeout, schema validation, malformed → unavailable), plus the `ExactLaya` and `AdversarialLaya` fakes |
| `service.py` | Watches `StateClient`, triggers on plate-at-pass and amendments, records verdict and chain. Also `ScriptedVerdictSource`, the fake that `outputs` builds against |
| `mistakes.py` | Repeated Mistakes: groups held chains by source, tool kind and allergen, and writes `mistake_pattern` with a suggested fix |

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Ticket revision: forbidden units, severity, trace sensitivity, `pass_threshold`, exceptions | `ticket` | orders, via `StateClient` |
| In | Plate binding and plate zone (`pass` triggers a verdict) | `plate_binding`, `entity` | vision and the partner entity store, via `StateClient` |
| In | Exact reachability with chains | `allergen_reach` via `ProvenanceGraph.reach` | partner graph |
| In | Unknown-history ancestors | `ProvenanceGraph.unknown_ancestors` | partner graph |
| In | Plate subgraph for Laya | `laya_request` via `ProvenanceGraph.subgraph` | partner graph |
| In | Laya's answer | `laya_response` via `LayaClient` | Laya model (trained by sim) |
| Out | Verdict and chain (`verdict`, `verdict_step` tables) | `verdict`, `chain_step` | outputs (gate, voice, expo board) |
| Out | Recurring contamination paths | `mistake_pattern` | outputs (Repeated Mistakes view) |
| Out | `ExactLaya`, `AdversarialLaya` | `LayaClient` | sim's eval harness |

## Safety rules that apply

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`; properties use `hypothesis`.

| Invariant | How we could break it | Guarding test |
|---|---|---|
| 1. Fail closed | An exception, timeout, `StateUnavailable`, `GraphUnavailable`, `LayaUnavailable`, missing ticket or binding, or a stale plate reaches PASS | Fault injection at every `StateClient`, `ProvenanceGraph` and `LayaClient` call site: the result is always HOLD with `error`, `missing_input`, `stale_track` or `model_unavailable` |
| 2. Models only tighten | Laya removes a flag or lifts confidence | For any inputs and any Laya outcome (random, empty, late, malformed, "clean", raises): `decide(full)` holds whenever `decide(rules_only)` holds, and `flagged` ⊇ exact flags |
| 4. Allergens, not contacts | Policy reads entity-level bans, or an exception suppresses a flag | Policy reads only allergen units. `ticket.exceptions` never suppress a flag (OQ-2 default) |
| 8. Unknown history | An unknown ancestor is ignored | Non-empty `unknown_ancestors` → HOLD `unknown_history`. Golden scenarios `unknown-tool-mid-shift` and `clean-plate-from-stack` |
| 9. Late changes | An amendment reuses the previous verdict | Every verdict is computed from scratch from full history. Golden scenario `late-amendment-adds-dairy` |

Invariant 5 (time-forward) belongs to the partner's graph. We check it end to end by running the golden scenarios against the real graph (verdict-10, `needs-partner`). Until the OQ-8 ADR lands, `trace_sensitive: false` is treated as `true`. Same inputs must produce byte-identical verdict JSON.

## Fakes

- **We use:** `FakeProvenanceGraph` (answers from scenario ground truth), `InMemoryStateClient` and `FixedClock`, all from `kitchen/shared/fakes/` (platform).
- **We ship:** `ExactLaya` (wraps `ProvenanceGraph.reach`), `AdversarialLaya` (random, empty, late, malformed, flipped, raises), and `ScriptedVerdictSource` (replays fixture verdicts and mistake patterns into `InMemoryStateClient` on a `Clock`).

## Tests

- Live in `kitchen/verdict/tests/`. Run `uv run pytest kitchen/verdict`; the safety suite alone is `uv run pytest kitchen/verdict -m safety`.
- The golden runner is parametrized over `contracts/scenarios/*.json`, so a new scenario needs no code change.
- Every PR touching `policy.py`, `confidence.py`, `laya.py` or `service.py` is `safety-critical` and needs `review:safety-approved`. `policy.py` is held by one Worker at a time.

## Milestones

- **M0:** `decide()`; clean-confidence ADR; Laya adapter with `ExactLaya` and `AdversarialLaya`; golden scenarios pass through the policy in CI; `ScriptedVerdictSource` for outputs.
- **M1:** verdict service on `StateClient`; safety suite with fault injection signed off; sim corpus gate with zero false passes; runs against the partner's real graph and database once they land (`needs-partner`).
- **M2:** live edges; staleness (plate updates, edge lag) → HOLD; rules-only latency ≤ 200 ms; deterministic replay of a recorded session. No hardware work.
- **M3:** `full` mode with the real Laya, timeout → `model_unavailable`; ≤ 1 s budget; `trace_sensitive` ADR (OQ-8).
- **M4:** Repeated Mistakes with suggested fixes; live late-change recheck; failure drills (database, graph, Laya, stale feed), each ending in HOLD.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Thresholds unknown (OQ-4) | OQ-4 defaults in `VerdictSettings` as per-severity floors a ticket can tighten but never loosen, recorded on every verdict ([ADR 0010](../../docs/decisions/0010-clean-confidence-and-flag-thresholds.md)); the owner sets the real values |
| Clean-confidence ill-defined | [ADR 0010](../../docs/decisions/0010-clean-confidence-and-flag-thresholds.md): `max(0, 1 - sum of forbidden p)`, zero on any input-health failure (an unknown unit ID included), and a unit flags when `1 - p_u <= effective_threshold`. Monotone: more evidence of a forbidden allergen never raises it |
| `trace_sensitive: false` semantics (OQ-8) | Strict until the ADR lands |
| Exceptions like "coconut is fine" (OQ-2) | Shown to the expo, never used to suppress a flag |
| The partner's graph isn't ready | `FakeProvenanceGraph` tests the policy only; real-graph runs carry `needs-partner` |
| What backs Laya (OQ-5); Laya slow in `full` mode | Everything HOLDs when Laya is unavailable, which is correct. Switching to `rules_only` is a recorded human action |
| A false wash erases real contamination | The graph caps wash efficacy (partner). We track wash-then-hold patterns in Repeated Mistakes |
