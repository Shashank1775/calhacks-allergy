# Architecture

This is the system spec. Every team builds against it. If your work needs it to say something it does not, open a `type:rfc` issue (see [agents/WORKFLOW.md](agents/WORKFLOW.md#contract-changes-rfcs)); do not quietly diverge.

## What the system does

A kitchen camera watches food prep. Every bin, tool, hand and plate is tracked, and every contact between them is logged as a time-stamped edge in a provenance graph. When a plate reaches the pass, the system works out which allergens could have reached that plate and compares them with what the ticket forbids. If there is any doubt, the plate is held.

## Non-negotiable invariants

These hold across every team. A PR that breaks one is rejected, whatever else it does. Tests that guard them carry the `safety` marker.

1. **Fail closed.** A missing input, an error, a timeout, a stale track or an unknown entity history produces **HOLD**, never PASS.
2. **Models only tighten.** The verdict is computed in plain deterministic code. Jev, Laya and the LSTM can add reasons to hold; they can never remove one. `hold = exact_flags or model_flags or confidence < ticket.pass_threshold or any_error`.
3. **No unanswered questions.** A ticket whose parse has a field below the confidence threshold cannot be sent until a person answers it.
4. **Tickets store forbidden allergens, not forbidden contacts.** The graph decides which contacts matter.
5. **Time runs forward.** A contamination path is only valid if its edges are in increasing time order.
6. **The interaction classifier is allergen-blind.** Interaction feature vectors carry no allergen fields. Allergens are applied by the graph, never learned by the classifier.
7. **Rules stay as the fallback.** The LSTM replaces zone rules only after it beats them on held-out clips, and zone rules keep running when the LSTM is unavailable.
8. **Clean only from a known-clean source.** Tools, hands and plates are clean when the shift starts. After that, an entity starts clean only if it is first seen leaving a designated `clean_stack` zone (the clean-plate stack or clean-utensil rack). Any other entity that first appears mid-shift, including a hand that walks into frame or a track that was lost and re-acquired under a new ID, has **unknown** history. It is treated as possibly carrying every allergen until a wash resets it.
9. **Late changes recheck everything.** Amending a ticket re-runs the verdict against the plate's full history.

## Pipeline

```
 SERVER TABLET            SHIFT START                      PI (camera + Hailo-8)
 typed / voice            prep sheets, bin labels          video frames
      │                        │                                │
      ▼                        ▼                                ▼
 ┌──────────┐  Jev       ┌────────────┐  Jev            ┌─────────────┐
 │ intake:  │──────────▶ │ intake:    │                 │ perception  │ YOLOv8 det+pose
 │ parse    │ questions  │ profiling  │ chef confirms   │             │ ByteTrack
 └────┬─────┘  answered  └─────┬──────┘ "unclear" items └──────┬──────┘
      │ apply                  │                               │ ObservationFrame (30 FPS, in memory)
      ▼                        ▼                               ▼
 ┌──────────────────────────────────────────────────┐   ┌─────────────┐
 │ state: SpacetimeDB                               │◀──│ interactions│ candidate pairs → feature vectors
 │ allergen_unit, ticket, ticket_constraint,        │   │             │ zone rules  |  LSTM
 │ entity (≈5 Hz), interaction (edges), verdict,    │   └─────────────┘ InteractionEvent → edge
 │ verdict_step, mistake_pattern                    │
 └───────┬──────────────────────────────▲───────────┘
         │ plate enters the pass         │ verdict + chain
         ▼                               │
 ┌──────────────────────────────────────────────────┐
 │ provenance: plate subgraph → exact reachability  │   Laya (fine-tuned on sim kitchens)
 │ (noisy-OR, washes reset, time-forward) + Laya    │◀── trained/evaluated by sim
 │ → verdict policy (plain code, fail closed)       │
 │ → repeated-mistakes analysis                     │
 └───────┬──────────────────────────────────────────┘
         ▼
 ┌──────────────────────────────────────────────────┐
 │ experience: pass gate (servo, red/green, buzzer),│
 │ Cartesia Sonic voice ("Hold seven"), expo board, │
 │ camera overlays, server tablet UI, chef confirm  │
 └──────────────────────────────────────────────────┘
```

## Stages

### 1. Order intake — team `intake`

- **Input.** The server types the ticket note, or speaks it and Cartesia Ink transcribes it.
- **Parse.** Jev reads the note and returns typed constraints: allergen units to avoid, severity, whether trace contact matters, and exceptions (for example "coconut is fine"). Every field carries a confidence. Contract: [`parse_result`](../contracts/schemas/parse_result.schema.json).
- **Clarify.** Fields below the confidence threshold become one-tap questions. The ticket cannot be sent until they are answered (invariant 3).
- **Cache.** Parse results are cached by normalized note text, so a repeated note works offline after its first parse.
- **Apply.** On send, constraints are written to SpacetimeDB: ticket ID, allergen unit ID, severity, trace sensitivity, confidence. Contract: [`ticket`](../contracts/schemas/ticket.schema.json).

### 2. Entity profiling — team `intake`

- At shift start, Jev reads each bin's label or prep sheet and assigns allergen units with probabilities. Contract: [`allergen_profile`](../contracts/schemas/allergen_profile.schema.json).
- Each profile is stored on the bin's entity in SpacetimeDB. Tools, hands and plates start clean.
- Ingredients Jev marks unclear become a one-time chef confirmation.
- Optional: an Apify Actor imports the restaurant's online menu to seed prep sheets.

### 3. Perception (on the Pi) — team `perception`

- **Detection.** Pretrained YOLOv8 detection plus pose on the Hailo-8, target 30+ FPS. Hands come from pose wrists. Plates are bowls or are found at the plating spot. Demo tools are spoons, forks and knives.
- **Tracking.** ByteTrack (Kalman filter built in) turns detections into observation frames with stable track IDs. Contract: [`observation_frame`](../contracts/schemas/observation_frame.schema.json).
- **Identity.** Bins are identified by taped slot. A plate is bound to a ticket at the plating spot when the cook taps "start ticket". Perception records whether each new entity was first seen leaving a `clean_stack` zone.
- **Entity store.** Every frame is kept in memory on the Pi. SpacetimeDB is updated about 5 times a second. Fields: ID, kind, label, zone, box, last-seen time, allergen profile. Contract: [`entity`](../contracts/schemas/entity.schema.json).

### 4. Interactions — team `interactions`

- **Candidates.** Only nearby or overlapping pairs are considered: hand–tool, tool–bin, tool–plate, hand–bin, hand–plate.
- **Features.** Per pair, per frame: distance, overlap, inside-zone flag, speeds, dwell time, entity kinds, wrist proximity. No allergen fields (invariant 6). Contract: [`interaction_features`](../contracts/schemas/interaction_features.schema.json).
- **Classification.** Actions: `transfer.contact`, `transfer.scoop`, `transfer.wipe`, `clean.wash`, `unimportant`, each with a probability. Zone rules run first and stay as the fallback; an LSTM over the feature sequence replaces them only if it beats them on held-out clips (invariant 7).
- **Output.** Each non-`unimportant` interaction is written to SpacetimeDB as an edge. Contract: [`interaction_event`](../contracts/schemas/interaction_event.schema.json).

### 5. Provenance graph — team `provenance`

- Entities are nodes; interactions are time-stamped edges.
- Allergen probabilities move along transfer edges by noisy-OR: for allergen *a* crossing an edge with probability *p* and action efficiency *k*, `P_dst(a) ← 1 − (1 − P_dst(a)) · (1 − k · p · P_src(a))`.
- A wash with probability *p* resets: `P(a) ← (1 − p) · P(a)` for every *a*.
- Edges are processed in time order, so every path is time-forward (invariant 5).
- Entities first seen mid-shift start at the "unknown" profile unless they came from a `clean_stack` zone (invariant 8).
- A `clean.wash` event is a self-loop on the washed entity (`a_id == b_id`).
- Edges with equal timestamps are ordered by a rule in the provenance team's ADR (OQ-17).

### 6. Final analysis — team `provenance` (Laya model trained by `sim`)

Triggered when a plate enters the pass zone, and again whenever its ticket is amended.

1. **Exact reachability** walks the plate's time-forward ancestors and computes the probability of each allergen reaching it.
2. **Laya** reads the same subgraph, answers which allergens reached the plate, and walks the chain back step by step. Contract: [`laya_request`](../contracts/schemas/laya_request.schema.json) / [`laya_response`](../contracts/schemas/laya_response.schema.json).
3. **Verdict policy** (plain code): HOLD if either flags an allergen the ticket forbids, if confidence is below the ticket's threshold, or if anything failed. Contract: [`verdict`](../contracts/schemas/verdict.schema.json).

### 7. Outputs — team `experience` (Repeated Mistakes analysis by `provenance`)

- **Pass gate.** Servo arm, red or green light, buzzer, and a Cartesia Sonic voice line ("Hold seven").
- **Expo board.** A SpacetimeDB client showing each held plate's chain with a confidence per step, and camera overlays coloring each entity by allergen status.
- **Repeated Mistakes.** Recurring contamination paths grouped by source, tool type and allergen, each with a suggested fix ("bin-4 keeps contaminating knives; give it its own utensil"). Contract: [`mistake_pattern`](../contracts/schemas/mistake_pattern.schema.json).
- **Server tablet and chef screens.** Ticket entry, one-tap questions, and chef confirmation of unclear ingredients. The screens belong to `experience`; the logic behind them belongs to `intake`.

## Boundaries between teams

Teams talk only through the contracts in [`/contracts`](../contracts/README.md):

| Boundary | Producer → consumer | Medium | Contract |
|---|---|---|---|
| Parse a note | intake → experience (tablet UI) | HTTP, intake service | [`contracts/apis.md`](../contracts/apis.md#intake-http-api), `parse_result` |
| Tickets and constraints | intake → provenance, experience | SpacetimeDB `ticket`, `ticket_constraint` | `ticket` |
| Bin profiles | intake → provenance, experience | SpacetimeDB `entity.allergen_profile` | `allergen_profile`, `entity` |
| Observation frames | perception → interactions | in-process on the Pi; JSONL for replay | `observation_frame` |
| Entities | perception → everyone | SpacetimeDB `entity` (≈5 Hz) | `entity` |
| Interaction features | interactions (internal); sim → interactions for training | JSONL | `interaction_features` |
| Edges | interactions → provenance | SpacetimeDB `interaction` | `interaction_event` |
| Laya | provenance ↔ Laya model (sim owns training) | `LayaClient` interface | `laya_request`, `laya_response` |
| Verdicts and chains | provenance → experience | SpacetimeDB `verdict`, `verdict_step` | `verdict` |
| Mistake patterns | provenance → experience | SpacetimeDB `mistake_pattern` | `mistake_pattern` |
| Golden scenarios | sim → everyone | files in `contracts/scenarios/` | `scenario` |
| Database access from Python | state → intake, perception, interactions, provenance | `StateClient` interface | [`contracts/apis.md`](../contracts/apis.md#stateclient-python-interface) |

Every boundary has a **fake**. Every team must be able to build and test with every other team's component replaced by its fake. The team that owns the producer side owns the fake (see each team's charter).

## Deployment (proposed, see open questions)

| Host | Runs |
|---|---|
| Raspberry Pi + Hailo-8 + camera | perception, interactions, pass gate driver |
| Laptop on the same LAN | SpacetimeDB (local standalone), intake service, provenance service, Laya inference, expo board and tablet web apps |

## Timebase and IDs

- `t_ms`: integer milliseconds since the shift's start, monotonic. Every internal timestamp uses it. The shift row records the wall-clock start.
- Entity IDs are stable strings: `bin-4`, `tool-knife-17`, `hand-p1-left`, `plate-t7`. A re-acquired track gets a **new** ID (invariant 8).
- Ticket IDs are strings; tickets also carry a short spoken `number` ("Hold seven").
- Boxes are normalized `[x1, y1, x2, y2]` in 0–1 frame coordinates.

## Performance budgets (proposed)

| Stage | Budget |
|---|---|
| Detection + pose + tracking on the Pi | ≥ 30 FPS |
| Entity sync to SpacetimeDB | ≈ 5 Hz |
| Interaction edge latency (event end → edge in DB) | ≤ 500 ms |
| Verdict latency (plate enters pass → gate moves) | ≤ 1 s with Laya, ≤ 200 ms rules-only |
| Ticket parse (online) | ≤ 3 s; cached ≤ 50 ms |

If a model that the current mode expects (for example Laya in `full` mode) errors or blows its budget, the verdict is **HOLD** with reason `model_unavailable` (invariant 1). Running without a model is an explicit mode a person switches on (`rules_only`), and every verdict records the mode it ran in.
