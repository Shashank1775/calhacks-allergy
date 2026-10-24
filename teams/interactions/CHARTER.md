# Team charter: `interactions`

> Owned by the interactions Manager. Changes go through the Director. Task-level detail lives in issues labeled `team:interactions`.

## Mission

Turn tracked entity pairs into a time-stamped stream of classified interactions (`transfer.contact`, `transfer.scoop`, `transfer.wipe`, `clean.wash`), each with an honest probability, using only allergen-blind evidence. Missing a transfer or inventing a wash makes a verdict looser, so when in doubt we favor recall for transfers and caution for washes.

## Owns

- Paths: `components/interactions/` (see [OWNERS](../../OWNERS)).
- Pipeline stage 4 in [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#4-interactions--team-interactions): candidate pairs, feature vectors, classification and event segmentation.
- Writer of the SpacetimeDB `interaction` table ([state-tables.md](../../contracts/state-tables.md#writers)).
- The zone-rule classifier (always available) and the LSTM (adopted only after it wins a frozen benchmark).
- The labeled clip dataset format, the labeling tool, the train/held-out split policy, and the benchmark (OQ-12).
- Edge direction policy (OQ-9), jointly with provenance.

## Does not own

- Detection, tracking, entity IDs and zone polygons: **perception**.
- What an edge does to allergen probabilities (noisy-OR, wash resets): **provenance**.
- Synthetic event streams for Laya training: **sim**. We may consume their synthetic features for LSTM pre-training.
- Recording real kitchen clips: **owner** (OQ-12). We provide the format and the tooling.

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|
| [`interaction_event`](../../contracts/schemas/interaction_event.schema.json) via `StateClient.record_interactions`, and as JSONL | provenance, experience, platform e2e | Recorded event JSONL fixture pack |
| [`interaction_features`](../../contracts/schemas/interaction_features.schema.json) JSONL windows | our own training, sim | Feature JSONL fixtures |
| `InteractionClassifier` implementations `zone_rules` and `lstm` ([apis.md](../../contracts/apis.md#classifier)) | our pipeline | `zone_rules` is the reference implementation |
| Labeled clip dataset format and benchmark report | owner, Director (adoption ADR) | Tiny labeled sample set |

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|
| [`observation_frame`](../../contracts/schemas/observation_frame.schema.json), in-process or JSONL | perception | Our hand-written frame fixtures first, then perception's recorded pack |
| Zone kinds from calibration | perception | Sample zone file |
| `StateClient` | state | `InMemoryStateClient` (platform) |
| `Clock` and generated models (`py_common`) | platform | `FakeClock` |

## Invariants we guard

| Invariant | How our code can break it | Guard (pytest, marked `safety`) |
|---|---|---|
| 6, allergen-blind classifier | A feature, or a lookup inside the classifier, that reveals which bin holds which allergen | Features validate against the schema (`additionalProperties: false`). `zone_kind` is used, never the zone ID. An import-lint test proves the pipeline never reads `bin_profile`, `entity_risk` or allergen data. A property test shows the output doesn't change when bin profiles change |
| 7, rules stay the fallback | The LSTM path failing silently, or replacing the rules without evidence | `FallbackClassifier` falls back to `zone_rules` on load failure, exception or timeout, and each event names the classifier that produced it. The LSTM is adopted only by an ADR backed by the frozen held-out benchmark |
| 1, fail closed | Dropping low-confidence transfers, emitting a wash too eagerly, or losing events while the DB is down | Transfers above a low floor are emitted with their honest probability, and provenance decides. `clean.wash` requires the wash zone, minimum dwell and a high probability. Events are buffered and retried on `StateUnavailable`, never dropped silently |
| 5, time runs forward | Bad segmentation (`t_end_ms < t_start_ms`) or out-of-order emission | Segmenter tests. Events are emitted in `t_start_ms` order per pair |

## Edge direction (OQ-9, default until the ADR)

| Action | Direction |
|---|---|
| `transfer.contact`, `transfer.wipe` | `both` |
| `transfer.scoop` | bin → tool, i.e. `b_to_a` when `a` is the tool and `b` is the bin |
| `clean.wash` | self-loop: `a_id == b_id` is the washed entity; `direction` is ignored |

## Stack and layout

- Python with numpy. PyTorch for the LSTM. Training runs on the laptop or a GPU; inference runs on the Pi CPU, as ONNX if needed, within the edge-latency budget.
- Feature units: distances in normalized frame units, speeds in normalized units per second, dwell in ms. This keeps features stable across FPS changes.
- Layout under `src/interactions/`: `candidates.py`, `features.py`, `segment.py`, `classifiers/` (`zone_rules.py`, `lstm.py`, `fallback.py`), `pipeline.py`, `io_jsonl.py`. Separately: `training/` (dataset, train, eval, benchmark) and `labeling/`.
- Test command: `uv run --package interactions pytest components/interactions`.

## Deliverables by milestone

| Milestone | We deliver |
|---|---|
| M0 | Hand-written frame fixtures with expected events. Candidate pairs plus allergen-blind features. Zone-rule classifier v0, frame JSONL → events (interactions-01). Edge direction and wash ADR. Recorded event fixture pack used by provenance |
| M1 | `FallbackClassifier`. Streaming pipeline writing through `StateClient` within ≤ 500 ms of event end. Rules re-tuned on perception's recorded pack. Dataset format and validator. Labeling tool v0. Benchmark harness. Feature-window export |
| M2 | Live on the Pi at the edge-latency budget. Rules tuned on owner recordings. Wash detection checked at the real wash station (`hardware`) |
| M3 | Labeled dataset from owner clips with a session-level split. LSTM trained. Frozen held-out benchmark against zone rules. Adoption ADR: adopt only if it wins on the cost-weighted score |
| M4 | Drills: LSTM crash, slow inference and DB outage all fall back to rules or a buffer, never to silence. Regression suite frozen for the demo |

## Working with other teams

- **perception:** frame shape, how hands are represented, zone kinds, and the fixture pack. We start without them on hand-written frames.
- **provenance:** edge direction, the wash convention, and what a probability means (P that the action happened). They own propagation; we own detection.
- **sim:** synthetic feature windows for pre-training, and realism feedback.
- **state:** `record_interactions` batching and idempotent `event_id`s.
- **Owner:** clip recording and labeling time (OQ-12).

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Too little real labeled data, so the LSTM never beats the rules | That's an acceptable outcome. The rules ship, and the benchmark makes the call (invariant 7) |
| 2D overlap ≠ 3D contact; occlusion blurs contact vs near-miss | Overhead camera. Recall bias for transfers; holds are the safe error |
| False `clean.wash` (most dangerous: it erases contamination) | Wash zone plus dwell plus high threshold, and false washes weighted heaviest in the benchmark |
| Train/held-out leakage | Split by recording session, never by clip. The held-out manifest is hashed and frozen |
| FPS jitter on the Pi distorts speed and dwell | Time-based units. Tests at 15, 30 and 60 FPS |
| Wash events have no partner entity | Self-loop convention, pending an RFC to document it in the schema |

## Definition of done (team additions)

- Every emitted event validates against `interaction_event`. Every feature row validates against `interaction_features`.
- Changes to rules thresholds, the fallback path or wash logic carry `safety-critical` and include benchmark numbers before and after.
- Rule constants live in one config object, are logged at startup, and are reported in `classifier.version`.

## Staffing

- M0: 2 Workers (fixtures plus rules; features plus ADR).
- M1: 3 Workers (pipeline and fallback; dataset and labeling; benchmark).
- M3: 2–3 Workers.
- The fallback and wash logic suit one careful Worker. Fixtures, tooling and export parallelize well.
