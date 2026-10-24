# Verdicts fail closed and are plain code

- **Status:** accepted
- **Date:** 2026-10-23
- **Decided by:** owner, director

## Context

A wrong PASS can send an allergen to a guest who reacts to it. A wrong HOLD costs a remake. Several parts of the pipeline are learned models (Laya, the ConvLSTM) that can be wrong, slow or unavailable.

## Decision

- The verdict is computed in plain, deterministic code from the partner graph's reachability: `hold = forbidden_allergen_flagged or laya_flagged or confidence < ticket.pass_threshold or any_error`, where an allergen is flagged at probability ≥ `1 − pass_threshold` and a Laya failure counts as an error.
- Learned models (Laya, the ConvLSTM) only produce the verdict's inputs. A low-confidence input becomes a one-tap question or a HOLD, never a PASS.
- **Update (2026-10-24):** Laya's final plate check ([decision](laya-plate-check.md)) is a second opinion that can only add hold reasons (`laya_flagged`, `laya_unavailable`).
- Any exception, timeout, missing input, stale feed or unknown-history entity on the verdict path yields HOLD (the gate shows `fault` when it loses its feed).

## Consequences

- More holds when parts are flaky. That is the intended trade.
- PRs on the verdict path are `safety-critical` and need property tests (`safety` marker).
- The golden scenarios in `contracts/scenarios/` encode these rules and run in CI.
