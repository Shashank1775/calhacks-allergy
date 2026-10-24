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

**Unknown unit IDs.** Every unit ID the verdict sees, in `reach`, in Laya's answer or on the ticket, must be in the allergen unit table (seeded from `contracts/allergen_units.json`). At runtime the schemas check only the ID's pattern, so a typo such as `tree_nuts` for `tree_nut` gets through. An unknown ID is an `error` (section 2), never "a unit that isn't forbidden". Dropping it would let a plate pass with a misspelled forbidden allergen on it.

**Child units.** Every `parent_id` is null today (OQ-2: the nine majors only). If child units are added later, a `reach` entry for a child must count against a forbidden parent. The ADR that adds child units has to say how.

### 2. Input health

The inputs are **healthy** only when none of these reason codes apply:

| Code | When |
|---|---|
| `error` | Any exception on the verdict path; `reach` returns nothing (`None`, which is not the same as an empty list); a probability that isn't a number in [0, 1]; a unit ID in `reach`, in Laya's answer or on the ticket that isn't in the allergen unit table (section 1) |
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
exact flag on u:  1 - exact_u <= effective_threshold
model flag on u:  1 - laya_u  <= effective_threshold
```

- **Why this form, and not `exact_u >= 1 - effective_threshold`.** A float can't hold 0.95 or 0.99 exactly [6]. In Python, `1 - 0.95` is `0.050000000000000044` and `1 - 0.99` is `0.010000000000000009`. With the other form, peanut at exactly 0.05 on a 0.95 ticket misses the flag. Its confidence, `1 - 0.05`, comes out at exactly 0.95, which isn't below 0.95, so the plate passes at a tie. The same happens at 0.01 on a 0.99 ticket. The form above does the same subtraction confidence does, so a single unit at a tie always flags. At 0.80 and 0.90 the rounding happens to land on the safe side, but the code must not rely on that.
- Each flagged unit gets one `flagged` entry with `probability = p_u` and `flagged_by` set to `exact`, `laya` or both.
- **How flags relate to confidence.** The sum of forbidden probabilities is at least `p_u`, so a flagged unit means `confidence <= 1 - p_u <= effective_threshold`. This holds in floats too. IEEE 754 rounds each addition and subtraction from its exact result [7], and rounding never swaps the order of two numbers, so adding a term of at least 0 never makes the sum smaller. Flags never disagree with confidence. They name the unit to blame, and at an exact tie (`confidence == effective_threshold`) the flag holds the plate, so the tie leans HOLD. Several units can each sit under the line and still add up past the budget. That plate holds with `low_confidence`.
- **Why the line comes from the ticket, not from each unit's severity.** The contract has one threshold per ticket, set by the strictest severity on it. A separate, lower line per unit could never change a decision, because confidence already holds those plates. It would only change the labels. One number per severity is simpler.

### 6. Decision and reasons

```
hold = not healthy or any flag or confidence < effective_threshold
```

The verdict lists every reason (AGENTS.md §8: every HOLD logs all its reasons):

- each health code that applies;
- `exact_flag` or `model_flag` for each flagged unit, with its `allergen_unit_id`;
- `low_confidence` whenever `confidence < effective_threshold`, even when there are flags too, with a `detail` such as `"0.92 < 0.95"`. This includes every health hold, because confidence is 0 there. That is on purpose: the recorded confidence and the reasons always agree, and the health code says what actually went wrong.

### 7. What never loosens a verdict

- `constraint.confidence` (the parse confidence) is ignored. Every constraint on a sent ticket counts in full, and a shaky parse never shrinks a probability.
- `ticket.exceptions` never suppress a flag or raise confidence (OQ-2 default).
- `trace_sensitive: false` is treated as `true` until the OQ-8 ADR lands.
- Known units that aren't forbidden don't enter confidence at all (invariant 4). An unknown unit ID is an `error`, not a unit that isn't forbidden (section 1).

### 8. VerdictSettings values (placeholders, owner sign-off needed)

| Field | Env var | Default | One unit flags at p ≥ |
|---|---|---|---|
| `pass_threshold_preference` | `VERDICT_PASS_THRESHOLD_PREFERENCE` | 0.80 | 0.20 |
| `pass_threshold_intolerance` | `VERDICT_PASS_THRESHOLD_INTOLERANCE` | 0.90 | 0.10 |
| `pass_threshold_allergy` | `VERDICT_PASS_THRESHOLD_ALLERGY` | 0.95 | 0.05 |
| `pass_threshold_anaphylactic` | `VERDICT_PASS_THRESHOLD_ANAPHYLACTIC` | 0.99 | 0.01 |
| `confidence_rule` | none (fixed in code) | `union_bound/1` | none |

- These are the OQ-4 defaults. The owner accepted them as defaults, not as final values. **The owner should sign off on the four numbers, the flag lines they imply, and the union-bound rule.**
- They are logged at startup and recorded on every verdict. `versions` allows only strings, so they go into `versions.policy`, for example `"0.1.0;confidence=union_bound/1;thresholds=0.80,0.90,0.95,0.99"`.
- **Bad settings fail closed.** `VerdictSettings` refuses to load if any of the four is outside (0, 1], or if any of them is lower than the one before it (preference, intolerance, allergy, anaphylactic). A mistyped env var must not quietly lower a floor. A verdict service that can't load its settings sends no verdicts, and the gate opens only on a PASS (`kitchen/gate/README.md`), so the plate stays held.

### 9. Monotonicity: more evidence never raises confidence

- **Raising any `exact_u` or `laya_u`** can only raise `p_u` (it is a max) and the sum. So confidence can only fall, and a flag can only appear, because the line is fixed for the ticket.
- **Adding a forbidden unit** (an amendment) adds a term of at least 0. With the ticket's `pass_threshold` held fixed, it can only raise `effective_threshold`, because the severity floor can only go up. The result is more holds, never fewer. If a later revision lowers `ticket.pass_threshold`, for example a tightened 0.999 reset to the 0.95 default, the floor still covers the severity part but the tightening is lost. orders is responsible for carrying a tightened `pass_threshold` into later revisions.
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

A hybrid keeps both readings: structural flags from the graph, and probabilistic confidence. It meets "any k". Its flags still carry the false-wash risk, though confidence keeps counting what a wash leaves behind. Contracts 0.1.0 has no field that tells a structural reach from a probabilistic one, so the hybrid would need an RFC first.

We choose the probabilistic reading. So the golden holds are guaranteed for `k ≥ 0.25` on every action, and the washes pass for every `k ≤ 1`. Two texts say "any k in (0, 1]":

- `kitchen/graph/README.md` promises the expected `allergens_reached` and chains for any k. It says nothing about decisions.
- RFC #130 says each new scenario's decision holds for any k. Its point 1 is the sharper conflict. There, `allergens_reached` is structural: a unit counts only if no carrier on its path was washed in between, which is why `wash-resets-knife` lists `[]`. But `reach` lists every unit with nonzero probability (`ProvenanceGraph.reach` in `contracts/apis.md`).

The RFC #130 wording needs narrowing. The verdict Manager should raise this on RFC #130 and with the partner. The owner chooses between the probabilistic reading and the hybrid when signing off on this ADR.

### 11. Edge tie order (OQ-17): the partner decides

Edge ordering lives in the partner's graph (OQ-17), not in the verdict. Here is what the verdict assumes:

- **The verdict's own tests don't depend on tie order.** `FakeProvenanceGraph` answers from scenario ground truth, and none of the six merged golden scenarios has a tie: events on a shared entity never overlap.
- **On the real graph** (verdict-10, #65), and for any golden scenario tagged `tie-order`, the verdict expects the OQ-17 default order: `t_start_ms`, then `t_end_ms`, then `event_id`. Ties are resolved **strictly: the order of tied events never lowers what a transfer carries.** Pair by pair, that means:
  - A wash goes before transfers *into* the washed entity and after transfers *out of* it.
  - A `direction: both` contact tied with a wash counts in each direction separately. Its inbound part lands after the wash, and its outbound part carries the value from before the wash.
  - A transfer *into* an entity goes before a transfer *out of* it.

  When all of these tie on one entity, no single sort order satisfies every pair. The partner decides how to compute that case.
- **Please pass this to the partner** (through the Manager or Director). There are two cases where the literal default is loose:
  - "A tied wash is applied before the transfer" (`kitchen/graph/README.md`) is strict for transfers into the washed entity, as in `tied-wash-does-not-erase-scoop`. It is loose for transfers out of it: a knife washed and touching a plate in the same camera window would hand the plate the washed residual.
  - Two tied transfers on a shared entity, such as `bin-1 → knife` and `knife → plate` in the same camera window, can be applied out before in. The plate then never gets the peanut, `reach` reports nothing, and the plate passes.

  Both are graph math, so they go to the partner as an escalation. The verdict rule doesn't change.
- **Tied steps inside a chain** may fail the "strictly increasing `t_ms`" check, `test_chain_time_ordered` in verdict-03 (#26). That is a test on ExactLaya's output, and nothing at runtime holds a plate for it. Revisit it when the partner's OQ-17 ADR lands.

### 12. Property tests that guard this definition

verdict-01 (#24) adopts these. Each one is marked `@pytest.mark.safety` and uses `hypothesis` [5]. They live in `kitchen/verdict/tests/test_confidence.py`. Run them with `uv run pytest kitchen/verdict/tests/test_confidence.py -m safety`.

| Test | Property |
|---|---|
| `test_confidence_in_unit_interval` | For any valid inputs, `0 <= confidence <= 1` |
| `test_confidence_monotone_in_probability` | Raising any exact or Laya probability of a forbidden unit never raises confidence, never removes a flag and never turns a hold into a pass |
| `test_confidence_monotone_in_constraints` | Adding a forbidden unit never raises confidence and never turns a hold into a pass |
| `test_confidence_zero_when_unhealthy` | Any health failure gives confidence 0 and a hold with that reason code |
| `test_laya_never_raises_confidence` | For any Laya response, `confidence(full) <= confidence(rules_only)`, and the full-mode flags include every exact flag |
| `test_flag_implies_hold` | Any flagged unit gives a hold, and `confidence <= effective_threshold` |
| `test_pass_needs_margin` | A pass means healthy, no flags, `confidence >= effective_threshold`, and `1 - p_u > effective_threshold` for every forbidden unit (the same float form as the flag in section 5) |
| `test_effective_threshold_never_looser` | The effective threshold is at least `ticket.pass_threshold` and at least the floor for the strictest severity |
| `test_parse_confidence_ignored` | Changing `constraint.confidence` never changes confidence or the decision |
| `test_non_forbidden_units_ignored` | Reach entries for known units that aren't on the ticket never change confidence |
| `test_unknown_unit_holds` | Any unit ID outside the allergen unit table, in `reach`, in Laya's answer or on the ticket, gives confidence 0 and a hold with `error`, whatever its probability |
| `test_duplicate_entries_never_raise_confidence` | Splitting one unit's probability across duplicate entries never raises confidence |
| `test_confidence_order_independent` | Shuffling the reach and Laya lists gives byte-identical confidence |
| `test_confidence_examples` | Table cases, including: peanut 0.04 plus sesame 0.04 on a 0.95 ticket gives confidence 0.92 and a hold (the placeholder passes it); peanut at exactly 0.05 on a 0.95 ticket, and at exactly 0.01 on a 0.99 ticket, each hold with `exact_flag` (the float ties in section 5) |

They sit beside verdict-01's own `models_only_tighten`, `fail_closed`, `test_exceptions_do_not_loosen` and `test_trace_default_strict`.

## Consequences

- **Easier.** One number per severity drives everything. Flags and confidence can't disagree. verdict-01 swaps out its placeholder without changing `decide()`'s signature.
- **Harder.** There will be more holds than under the placeholder:
  - Tickets with several forbidden units hold more often (a sum instead of a max).
  - A mixed ticket is judged at its strictest severity. For example, "prefer no dairy" on an anaphylactic-peanut ticket is held to 0.99. The contract has one threshold per ticket, and this leans HOLD.
  - A misspelled unit ID anywhere holds every plate it touches until someone fixes the source. That is loud on purpose.
- **Watch.**
  - Whether a golden hold really holds depends on the partner's efficiencies `k` (section 10).
  - The thresholds are policy, not clinical values, and the owner needs to sign off on them.
  - orders and verdict each hold a copy of the OQ-4 numbers. The floor makes drift fail closed.
- **Golden checks** (verdict-05, #28):
  - Read `reason_codes` as "must include", because every HOLD lists all its reasons. For example, `knife-carries-peanut-to-plate` also carries `low_confidence`.
  - The flagged units should equal `allergens_reached` minus any unit the revision doesn't forbid. This rule also matches `late-amendment-adds-dairy` rev 1, which lists milk without forbidding it.
  - `FakeProvenanceGraph` should report each reached unit at probability 1.0, or at least 0.20 (the highest value in the section 8 table), so that flags fire. That choice is platform's.
- **Enforced by** the property tests in section 12, the golden runner, and the safety reviewer's checklist (AGENTS.md §5).

## Sources

1. Fréchet inequalities. Wikipedia. Bounds on a conjunction or disjunction of events that hold whatever their dependence. https://en.wikipedia.org/wiki/Fr%C3%A9chet_inequalities
2. Boole's inequality (union bound). Wikipedia. https://en.wikipedia.org/wiki/Boole%27s_inequality
3. FAO and WHO, *Risk assessment of food allergens, part 2: review and establish threshold levels in foods for the priority allergens*, Food Safety and Quality Series 15, 24 January 2023. ED05-based reference doses in mg of total protein. https://www.who.int/publications/i/item/9789240065420
4. "Allergen Bureau transitions to VITAL 4.0 standard for precautionary allergen labeling", Food Safety Magazine, 28 August 2024. VITAL 4.0 moves to ED05 reference doses. https://food-safety.com/articles/9703-allergen-bureau-transitions-to-vital-40-standard-for-precautionary-allergen-labeling
5. Hypothesis, the property-based testing library for Python. https://hypothesis.readthedocs.io/en/latest/
6. "Floating-Point Arithmetic: Issues and Limitations", the Python tutorial. Most decimal fractions can't be stored exactly as binary floats. https://docs.python.org/3/tutorial/floatingpoint.html
7. IEEE 754. Wikipedia. An operation's result is its exact result, rounded. https://en.wikipedia.org/wiki/IEEE_754

The golden-scenario numbers in section 10 are hand calculations with the AGENTS.md §3 formulas, not runs of the partner's graph. They are **unverified against the real graph** until verdict-10. The float values in section 5 were checked in Python 3 (`1 - 0.95`, `1 - 0.99`, and a 200,000-case random check that a flag always means `confidence <= effective_threshold` and a pass always means `1 - p_u > effective_threshold`).
