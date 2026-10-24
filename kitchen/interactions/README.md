# kitchen/interactions: Who touched what

**Owner:** team `interactions` (agents). The LSTM model itself lives in [`kitchen/lstm/`](../lstm/README.md) and belongs to the partner. This folder runs it, falls back from it, and benchmarks it.

## Pipeline step

Turn tracked entity pairs into time-stamped, classified interactions (the edges of the provenance graph), using only allergen-blind evidence.

## What happens here

- Pick candidate pairs that are near or overlapping: hand–tool, tool–bin, tool–plate, hand–bin, hand–plate.
- Compute features per pair per frame: distance, overlap, inside-zone flag, zone kind, speeds, dwell, wrist proximity. No allergen fields, ever (invariant 6).
- Classify each pair's feature window as `transfer.contact`, `transfer.scoop`, `transfer.wipe`, `clean.wash` or `unimportant`, with a probability. Zone rules always run. The partner's LSTM is used only after it wins the benchmark (invariant 7).
- Segment per-frame labels into `InteractionEvent`s, and write them through `StateClient.record_interactions` within 500 ms of each event's end.
- Own the labeled clip format, the labeling tool and the frozen benchmark that decides LSTM adoption, and export the feature windows the partner trains on.

## Files

| File | What it does |
|---|---|
| `candidates.py` | Candidate pairs: the five pair types, within a distance or overlap cutoff |
| `features.py` | `InteractionFeatures` per pair per frame, plus per-pair window export for LSTM training |
| `zone_rules.py` | Hand-written rule classifier, always available. All constants in one config object |
| `classifier.py` | Wrapper: uses the adopted LSTM if healthy, zone rules otherwise; every event names its classifier |
| `events.py` | Segments labels into events; streaming pipeline; writes via `StateClient` with buffer and retry; JSONL CLI |
| `labels.py` | Labeled clip dataset format, validator, frozen held-out manifest, labeling tool |
| `benchmark.py` | Frozen held-out benchmark with a cost-weighted score; compares any `InteractionClassifier` |

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| in | `ObservationFrame`, in-process or JSONL | [`observation_frame`](../../contracts/schemas/observation_frame.schema.json) | vision |
| in | Zone kinds | zone file, `StateClient.list_zones` | vision |
| in | LSTM classifier | [`InteractionClassifier`](../../contracts/apis.md#interactionclassifier) | partner (`kitchen/lstm/`) |
| out | Edges | [`interaction_event`](../../contracts/schemas/interaction_event.schema.json) via `StateClient.record_interactions` → `interaction` table | partner graph, outputs |
| out | Event JSONL fixture pack | `interaction_event` | verdict, partner graph tests, platform e2e |
| out | Feature windows JSONL | [`interaction_features`](../../contracts/schemas/interaction_features.schema.json) | partner (LSTM training), sim |
| out | Benchmark report | none | partner, Director (adoption ADR) |

Default edge direction until the ADR (OQ-9): contact and wipe go `both` ways; a scoop moves bin → tool; `clean.wash` is a self-loop (`a_id == b_id`).

## Safety rules that apply

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). Every test below is marked `safety`.

- **Invariant 6: allergen-blind classifier.** Feature rows validate against the schema (`additionalProperties: false`). Only `zone_kind` is used, never the zone ID. An import-lint test proves this folder never reads `bin_profile`, `entity_risk` or allergen data. A property test shows output doesn't change when bin profiles change.
- **Invariant 7: rules stay the fallback.** `classifier.py` falls back to zone rules when the LSTM fails to load, raises or times out (tested with `ScriptedClassifier`). Every event names its classifier. The LSTM is adopted only by an ADR backed by the frozen benchmark and co-signed by the partner.
- **Invariant 1: fail closed.** Transfers above a low floor are emitted with their honest probability, and the graph decides. `clean.wash` needs the wash zone, a minimum dwell and a high probability, so a brief pass through the wash zone is not a wash. Events are buffered and retried on `StateUnavailable`, never dropped silently.
- **Invariant 5: time runs forward.** Every event has `t_start_ms ≤ t_end_ms`. Events are emitted in `t_start_ms` order per pair. Events for a re-acquired entity use its new ID, never the old one.

## Fakes

- **We use:** our hand-written frame fixtures, then vision's recorded pack; `InMemoryStateClient`, `ScriptedClassifier` (the LSTM stand-in) and `FixedClock` (platform).
- **We ship:** the event JSONL fixture pack, feature-window fixtures, and a tiny labeled sample session.

## Tests

Tests live in `kitchen/interactions/tests/`; run them with `uv run pytest kitchen/interactions`. Feature tests also run at 15, 30 and 60 FPS resamplings of the same scene.

## Milestones

- **M0:** frame fixtures (interactions-02), candidates and features (03), zone rules v0 (01), edge direction ADR (04), event fixture pack (05).
- **M1:** classifier wrapper with fallback (06), streaming pipeline (07), rules tuned on vision's pack (08), dataset format (09), labeling tool (10), benchmark (11), feature-window export (12).
- **M2:** live on the Pi within the 500 ms budget. Rules tuned on owner recordings. Wash detection checked at the real wash station (`hardware`).
- **M3:** owner clips labeled, with the session-level held-out split frozen. Benchmark the partner's LSTM against zone rules. Adoption ADR co-signed by the partner. The LSTM becomes primary only if it wins.
- **M4:** drills: an LSTM crash, slow inference and a DB outage each fall back to rules or the buffer, never to silence. Regression suite frozen for the demo.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Too little labeled data, so the LSTM never wins | That's acceptable. Rules ship, and the benchmark makes the call |
| 2D overlap is not 3D contact | Overhead camera; bias toward recall for transfers (holds are the safe error) |
| A false `clean.wash`, the most dangerous error because it erases contamination | Wash zone plus dwell plus a high threshold; heaviest weight in the benchmark |
| Leakage between train and held-out | Split by recording session, never by clip. Hashed, frozen held-out manifest |
| FPS jitter distorts speed and dwell | Time-based units; tests at 15, 30 and 60 FPS |
| Recording and labeling time (OQ-12) | Owner records; we supply the format, tool and recording guide |
| Edge direction undecided (OQ-9) | Defaults above until the ADR |

Open questions: [AGENTS.md §11](../../AGENTS.md#11-open-questions).
