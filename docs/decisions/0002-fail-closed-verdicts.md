# 0002: Verdicts fail closed, and models only tighten

- **Status:** accepted
- **Date:** 2026-10-23
- **Deciders:** director, owner

## Context

A wrong PASS can send an allergen to a guest who reacts to it. A wrong HOLD costs a remake. Several parts of the pipeline are learned models (Laya, the LSTM) that can be wrong, slow or unavailable.

## Decision

- The verdict is computed in plain, deterministic code: `hold = exact_flags or model_flags or confidence < ticket.pass_threshold or any_error`.
- Model outputs can add reasons to hold. They can never remove one, and never raise clean-confidence above what exact reachability allows.
- Any exception, timeout, missing input, stale feed or unknown-history entity on the verdict path yields HOLD (the gate shows `fault` when it loses its feed).
- Running without an expected model is an explicit `rules_only` mode a person turns on, and is recorded on every verdict.

## Consequences

- More holds when parts are flaky. That is the intended trade.
- PRs on the verdict path are `safety-critical` and need property tests (`safety` marker).
- The golden scenarios in `contracts/scenarios/` encode these rules and run in CI.
