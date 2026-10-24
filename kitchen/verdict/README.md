# kitchen/verdict: Pass or hold

**Owner:** team `verdict` (agents). This is the safety-critical agent team.

When a plate reaches the pass, or its ticket is amended, decide PASS or HOLD in plain code and explain the chain.

## What happens here

- A bound plate enters the `pass` zone, or its ticket gets a new revision, or an interaction edge lands that its latest verdict didn't account for. That's an edge starting at or before the verdict (interactions writes edges after classifying them), or one touching the plate while it waits at the pass. The service wakes up. The third trigger is `late_edge` (#183).
  - It uses a reach cutoff per judged plate: the later of the plate's last real judgment and the end of its last touch.
  - An edge rechecks a plate only if it touches the plate or starts by that cutoff, so rechecks stop once nothing new can reach the plate.
  - A recheck that would say the same thing as the plate's latest recorded verdict (same decision and reasons) writes nothing, so a held plate isn't recorded and spoken again on every touch.
  - Entering and leaving the pass are read only from the entity store's newest row for the plate (#216). Rows the graph wrote (`caused_by` set) copy a zone forward, possibly a stale one. Rows older by `(t_ms, version)` than one already seen were written late. Both are skipped, so neither can take a judged plate out of the pass and have it judged, and its hold spoken, twice. A plate that really leaves and comes back is judged again.
- It asks the partner's graph for exact reachability (`reach`), the plate's unknown-history ancestors (`unknown_ancestors`) and the plate's subgraph (`subgraph`).
- **Laya's final plate check** (`laya_check.py`) reads that subgraph and gives P(reached the plate) for each forbidden allergen. Each one at P ≥ 1 − `pass_threshold` adds a `laya_flagged` reason; if Laya can't answer, `laya_unavailable`. Laya can only add holds, never remove one.
- `decide()` combines that with the ticket: **HOLD** if a forbidden allergen is flagged (by the graph's numbers or by Laya), if an unknown ancestor exists, if clean-confidence is below the pass threshold, or if anything failed.
- The verdict and its chain are written in one call (`record_verdict`), so the gate, voice and expo board can react.

**Clean-confidence** is how sure we are the plate carries none of the ticket's forbidden allergens:

```
confidence = max(0, 1 - sum(p[a] for a in forbidden))   # p[a] from reach; 0 if absent
           = 0 if any input is unhealthy (error, missing input, stale plate, unknown history)
flag a forbidden allergen when 1 - p[a] <= pass_threshold
hold = not healthy or any flag or confidence < pass_threshold
```

The sum is the safe bound however the allergens depend on each other (one knife can carry peanut and soy together). The pass threshold is the ticket's `pass_threshold`, which orders sets from the strictest severity. Same inputs give byte-identical verdict JSON.

## Files

| File | What it does |
|---|---|
| `policy.py` | `decide()` and `clean_confidence()`. Pure; the pass threshold comes from the ticket |
| `service.py` | `VerdictService`: watches `StateClient`, triggers on plate-at-pass and amendments, records verdicts (retrying while the database is down). `VerdictSettings` (staleness window). Also `FakeVerdictFeed`, which outputs builds against |
| `laya_check.py` | Laya's final plate check: `LocalLayaCheck` (the fine-tuned plate model), `FakeLayaCheck` (exact noisy-OR) and `laya_hold_reasons()` |
| `training/` | `plate_data.py` (random kitchens, their plate subgraphs and the exact answers; the golden scenarios as rows), `eval_plate.py` (scores a checkpoint on pina1) and `check_golden.py` (runs a checkpoint over the golden scenarios). Training uses `kitchen/orders/training/train_laya.py` on pina1 |

## In and out

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Ticket: forbidden allergens, `pass_threshold`, revision | `ticket` | orders, via `StateClient` |
| In | Plate binding and plate zone (`pass` triggers a verdict) | `plate_binding`, `entity` | vision and the partner entity store, via `StateClient` |
| In | Exact reachability with chains | `allergen_reach` via `ProvenanceGraph.reach` | partner graph |
| In | Unknown-history ancestors | `ProvenanceGraph.unknown_ancestors` | partner graph |
| In | The plate's subgraph | [`plate_subgraph`](../../contracts/schemas/plate_subgraph.schema.json) via `ProvenanceGraph.subgraph` | partner graph |
| Uses | Laya's plate check | [`LayaPlateCheck`](../../contracts/apis.md#layaplatecheck) | the fine-tuned plate model ([decision](../../docs/decisions/laya-plate-check.md)) |
| Out | Verdict and chain (`verdict` table) | `verdict`, `chain_step` | outputs (gate, voice, expo board) |

## Running the plate check

The fine-tuned plate model lives in `data/models/laya-plate-v2` (gitignored, about 800 MB; set `LAYA_PLATE_MODEL_DIR`). It needs the `laya` package for the run:

```bash
uv run --with laya==0.4.14 python -m kitchen.verdict.training.check_golden --model data/models/laya-plate-v2
uv run python -m kitchen.verdict.training.plate_data data/laya_training/plates   # regenerate the training data
```

plate-v2 was trained on 1,700 generated kitchens, a third of them washed stories (3 epochs, about 28 minutes on one A6000). On 200 held-out kitchens it is 98.4% accurate (counted at 0.5), scores a typical clean plate at 0.0015, never misses an allergen at an anaphylactic ticket's threshold, wrongly holds 0.2% to 1.8% of clean questions depending on the threshold, and gets all six golden scenarios (seven verdicts) right. The washed-knife scenario is close: 0.049 against a 0.05 cutoff on a 0.95 ticket.

## Safety rules

See [Safety rules](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`; properties use `hypothesis`.

| Rule | How we could break it | Test |
|---|---|---|
| Fail closed | An exception, timeout, `StateUnavailable`, `GraphUnavailable`, missing ticket or binding, or a stale plate reaches PASS; or a graph-written or late row moves a plate, so it is judged twice or a real return to the pass is missed | Fault injection at every `StateClient` and `ProvenanceGraph` call: the result is always HOLD with `error`, `missing_input` or `stale_track`, and confidence 0. In `test_service.py`, three pass-entry tests: a graph-written row, or a row older by `(t_ms, version)`, never judges a plate again, and a plate that really comes back is still judged |
| Plain-code verdict | Anything but `decide()` makes the call, confidence rises with more evidence, or Laya removes a hold | `decide()` is pure; a property test checks that adding probability to a forbidden allergen never raises confidence; now: `laya_hold_reasons` only ever returns `laya_flagged` reasons for forbidden allergens, and every model failure raises `LayaCheckUnavailable`; with `decide()`: a property test that no Laya answer removes a reason and that a Laya failure holds |
| Allergens, not contacts | The policy reads anything but the ticket's forbidden allergens | The policy reads only `ticket.forbidden` and the reach probabilities |
| Known-clean source | An unknown ancestor is ignored | Non-empty `unknown_ancestors` → HOLD `unknown_history`. Golden scenarios `unknown-tool-mid-shift` and `clean-plate-from-stack` |
| Late changes recheck everything | An amendment reuses the previous verdict, or an edge written after the plate passed never reaches it | Every verdict is computed from scratch from full history. Golden scenario `late-amendment-adds-dairy`. Late-edge tests in `test_service.py`: a late edge, an edge at the pass, a refused write that's retried, rechecks in the same millisecond, no recheck once nothing can reach the plate, no repeat write when the answer hasn't changed, and a restarted service. The real graph end to end: `tests/e2e/test_late_edges.py` |

Time running forward belongs to the partner's graph; we check it end to end by running the golden scenarios against the real graph once it lands (`needs-partner`). `trace_sensitive: false` is treated as `true` for now.

## Tests

- Tests live in `kitchen/verdict/tests/`. Run `uv run pytest kitchen/verdict`; the safety suite alone is `uv run pytest kitchen/verdict -m safety`.
- They use `FakeProvenanceGraph` (answers from scenario ground truth), `FakeStateClient` and `FakeClock` (platform).
- The golden runner is parametrized over `contracts/scenarios/*.json`, so a new scenario needs no code change.
- We ship `FakeVerdictFeed`: `feed = FakeVerdictFeed(state, scenario_path); feed.play_until(clock.now_ms())` writes the scenario's expected verdicts into `state` as their times pass.
- The golden runner (`test_golden.py`) replays every scenario through `VerdictService`; `VERDICT_GOLDEN_GRAPH=real` will run it on the partner's graph once that's importable.

## Watch out for

- The pass thresholds are settled (0.80 / 0.90 / 0.95 / 0.99 by strictest severity, AGENTS.md OQ-4). Orders writes each ticket's `pass_threshold`, and the verdict reads it from the ticket.
- `FakeProvenanceGraph` tests the policy only; real-graph runs carry `needs-partner`.
- A false wash erases real contamination. The partner's graph caps how much a wash can clean.
- The plate model reads only the plate's time-forward paths, at most 10 edges (the longest in its training data, about 340 of Laya's 512 tokens); a longer history raises `LayaCheckUnavailable` and holds. It was trained on generated kitchens with full transfer (k = 1), so it leans strict.
