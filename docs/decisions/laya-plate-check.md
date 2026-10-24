# A second Laya model makes the final plate check, stricter only

- **Status:** accepted
- **Date:** 2026-10-24
- **Decided by:** owner

## Context

The owner wants Laya to make the final call on whether a plate goes out, as well as parsing tickets. The two jobs are very different (reading a server's note versus reading the provenance graph), so they get separate fine-tuned models. A wrong PASS can hurt a guest, so whatever Laya says must not be able to send a plate the plain-code check would hold.

## Decision

- **Two models.** The ticket-note model stays in `kitchen/orders/laya_parser.py`. The plate model is `kitchen/verdict/laya_check.py`, fine-tuned from `convaiinnovations/laya` on pina1.
- **Input.** The partner's graph gives the plate's subgraph: `ProvenanceGraph.subgraph(plate_id, as_of_t_ms)` returns the plate's time-forward ancestry (`contracts/schemas/plate_subgraph.schema.json`). The check turns it into short text: each entity and what it carried at the start, then each interaction in time order.
- **Question.** For each forbidden allergen: "Following the events in time order, could X have reached the plate?" The answer is P(reached).
- **Stricter only.** Each forbidden allergen at P ≥ 1 − `pass_threshold` adds a `laya_flagged` hold reason, the same flag rule the verdict applies to the graph's numbers. A failure, a malformed answer, or a subgraph over 20 edges adds `laya_unavailable`. Laya can never remove a hold reason or cause a PASS.
- **Training data.** Generated, not hand-labeled: random small kitchens (bins from a fixed pool, tools, hands, a plate, sometimes a tool with unknown history), stories that carry an allergen to the plate, wash it off first, touch the plate too early, or stay clean, plus a few off-path edges. Labels are exact noisy-OR with full transfer (k = 1). Best case only, as the owner asked: short histories, clear probabilities.

## Consequences

- The verdict needs the plate model running. If it's down, every plate holds. The owner chose not to plan for an outage in a controlled demo.
- Laya and the graph can disagree. Disagreeing toward HOLD costs a remake; disagreeing toward PASS can't happen.
- `ProvenanceGraph.subgraph` is a partner interface change and needs the partner's OK.
- Tests: `kitchen/verdict/tests/` checks that no Laya answer removes a reason, that a failure holds, and that the fake matches every golden scenario.
