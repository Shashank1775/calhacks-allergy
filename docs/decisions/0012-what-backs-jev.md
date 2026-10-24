# 0012: What backs Jev, and how orders calls it

- **Status:** proposed (OQ-5 is the owner's call; this is the orders spike's answer and recommendation)
- **Date:** 2026-10-23
- **Deciders:** director, mgr-orders, owner

## Context

Jev is the model role behind [`JevClient`](../../contracts/apis.md#jevclient). It parses ticket notes (`parse_note`) and reads bin labels and prep sheets (`profile_bin`). OQ-5 asks what backs it. Until the owner decides, the default holds: Jev stays behind the interface and `FakeJev` covers it. Issue #11 (orders-03) asks the spike to find the provider, auth, request and response shape, rate limits, cost per parse, latency, offline options and image input, and to sketch a real adapter.

**What we found.** "Jev" is a real product, not only a placeholder name. TypeSafe AI's Jev is a "System One" decision model, in early access since September 2026 [S1][S3]. It fits the role: you send some text (the *state*) and typed questions, and get back typed answers with probabilities. It does not write free text. `.env.example` still calls the name a placeholder, so question 1 below asks the owner to confirm this is the Jev they meant.

**How this was checked.** We read the vendor docs and secondary sources on 2026-10-23. We made **no API calls**: we have no key, and agents never call paid APIs. "Unverified" below means we didn't measure it ourselves: it's either a vendor claim or comes from a secondary source.

**Scope notes.**
- The issue asked for `kitchen/orders/docs/jev-spike.md`. The dispatch scope for this task is this ADR, so the full spike answer lives here.
- The repo's **Laya role** (names allergens that reached a plate; owned by verdict and sim) is out of scope. There's also an open-weights decision model called "Laya", from ConvAI Innovations. It appears below only as an offline backend for the *Jev* role. The shared name is a coincidence, so don't mix the two up.

## Findings

| Question | Answer | Source |
|---|---|---|
| Provider and model | TypeSafe AI, model Jev. Versioned IDs are `jev-1.14.0` (released 2026-10-14) and `jev-1.13.0`. The aliases `jev-latest` and `jev-preview` both point to `jev-1.14.0` today, and aliases move when a release ships | [S2][S9] |
| Access | Early access through a waitlist; keys are issued at `console.typesafe.ai/keys`. Also listed on Vercel AI Gateway as `typesafe-ai/jev`. **Unverified:** whether Vercel's listing skips the waitlist | [S3][S4][S11] |
| Auth | `Authorization: Bearer <key>`. The official SDK reads `TYPESAFE_API_KEY` | [S4][S5] |
| Request | `POST https://api.typesafe.ai/v1/systemone`, JSON `{model, state, questions, assets?}`. Question types: `noul` (yes/no), `choice` (1 to 255 options), `score` (1 to 10 ordered levels) and `box` (images only, 1.14 only). All questions in one call are answered separately against the same state | [S1][S4][S5] |
| Response | `{model, answers, usage}`. `noul` gives `noul`, the probability of yes. `choice` gives `choice`, `confidence` and `probabilities`. `score` gives `score`, `confidence`, `legend` and `probabilities`. `usage` gives `input_tokens` (billed) and `output_tokens` (free) | [S4][S5] |
| Limits | Body up to 32 MiB. Context is 64k tokens per request and 32k per question (262k with an opt-in long-context header, which the vendor says is slower and less accurate) | [S2][S7] |
| Errors | `400` bad request or over context, `401` bad key, `413` too large, `415` bad encoding, `422` validation, `429` rate limited, `529` overloaded. Retry `429` and `529` with backoff | [S7] |
| Rate limits | Set per organization, in tokens per second and requests per second. **Unverified:** the numbers appear only in the owner's console, which we can't see | [S2] |
| Cost | $0.042 per million input tokens; output is free. The vendor says it "can't prove it isn't subsidized", so the price may change | [S2][S3] |
| Cost per parse | **Estimate, unverified.** A note parse asks about 30 to 40 questions (design below). At roughly 150 tokens per question, that's about 5,000 input tokens, or **$0.0002 per parse**. Even ten times that stays under a cent. The docs don't say whether the state is billed once per call or once per question; the adapter logs `usage.input_tokens`, so the first real runs will give the true figure | [S2][S5] |
| Latency | **Vendor claim, unverified:** 70 to 500 ms end to end, measured "from our laptops on the West Coast"; one homepage demo shows 0.114 s. A secondary source reports a hosted p50 of 236 to 276 ms (unverified). Our budget is 3 s per online parse (orders README, M2), so even the slow end leaves room | [S3][S12][S15] |
| Determinism | The API has no temperature or seed setting, and the docs don't promise the same answer for the same input. So the parse cache matters, and eval scores only hold for one pinned version | [S5] |
| Offline | Hosted only. The docs mention no self-hosted or on-prem option. The offline choices are in the next table | [S3][S5] |
| Image input for bin labels | **It works, but isn't ready.** Jev 1.13 and 1.14 accept PNG or JPEG behind the `TypeSafe-Danger-Zone-Images: true` header: 280 to 1568 px per side, up to 16 MiB per image, billed as input tokens. The vendor says images have "way too much jaggedness" for production. The docs make no OCR claim, and the models page lists input as text only, so the docs disagree with each other | [S2][S8][S9] |
| Data handling | Per the legal page, the privacy policy promises not to train on user data. The DPA says data is kept "as long as necessary" and doesn't name a processing region. Zero data retention is available only to enterprise customers | [S10] |
| Known weak spots | From the vendor's own list: reads scoping words and negations literally, and handles double negatives less well; doesn't count reliably; leans toward the first option in a choice; doesn't treat the state as hostile, so injected text can shift answers; can't generate strings | [S9] |

**Offline options.**

| Option | What it gives | Catch | Source |
|---|---|---|---|
| Parse cache plus manual entry | Repeated notes parse instantly with no network. A new note with Jev down falls back to manual entry. Both are already planned (orders-05, M4) | New notes can't be auto-parsed offline | orders README |
| Self-hosted Laya through `laya-serve` | Apache-2.0 weights (ModernBERT-large, 421M parameters, 512-token context). `laya-serve` speaks the same `POST /v1/systemone` protocol, so only the base URL changes. Reported speeds: about 40 ms per question on a T4 GPU and about 0.2 to 0.5 s on a CPU | Near chance zero-shot on typed decisions (0.362, against 0.461 for always picking the most common answer), so it needs fine-tuning on our eval set and refitted temperatures. Its `confidence` uses normalized entropy, so Jev thresholds don't carry over. Every `score` level needs a description, and a choice allows at most 100 options | [S13][S14][S15] |
| Cloudflare "Clef" | Reported to be Jev-compatible and open-weights | One aggregator article, **unverified**. Not considered further | [S16] |

## Decision

This is proposed and becomes binding only when the owner answers OQ-5.

1. **Back the Jev role with TypeSafe's hosted Jev, pinned to `jev-1.14.0`** rather than an alias. Aliases move on release, and eval scores only hold for one version. `ParseResult.parser.version` records the `model` the response names.
2. **`JevClient` stays exactly as written.** The real adapter goes in `kitchen/orders/jev.py`, as below. `FakeJev` remains what tests and CI use, and no test reaches the network.
3. **Offline story for M4: parse cache plus manual entry.** Self-hosted Laya is a fallback we won't build unless the owner asks for offline parsing of new notes.
4. **Bin labels: text first.** Photos sit behind a config flag that is off by default. A profile made from a photo always goes to the chef for confirmation.
5. **Model confidence can only add questions, never remove one.** A deterministic keyword floor keeps the "never drop a mention" rule even if Jev misses an allergen.

### Real adapter design (`kitchen/orders/jev.py`, a later PR)

```python
class TypeSafeJev:
    """JevClient backed by TypeSafe's hosted Jev, or any server that speaks POST /v1/systemone."""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_s: float, mention_floor: float): ...

    def parse_note(self, note: str, *, source: Literal["typed", "voice"]) -> ParseResult: ...

    def profile_bin(self, *, label_text: str | None = None, image_jpeg: bytes | None = None,
                    prep_sheet_text: str | None = None) -> AllergenProfile: ...
```

Each method is three plain steps: build the questions (a pure function), make one HTTP call, and map the answers to contract fields (a pure function). The pure parts can be tested without the network.

**Questions for one note** (all sent in one request, the vendor's "fan-out" pattern [S6]):

| Question name | Type | What it asks | Fills |
|---|---|---|---|
| `avoid_<unit>` (one per allergen unit, 9) | `noul` | Does this note say the guest must avoid `<unit name>`, or foods made with it? | whether a constraint exists, and `confidence.allergen_unit_id` |
| `severity_<unit>` (9) | `choice`: `anaphylactic`, `allergy`, `intolerance`, `preference`, `not_stated` | How serious does the note say the reaction to `<unit name>` is? | `severity` |
| `traces_<unit>` (9) | `noul` | Does the note say that even traces or cross-contact of `<unit name>` matter? | `trace_sensitive` |
| `exception_<unit>` (only units with candidate words) | `choice`: candidate spans from the note, plus `none` | Which of these does the note say is fine to serve despite the `<unit name>` restriction? | `exceptions[].ingredient` |

- The note goes only in `state`. Instructions are fixed strings in our code, so a note can't rewrite the questions. Injected text could still lower a probability, which the keyword floor and question threshold catch, and the eval set should include such notes.
- Jev can't write strings, so the exception ingredient is a word copied from the note. Code finds candidate spans (words after "fine", "ok" or "except"), and Jev only picks one or `none`. This is the vendor's pre-parsed extraction pattern [S6].
- Severity options run from most to least severe, so Jev's lean toward the first option [S9] errs toward the stricter severity and a higher `pass_threshold`.

**Per-field confidence signal Jev can give, and how we map it:**

| Field | Value | Confidence written to `ParseResult` | Why |
|---|---|---|---|
| `allergen_unit_id` | A constraint exists if the `avoid_<unit>` probability ≥ `mention_floor`, **or** the keyword matcher finds the unit | The `noul` probability itself | `noul` has no separate confidence [S8]; its probability *is* how likely the constraint is to be real. A keyword hit with a low probability gets that low confidence, so it becomes a question |
| `severity` | The chosen option | `choice.confidence`, which is 0 when probability is spread evenly and never above the top probability [S8]. `not_stated` gets confidence 0 | Severity sets `pass_threshold` (OQ-4), so a guessed severity could loosen the gate. If the note doesn't say, we ask |
| `trace_sensitive` | `true` if the probability ≥ 0.5 | `abs(2 * p - 1)`, the vendor's suggested confidence for a yes/no answer [S8] | A probability near 0.5 means Jev is unsure, and that becomes a question |
| `exceptions[].ingredient` | The chosen span; `none` means no exception | `choice.confidence` | Exceptions narrow only on explicit words. Below the threshold, they become a question (team rule) |
| `profile_bin` probabilities | For each unit, the larger of the label-level `contains_<unit>` probability and the highest per-ingredient probability | Not a field. Unclear ingredients go to the chef instead | Taking the larger value leans toward "carries the allergen" |

The vendor doesn't show that these probabilities are calibrated [S8]. So `question_threshold` comes from the orders eval set (orders-02, where `wrong_without_question` must be 0), not from vendor guidance.

**Keyword floor.** The adapter also runs the deterministic keyword matcher that `FakeJev` uses. Any unit it finds becomes a constraint even when Jev scores it below `mention_floor`, and its confidence is Jev's probability, so it becomes a question. Jev can add constraints the keywords miss. It can never remove one they found.

**`profile_bin`.**
- Code splits label or prep-sheet text into ingredients, on commas, semicolons, and "Contains:" or "May contain:" lines.
- For each ingredient, a `choice` asks which unit it is: the 9 units, plus `none` and `unclear`. For each unit, a label-level `noul` asks whether the label says the bin contains or may contain it.
- An ingredient answered `unclear`, or with confidence below the threshold, goes into `unclear_ingredients`, which opens a chef confirmation.
- The profile has `source: "jev"` and `confirmed_by_chef: false`.
- `image_jpeg` is sent only when `ORDERS_JEV_IMAGES=true`. Without that flag, a photo-only request raises `JevUnavailable`, and the person falls back to manual entry. A profile made from a photo always opens one chef confirmation for the whole label.

**Failures (invariant 1).**
- Any of these raises `JevUnavailable`, with the cause chained: a transport error, a timeout, a non-200 status, a response missing any question we asked, or output that fails the `parse_result` or `allergen_profile` schema.
- The adapter never returns a partial parse.
- One attempt, plus one retry on `429` or `529`, all inside a 2.5 s total budget, so `/v1/parse` stays under 3 s. The SDK's default 30 s retry budget [S5] is too long for us.
- A missing key fails at startup with a message naming `JEV_API_KEY`, and the issue goes to `status:needs-human` (AGENTS.md §6, data and secrets).

**Config.**
- The key comes from `JEV_API_KEY`, which is already in `.env.example`.
- Typed settings use the `ORDERS_` prefix: `ORDERS_JEV_MODEL=jev-1.14.0`, `ORDERS_JEV_BASE_URL=https://api.typesafe.ai`, `ORDERS_JEV_TIMEOUT_S=2.5`, `ORDERS_JEV_MENTION_FLOOR=0.05` and `ORDERS_JEV_IMAGES=false`.
- All settings are logged at startup; the key never is.

**HTTP client.** A plain `httpx` POST: one endpoint, one JSON shape, and our own timeout. The official `typesafe-sdk` would also work, but it brings its own retry policy that we'd have to override. Adding the dependency to the root `pyproject.toml` happens in the adapter PR, labeled `cross-team` for platform.

**Tests for that PR.**
- Recorded responses with no keys in them, replayed through `httpx.MockTransport`, so no test touches the network.
- `safety` tests: timeout, `5xx`, malformed body and a missing answer each raise `JevUnavailable`.
- A `hypothesis` property: for any Jev answers, every unit the keyword matcher finds ends up as a constraint or a question.
- The eval scorer (orders-02) runs against real Jev only once the owner provides a key, and its output is posted on the PR.

**Switching to Laya later** means the same adapter with `ORDERS_JEV_BASE_URL` pointed at `laya-serve`, after fine-tuning, and with thresholds re-tuned because its confidence formula differs.

## Consequences

- **Easier:** one HTTP call per parse returns typed answers, with no free text to parse. It costs fractions of a cent, and an open-weights server speaks the same protocol if we ever need it.
- **Harder:** we depend on early access, a vendor key and a young API. Releases come weeks apart, aliases move, and the formal deprecation policy isn't published yet [S9]. So we pin the version and re-run the eval set on every version bump.
- **Watch:** the vendor hasn't shown its confidence numbers are calibrated, so thresholds must come from our eval set. The docs disagree on image support. Rate limits stay unknown until the owner reads the console. And the guest's allergy details leave our network.
- **Enforced by** (in later PRs): the `safety` tests and property test above, the eval scorer's `wrong_without_question = 0` gate, and the rule that tests never touch the network.

## Questions for the owner (OQ-5)

1. **Is TypeSafe's Jev the "Jev" you meant?** If so, approve it as the backend, pinned to `jev-1.14.0`. *Recommendation: yes.*
2. **Key:** request early access at `console.typesafe.ai`, then put the key in your local `.env` as `JEV_API_KEY`. Agents never see it.
3. **Budget and limits:** we estimate under a cent per parse. Set a monthly cap (OQ-15), and tell us your organization's rate limits from the console.
4. **Data:** ticket notes carry allergy details (but no guest names) to a hosted API whose processing region isn't stated. Is that acceptable for the demo? Zero data retention is enterprise only.
5. **Offline (M4):** is cache plus manual entry enough, or do you want self-hosted Laya, which needs fine-tuning work? *Recommendation: cache plus manual entry.*
6. **Label photos:** keep them experimental and use text labels for the demo? *Recommendation: yes.*

## Sources

All read on 2026-10-23.

- [S1] TypeSafe docs, introduction: https://docs.typesafe.ai/introduction
- [S2] TypeSafe docs, models (versions, aliases, price, context, rate limits): https://docs.typesafe.ai/models
- [S3] TypeSafe launch post, "Introducing System One models and Jev" (latency, price, early access, measurement setup): https://typesafe.ai/blog/introducing-system-one-models-and-jev
- [S4] TypeSafe docs, quickstart (endpoint, auth, SDK, example response): https://docs.typesafe.ai/introduction/quickstart
- [S5] TypeSafe docs, `POST /v1/systemone` reference, plus the Python SDK client, retries and exceptions pages: https://docs.typesafe.ai/api/system-one, https://docs.typesafe.ai/sdk/python/api/clients/sync, https://docs.typesafe.ai/sdk/python/api/retries, https://docs.typesafe.ai/sdk/python/api/exceptions
- [S6] TypeSafe docs, fan-out pattern and pre-parsed value extraction cookbook: https://docs.typesafe.ai/patterns/fan-out, https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook
- [S7] TypeSafe docs, request limits and status codes: https://docs.typesafe.ai/api/errors
- [S8] TypeSafe docs, confidence and images: https://docs.typesafe.ai/confidence, https://docs.typesafe.ai/images
- [S9] TypeSafe, Jev 1.14 post and the Jev 1.13 known-weaknesses page: https://typesafe.ai/blog/jev-1-14, https://docs.typesafe.ai/model-jaggedness/jev-1.13
- [S10] TypeSafe legal page and DPA: https://docs.typesafe.ai/legal, https://typesafe.ai/legal/data-processing
- [S11] Vercel AI Gateway model page for `typesafe-ai/jev`: https://vercel.com/ai-gateway/models/jev
- [S12] TypeSafe homepage (demo timing): https://typesafe.ai
- [S13] Laya model card (checkpoints, license, accuracy, latency): https://huggingface.co/convaiinnovations/laya
- [S14] Laya code and `laya-serve` README (wire compatibility and its differences): https://github.com/NandhaKishorM/laya
- [S15] Secondary review of Laya, with third-party Jev latency figures (unverified): https://andrew.ooo/posts/laya-review-open-weight-jev-alternative-system-1-decision-model/
- [S16] Dealroom news item on Cloudflare Clef (single secondary source, unverified): https://dealroom.co/news/158476-cloudflare-launches-clef-as-typesafes-decision-model-idea-spreads-across/
