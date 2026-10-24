# Glossary

| Term | Meaning |
|---|---|
| **Allergen unit** | One row of the allergen table, e.g. `peanut`. Currently the nine major US allergens (see `contracts/allergen_units.json`, OQ-2). |
| **Ticket** | A guest's order note plus its typed constraints. Has a short spoken `number` and a `revision` that increases with each amendment. |
| **Constraint** | One forbidden allergen unit on a ticket, with severity, trace sensitivity and confidence. |
| **Exception** | An ingredient the guest says is fine despite its unit, e.g. coconut under tree nuts. |
| **Trace sensitive** | Whether incidental contact counts, as opposed to only direct ingredients (OQ-8). |
| **Question threshold** | Parse confidence below which a field becomes a one-tap question (`parse_result.question_threshold`). |
| **Pass threshold** | Minimum clean-confidence a plate needs to pass (`ticket.pass_threshold`). |
| **Clean-confidence** | How sure the system is that a plate carries none of the ticket's forbidden allergens. Defined in the provenance team's ADR. |
| **Entity** | A tracked bin, tool, hand or plate. Graph node. |
| **Interaction / edge** | A classified contact between two entities at a time: transfer (contact, scoop, wipe), clean (wash), or unimportant. |
| **Provenance graph** | Entities as nodes and time-stamped interactions as edges, for one shift. |
| **Noisy-OR** | How probabilities combine along edges: an allergen reaches a node unless every incoming path fails to carry it. |
| **Wash reset** | A `clean.wash` edge multiplies an entity's allergen probabilities by `(1 − p_wash)`. |
| **Time-forward path** | A chain of edges in increasing time order. Only these can carry allergens. |
| **Unknown history** | An entity first seen mid-shift and not from a `clean_stack` zone. Treated as possibly carrying everything. |
| **Clean stack** | A zone (clean-plate stack, clean-utensil rack) whose entities start clean when first seen mid-shift. |
| **Pass** | The counter where finished plates leave the kitchen. Plates are judged here. |
| **Expo** | The expediter's station. The expo board shows held plates and their chains. |
| **Plating spot** | Where a plate is bound to its ticket when the cook taps "start ticket". |
| **Verdict** | PASS or HOLD for one plate against one ticket revision, with reasons and the contamination chain. |
| **Mode** | `full` (exact reachability plus Laya) or `rules_only` (exact only, switched on by a person). |
| **Fail closed** | On any error or doubt, HOLD. |
| **Jev** | The model role that parses ticket notes and reads bin labels and prep sheets. What backs it: OQ-5. |
| **Laya** | The model role that reads a plate's subgraph and names the allergens that reached it, with chains. Fine-tuned on simulated kitchens. What backs it: OQ-5. |
| **Zone rules** | Hand-written interaction classifier. Always available; the fallback. |
| **LSTM** | Learned interaction classifier over feature sequences. Adopted only if it beats zone rules on held-out clips. |
| **ByteTrack** | Multi-object tracker that turns per-frame detections into stable track IDs. |
| **Hailo-8** | The AI accelerator on the Pi that runs YOLOv8 detection and pose. |
| **SpacetimeDB** | The database that holds all shared state, and that clients subscribe to. |
| **Fake** | A stand-in implementation of a contract, owned by the producing team, used by consumers' tests. |
| **Golden scenario** | A small hand-checked kitchen story with a known correct verdict, in `contracts/scenarios/`. |
| **`t_ms`** | Milliseconds since shift start. The only timebase inside the system. |
