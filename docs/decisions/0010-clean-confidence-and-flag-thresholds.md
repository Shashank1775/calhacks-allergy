# 0010: Clean-confidence and flag thresholds

- **Status:** proposed
- **Date:** 2026-10-23
- **Deciders:** director, mgr-verdict, owner (signs off on the threshold values and on the rule itself, because both decide when a plate passes)

## Context

The verdict rule is fixed by [ADR 0002](0002-fail-closed-verdicts.md) and AGENTS.md §3, invariant 2:

```
hold = exact_flags or model_flags or confidence < ticket.pass_threshold or any_error
```

Nobody has defined `confidence` or a "flag" yet. verdict-01 (#24) uses `1 - max P(forbidden unit)` as a placeholder until this ADR lands (issue #25).

**What the verdict gets** (contracts 0.1.0):

- `ticket`: the forbidden units (`constraints`), each with a `severity`, a `trace_sensitive` flag and a parse `confidence`, plus `pass_threshold` and `exceptions`.
- `ProvenanceGraph.reach`: a list of `allergen_reach` (unit, probability, chain). It lists only units with nonzero probability.
- In `full` mode, Laya's `laya_response`, which has the same `allergen_reach` shape.
- Input health: errors, missing inputs, stale tracks, unknown-history ancestors and model availability.

**Thresholds.** The owner accepted the OQ-4 defaults on 2026-10-23: a pass threshold of 0.80 / 0.90 / 0.95 / 0.99 for preference / intolerance / allergy / anaphylactic. orders sets `ticket.pass_threshold` from the strictest severity on the ticket.

**Why the placeholder is not enough.** `1 - max p` is the *most optimistic* chance that no forbidden allergen reached the plate that the per-unit numbers allow. It is the Fréchet upper bound [1]. Take peanut at 0.04 and sesame at 0.04 on a 0.95 ticket. The placeholder gives 0.96 and passes. But the chance that at least one of them got there can be as high as 0.08, and it is 0.0784 even if the two are independent. The plate is over its doubt budget either way.

**What the numbers are not.** Real precautionary allergen labelling works on *dose*: milligrams of protein, measured against reference doses. The FAO/WHO expert consultation set ED05-based reference doses [3], and the Allergen Bureau's VITAL 4.0 adopted them in 2024 [4]. A camera can't measure dose. Our probabilities say how likely contact was, not how much allergen arrived. So the thresholds here are an owner policy, not a clinical risk level. **Unverified:** no mapping is known from these probabilities to reaction risk.

**Out of scope:** what `trace_sensitive: false` changes (OQ-8, M3), the graph math (partner), and the implementation (verdict-01).

## Decision

### 1. Per-unit probability

Let `F` be the set of forbidden units on the ticket revision being judged. For each unit `u` in `F`:

- `exact_u` is the probability `reach` reports for `u`. It is 0 when `u` is absent, because `reach` lists every unit with nonzero probability.
- `laya_u` is the same thing from Laya's response, in `full` mode only. It is 0 in `rules_only`.
- If one source lists a unit more than once, that source's value is the sum of those entries, capped at 1. A duplicate can only lower confidence.
- `p_u = max(exact_u, laya_u)`. Laya can raise a unit's probability, but never lower it (invariant 2).

### 2. Input health

The inputs are **healthy** only when none of these reason codes apply:

| Code | When |
|---|---|
| `error` | Any exception on the verdict path; `reach` returns nothing (`None`, which is not the same as an empty list); a probability that isn't a number in [0, 1] |
| `missing_input` | No ticket, no plate binding, or no usable `pass_threshold` |
| `stale_track` | The plate's entity row is older than the staleness window |
| `model_unavailable` | `full` mode and Laya failed, timed out or answered malformed |
| `unknown_history` | `unknown_ancestors` is non-empty (invariant 8) |
| `unanswered_question` | The ticket still has an open question (invariant 3) |

### 3. Clean-confidence

```
if not healthy:
    confidence = 0
else:
    confidence = max(0, 1 - sum(p_u for u in F))    # add in sorted unit-id order
```

- **Why a sum.** `1 - sum(p_u)` is the lowest chance of "no forbidden allergen" that is still consistent with the per-unit numbers. It comes from Boole's inequality (the union bound) and is the Fréchet lower bound. It holds however the units depend on each other [1][2]. The units on one plate do depend on each other: one knife, one bin of satay, peanut and soy together. A product `prod(1 - p_u)` would assume independence. When the probabilities are small, which is where every pass happens, the two differ by less than 2 points at the 0.80 line and by less than 0.01 points at the 0.99 line. So the safe choice costs almost nothing.
- **Why zero when unhealthy.** If we can't trust the inputs, we can't vouch for the plate. Zero also keeps the recorded confidence consistent with the HOLD.
- **Why sorted order.** Float addition depends on order. Adding in sorted unit-id order makes the same inputs give byte-identical verdict JSON.
- The result is always in [0, 1].

### 4. Pass threshold

```
effective_threshold = max(ticket.pass_threshold, settings floor for the strictest severity in F)
```

- A ticket can make its threshold stricter than the severity default, but never looser. orders keeps its own copy of the OQ-4 numbers, so if the two copies drift, the result fails toward HOLD.
- A ticket with no constraints uses `ticket.pass_threshold`. With `F` empty, a healthy plate has confidence 1.
- `verdict.pass_threshold` records `effective_threshold`.

### 5. Flags

A forbidden unit `u` is **flagged** when its probability alone uses up the plate's whole doubt budget:

```
flag_line = 1 - effective_threshold
exact flag on u:  exact_u >= flag_line
model flag on u:  laya_u  >= flag_line
```

- Each flagged unit gets one `flagged` entry with `probability = p_u` and `flagged_by` set to `exact`, `laya` or both.
- **How flags relate to confidence.** A flag means `sum(p) >= flag_line`, so `confidence <= effective_threshold`. Flags never disagree with confidence. They name the unit to blame, and at an exact tie (`confidence == effective_threshold`) the flag holds the plate, so the tie leans HOLD. Several units can each sit under the line and still add up past the budget. That plate holds with `low_confidence`.
- **Why the line comes from the ticket, not from each unit's severity.** The contract has one threshold per ticket, set by the strictest severity on it. A separate, lower line per unit could never change a decision, because confidence already holds those plates. It would only change the labels. One number per severity is simpler.

### 6. Decision and reasons

```
hold = not healthy or any flag or confidence < effective_threshold
```

The verdict lists every reason (AGENTS.md §8: every HOLD logs all its reasons):

- each health code that applies;
- `exact_flag` or `model_flag` for each flagged unit, with its `allergen_unit_id`;
- `low_confidence` whenever `confidence < effective_threshold`, even when there are flags too, with a `detail` such as `"0.92 < 0.95"`.

### 7. What never loosens a verdict

- `constraint.confidence` (the parse confidence) is ignored. Every constraint on a sent ticket counts in full, and a shaky parse never shrinks a probability.
- `ticket.exceptions` never suppress a flag or raise confidence (OQ-2 default).
- `trace_sensitive: false` is treated as `true` until the OQ-8 ADR lands.
- Units that aren't forbidden don't enter confidence at all (invariant 4).

### 8. VerdictSettings values (placeholders, owner sign-off needed)

| Field | Env var | Default | Flag line it gives |
|---|---|---|---|
| `pass_threshold_preference` | `VERDICT_PASS_THRESHOLD_PREFERENCE` | 0.80 | 0.20 |
| `pass_threshold_intolerance` | `VERDICT_PASS_THRESHOLD_INTOLERANCE` | 0.90 | 0.10 |
| `pass_threshold_allergy` | `VERDICT_PASS_THRESHOLD_ALLERGY` | 0.95 | 0.05 |
| `pass_threshold_anaphylactic` | `VERDICT_PASS_THRESHOLD_ANAPHYLACTIC` | 0.99 | 0.01 |
| `confidence_rule` | none (fixed in code) | `union_bound/1` | none |

- These are the OQ-4 defaults. The owner accepted them as defaults, not as final values. **The owner should sign off on the four numbers, the flag lines they imply, and the union-bound rule.**
- They are logged at startup and recorded on every verdict. `versions` allows only strings, so they go into `versions.policy`, for example `"0.1.0;confidence=union_bound/1;thresholds=0.80,0.90,0.95,0.99"`.

### 9. Monotonicity: more evidence never raises confidence

- **Raising any `exact_u` or `laya_u`** can only raise `p_u` (it is a max) and the sum. So confidence can only fall, and a flag can only appear, because the line is fixed for the ticket.
- **Adding a forbidden unit** (an amendment) adds a term of at least 0, and it can only raise `effective_threshold`. The result is more holds, never fewer.
- **Any health failure** sets confidence to 0, its minimum.
- **Laya** always gives `p_u >= exact_u`. So `confidence(full) <= confidence(rules_only)`, and the flags in `full` mode include every exact flag (invariant 2).
- **Duplicate entries** are summed, so splitting one unit's entry into several can't lower its total.

This is monotone in the verdict's own inputs. Two more facts belong to the partner's graph, not to us. Under noisy-OR, extra transfer edges can only raise a unit's probability. A wash is evidence of *cleaning*, so a unit's probability may fall after one, by the graph's math.

### 10. Check against the golden scenarios

These numbers use the graph math in AGENTS.md §3 with one efficiency `k` for every action. `p` is the forbidden unit's probability at the plate when `k = 1`.

| Scenario | T | p at k = 1 | Confidence at k = 1 | Expected | Under this rule |
|---|---|---|---|---|---|
| `knife-carries-peanut-to-plate` | 0.99 | 0.838 | 0.162 | hold | hold for k ≥ 0.11 |
| `wash-resets-knife` | 0.95 | 0.017 | 0.983 | pass | pass for every k ≤ 1 |
| `time-order-blocks-path` | 0.95 | 0 | 1 | pass | pass |
| `late-amendment-adds-dairy` rev 1 | 0.95 | milk not forbidden | 1 | pass | pass |
| `late-amendment-adds-dairy` rev 2 | 0.95 | 0.802 | 0.198 | hold | hold for k ≥ 0.25 |
| `unknown-tool-mid-shift` | 0.95 | (unhealthy) | 0 | hold | hold for every k |
| `clean-plate-from-stack` | 0.95 | 0 | 1 | pass | pass |

Proposed in RFC #130, not merged: `hand-carries-sesame-via-tongs` holds for k ≥ 0.242, `tied-wash-does-not-erase-scoop` for k ≥ 0.245, and `second-wash-cleans-only-the-past` t9 for k ≥ 0.243. Its plate t10 passes for every k ≤ 1.

**Finding: "for any k in (0, 1]" can't be met by any rule built on probabilities.** Both scenarios below have threshold 0.95:

- `late-amendment-adds-dairy` rev 2 must hold at `p = 0.80·k²`, which is 0.008 at k = 0.1.
- `wash-resets-knife` must pass at `p = 0.017` (k = 1).

Any rule that looks only at `p` and `T`, and never holds less as `p` grows, gets one of them wrong. The only way to meet "any k" is for exact reachability to drop every path a wash has crossed, *and* for the verdict to flag any nonzero unit. That would treat a wash of any probability as a full reset, which is the README risk "a false wash erases real contamination". It would also leave the OQ-4 thresholds with nothing to do.

We choose the probabilistic reading. So the golden holds are guaranteed for `k ≥ 0.25` on every action, and the washes pass for every `k ≤ 1`. Two texts say "any k in (0, 1]": the wording in `kitchen/graph/README.md` and the RFC #130 descriptions. Both need narrowing. The verdict Manager should raise this on RFC #130 and with the partner.

### 11. Edge tie order (OQ-17): the partner decides

Edge ordering lives in the partner's graph (OQ-17), not in the verdict. Here is what the verdict assumes:

- **The verdict's own tests don't depend on tie order.** `FakeProvenanceGraph` answers from scenario ground truth, and none of the six merged golden scenarios has a tie: events on a shared entity never overlap.
- **On the real graph** (verdict-10, #65), and for any golden scenario tagged `tie-order`, the verdict expects the OQ-17 default order: `t_start_ms`, then `t_end_ms`, then `event_id`. Ties that involve a wash are resolved **strictly: a wash tied with a transfer never lowers what that transfer carries.** That means the wash goes before transfers *into* the washed entity and after transfers *out of* it.
- **Please pass this to the partner** (through the Manager or Director). The literal default, "a tied wash is applied before the transfer" (`kitchen/graph/README.md`), is strict for transfers into the washed entity, as in `tied-wash-does-not-erase-scoop`. It is loose for transfers out of it: a knife washed and touching a plate in the same camera window would hand the plate the washed residual.
- **Tied steps inside a chain** would fail the "strictly increasing `t_ms`" check in verdict-03 (#26) and hold the plate. That fails closed, so it's safe but noisy. Revisit it when the partner's OQ-17 ADR lands.

### 12. Property tests that guard this definition

verdict-01 (#24) adopts these. Each one is marked `@pytest.mark.safety` and uses `hypothesis` [5]. Run them with `uv run pytest kitchen/verdict -m safety -k "confidence or flag"`.

| Test | Property |
|---|---|
| `test_confidence_in_unit_interval` | For any valid inputs, `0 <= confidence <= 1` |
| `test_confidence_monotone_in_probability` | Raising any exact or Laya probability of a forbidden unit never raises confidence, never removes a flag and never turns a hold into a pass |
| `test_confidence_monotone_in_constraints` | Adding a forbidden unit never raises confidence and never turns a hold into a pass |
| `test_confidence_zero_when_unhealthy` | Any health failure gives confidence 0 and a hold with that reason code |
| `test_laya_never_raises_confidence` | For any Laya response, `confidence(full) <= confidence(rules_only)`, and the full-mode flags include every exact flag |
| `test_flag_implies_hold` | Any flagged unit gives a hold, and `confidence <= effective_threshold` |
| `test_pass_needs_margin` | A pass means healthy, no flags, `confidence >= effective_threshold`, and every forbidden `p_u < 1 - effective_threshold` |
| `test_effective_threshold_never_looser` | The effective threshold is at least `ticket.pass_threshold` and at least the floor for the strictest severity |
| `test_parse_confidence_ignored` | Changing `constraint.confidence` never changes confidence or the decision |
| `test_non_forbidden_units_ignored` | Reach entries for units that aren't on the ticket never change confidence |
| `test_duplicate_entries_never_raise_confidence` | Splitting one unit's probability across duplicate entries never raises confidence |
| `test_confidence_order_independent` | Shuffling the reach and Laya lists gives byte-identical confidence |
| `test_confidence_examples` | Table cases, including peanut 0.04 plus sesame 0.04 on a 0.95 ticket: confidence 0.92, hold (the placeholder passes it) |

They sit beside verdict-01's own `models_only_tighten`, `fail_closed`, `test_exceptions_do_not_loosen` and `test_trace_default_strict`.

## Consequences

- **Easier.** One number per severity drives everything. Flags and confidence can't disagree. verdict-01 swaps out its placeholder without changing `decide()`'s signature.
- **Harder.** There will be more holds than under the placeholder:
  - Tickets with several forbidden units hold more often (a sum instead of a max).
  - A mixed ticket is judged at its strictest severity. For example, "prefer no dairy" on an anaphylactic-peanut ticket is held to 0.99. The contract has one threshold per ticket, and this leans HOLD.
- **Watch.**
  - Whether a golden hold really holds depends on the partner's efficiencies `k` (section 10).
  - The thresholds are policy, not clinical values, and the owner needs to sign off on them.
  - orders and verdict each hold a copy of the OQ-4 numbers. The floor makes drift fail closed.
- **Golden checks** (verdict-05, #28):
  - Read `reason_codes` as "must include", because every HOLD lists all its reasons. For example, `knife-carries-peanut-to-plate` also carries `low_confidence`.
  - The flagged units should equal `allergens_reached` minus any unit the revision doesn't forbid. This rule also matches `late-amendment-adds-dairy` rev 1, which lists milk without forbidding it.
  - `FakeProvenanceGraph` should report each reached unit at probability 1.0, or at least 0.20 (the highest flag line), so that flags fire. That choice is platform's.
- **Enforced by** the property tests in section 12, the golden runner, and the safety reviewer's checklist (AGENTS.md §5).

## Sources

1. Fréchet inequalities. Wikipedia. Bounds on a conjunction or disjunction of events that hold whatever their dependence. https://en.wikipedia.org/wiki/Fr%C3%A9chet_inequalities
2. Boole's inequality (union bound). Wikipedia. https://en.wikipedia.org/wiki/Boole%27s_inequality
3. FAO and WHO, *Risk assessment of food allergens, part 2: review and establish threshold levels in foods for the priority allergens*, Food Safety and Quality Series 15, 24 January 2023. ED05-based reference doses in mg of total protein. https://www.who.int/publications/i/item/9789240065420
4. "Allergen Bureau transitions to VITAL 4.0 standard for precautionary allergen labeling", Food Safety Magazine, 28 August 2024. VITAL 4.0 moves to ED05 reference doses. https://food-safety.com/articles/9703-allergen-bureau-transitions-to-vital-40-standard-for-precautionary-allergen-labeling
5. Hypothesis, the property-based testing library for Python. https://hypothesis.readthedocs.io/en/latest/

The golden-scenario numbers in section 10 are hand calculations with the AGENTS.md §3 formulas, not runs of the partner's graph. They are **unverified against the real graph** until verdict-10.
