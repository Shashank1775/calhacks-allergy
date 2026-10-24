# Laya parses ticket notes and bin labels

- **Status:** accepted
- **Date:** 2026-10-24
- **Decided by:** owner, director

## Context

We need a model that turns a ticket note into forbidden allergens and a bin label into allergen probabilities. The orders spike (PR #133) found two options with the same request format (`POST /v1/systemone`: a state plus typed questions in, calibrated probabilities out):

- **TypeSafe Jev**: a closed, hosted API with a waitlist and per-token pricing.
- **Laya** (`convaiinnovations/laya`): open weights (Apache-2.0), self-hosted, and fine-tunable.

## Decision

- Use **Laya**, fine-tuned on our own examples. Training runs on pina1 (the lab's GPU server, reached over the Georgia Tech VPN) with Laya's own `laya.train` recipe.
- The interface is `LayaParser` (contracts/apis.md). Its adapter asks Laya typed questions (does the note forbid each allergen, how severe, do traces matter, is an ingredient allowed; does the bin contain each allergen) and builds `ParseResult` and `AllergenProfile` from the answers.
- Answers below the question threshold become one-tap questions, as before.

## Consequences

- No API keys, no per-call cost, and it works offline.
- Laya answers questions; it doesn't write text. Free-text fields (such as the name of an allowed ingredient) come from the note itself or a one-tap question.
- The checkpoint we use is English-only, and reads at most 512 tokens per note.
- The base checkpoint is close to chance on our questions. It only becomes useful after fine-tuning, and its probabilities are only calibrated after the temperature refit that `laya.train` runs at the end.
