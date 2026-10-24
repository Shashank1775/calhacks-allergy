# kitchen/interactions: Who touched what (partner)

**Owner:** the partner (a human). The first version was written by an agent at the owner's request, on a `partner/` branch. This README says what the step does, what the agent teams can rely on, and how to run it.

## What this step does

It turns tracked entity pairs into time-stamped, classified interactions: the edges of the provenance graph.

1. **Candidate pairs** (`candidates.py`). Per frame: hand–tool, tool–bin, tool–plate, hand–bin and hand–plate pairs whose boxes are within `candidate_max_gap` frame widths, with the actor (hand or tool) always as `a`. A tool or hand inside a wash zone is also paired with itself, because a wash is a self-loop.
2. **Features** (`features.py`). One `interaction_features` row per pair per frame: center distance and speeds in frame widths (per second), overlap as intersection over the smaller box, `inside_zone`, `zone_kind` (never the zone ID), `dwell_ms`, `wrist_proximity`.
3. **Pair windows** (`windows.py`). Per pair per frame, a `PairWindow`: the feature rows from the last `window_ms`, plus a clip of up to 16 steps over about 1 s, built only by `kitchen/shared/clips.build_clip` from the frames' boxes and vision's change masks.
4. **Classify** (`zone_rules.py`, `classifier.py`). Zone rules always; a primary classifier (the ConvLSTM) only once adopted, with zone rules as the fallback.
5. **Events** (`events.py`). Per-frame labels are merged into `interaction_event`s and written through `StateClient.record_interaction`, buffered while the database is down.

## What's built

| Piece | State |
|---|---|
| `settings.py`: `InteractionSettings` | Built. Every constant of the step in one object (`INTERACTIONS_*` variables), each with a comment saying why. Logged at startup and fingerprinted into the zone-rules version |
| `candidates.py` | Built |
| `features.py`: `FeatureTracker` | Built. Every row validated against the schema |
| `windows.py`: `WindowBuilder` | Built. Clips only when `with_clips` is on (see below) |
| `zone_rules.py`: `ZoneRules` | Built. Passes the shared `InteractionClassifier` contract suite |
| `classifier.py`: `FallbackClassifier` | Built. `classify_window(window)` returns `(labels, {"name", "version"})`; `fallback_count` for health |
| `events.py`: `Segmenter`, `EventWriter`, `InteractionStep`, the CLI | Built |
| `labels.py`, `benchmark.py` | Not built (labeled real clips, and the adoption benchmark) |
| Wiring into vision's frame loop on the Pi | Not yet: see "Running it live" |

### Zone rules

Read from the newest feature row and the window's rows; thresholds and their reasons are in `settings.py`.

| Action | When | Probability | Direction |
|---|---|---|---|
| `transfer.scoop` | a tool overlaps a bin, inside that bin's slot, for `scoop_min_ms` (300) | 0.9 | `b_to_a` (bin to tool) |
| `transfer.contact` | any pair overlaps (at least 0.1 of the smaller box) for `contact_min_ms` (200) | 0.8 | `both` |
| `clean.wash` | a tool or hand paired with itself, in the wash zone for `wash_min_dwell_ms` (3000) | 0.95 | self-loop |
| `unimportant` | anything else: hovering near, passing over | — | no edge |

A scoop is also a contact, so a tool in its bin gives both edges: the scoop moves food bin to tool, and the contact lets a dirty tool dirty the bin. `transfer.wipe` isn't produced yet: it moves both ways like a contact, which covers it until a rule can tell the two apart.

### Events

- **Segmenting.** Hits on the same pair, action and classifier less than `merge_gap_ms` (300) apart are one event, from the first hit to the last. An event closes once its pair has gone `merge_gap_ms` without a hit, so it reaches the database within about 500 ms of its end. A transfer still going after `max_event_ms` (1000) is written and a new one started, so a plate carried to the pass by a dirty hand gets its edge before the hand lets go. A wash is never cut (two washes would clean twice).
- **Probabilities.** A transfer keeps its highest per-frame probability; a wash its lowest, since a wash lowers risk. Transfers below `event_floor` (0.5) and washes below `wash_floor` (0.9) make no edge.
- **Self-loops.** Only a self pair can make a `clean.wash`, and a self pair can make nothing else.
- **IDs.** `ev-<t_start_ms>-<a_id>-<b_id>-<action>`: deterministic, so a replay writes no duplicates (`record_interaction` is idempotent by `event_id`).
- **Order.** Each pair and action's events come out in time order with `t_start_ms <= t_end_ms`. Events of different pairs can close out of start order (a long contact closes after a short one that started later); the graph orders edges by `t_start_ms`, `t_end_ms`, `event_id`.
- **Writing.** `EventWriter` keeps every event until `record_interaction` succeeds. On `StateUnavailable` it stops, and tries again no sooner than `retry_ms` (250) later by the injected `Clock`. `len(writer.pending)` is the buffer depth for health checks.

### The classifier wrapper

`FallbackClassifier(ZoneRules(settings), primary=None)`. With no primary (the default until the benchmark ADR adopts the ConvLSTM), zone rules answer everything. With a primary, zone rules answer instead, and `fallback_count` goes up, whenever:

- the window has no clip (no change mask, a pair too young for two steps, or a clip that failed to build);
- the primary raises, or returns anything other than a non-empty list of `(action, probability)` with probabilities in [0, 1];
- the primary returns `clean.wash` (only zone rules judge washes);
- the primary takes longer than `primary_timeout_ms` (100). It runs in a daemon thread; while a call is stuck, later windows go straight to zone rules without waiting.

Self pairs (washes) always go to zone rules and don't count as fallbacks. Every event records the classifier that produced it.

## Running it

From frames on disk (a vision recording directory holds `frames.jsonl`, `change.npz` and `manifest.json`):

```bash
uv run python -m kitchen.interactions.events --frames frames.jsonl --zones zones.json --out events.jsonl
uv run python -m kitchen.interactions.events --frames data/recordings/run1/frames.jsonl \
    --change data/recordings/run1/change.npz --zones zones.json --out events.jsonl
```

- `--zones` is vision's zones file. Only zone IDs and kinds are used.
- A `manifest.json` next to `frames.jsonl` supplies the camera frame size (for distances in frame widths and for letterboxing clip boxes) and the change-mask parameters. Without one, the defaults are 1280 × 720 and the shared change-mask defaults.
- `--change` adds a clip to every window, so a replay runs the clip path an adopted ConvLSTM would read. Zone rules don't read clips, so it doesn't change the events today.
- Settings come from `INTERACTIONS_*` variables, for example `INTERACTIONS_CONTACT_MIN_MS=250`.

### Running it live

In-process on the Pi, one `InteractionStep` per camera, fed by a vision consumer:

```python
step = InteractionStep(state.list_zones(), settings=load_interaction_settings(), source_size=(width, height))
writer = EventWriter(state, clock, step.settings.retry_ms)

def interactions_consumer(output):            # vision's FrameOutput
    writer.write(step.process(output.observation, output.change_mask))
```

Pass `with_clips=True` (and a `classifier=FallbackClassifier(rules, primary)`) only once a primary is adopted: drawing a clip per pair per frame costs about 1 ms on a laptop, several times that on the Pi, and zone rules never read them.

## Safety rules

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). Tests guarding them carry the `safety` marker.

- **Blind classifier.** Features come from geometry, kinds and zone kinds only; the schema refuses extra fields, and features carry `zone_kind`, never the zone ID. Clips are built only by the shared builder: box masks and the change mask, no pixels, classes or kinds.
- **Rules stay as the fallback.** As above. Zone rules need nothing but features, so they always answer.
- **Fail closed.** No event is dropped while the database is down. A window with no change mask gets no clip rather than a made-up one. An unknown zone ID is `other`, never a wash. A wash needs the wash zone, 3 s of dwell, a self pair and probability 0.9 or more; a brief pass through the sink is nothing.
- **Time runs forward.** A frame not after the previous one is refused. `t_start_ms <= t_end_ms`, and each pair and action's events are in time order. A re-acquired entity has a new ID from vision, so its events are on the new ID.

## Tests

`uv run pytest kitchen/interactions`. The scenes in `tests/scenes.py` are hand-written keyframes (a knife scooping from a bin then touching a plate, a hover, a pass-over, a wash, a brief wash, a hand on a plate) rendered at 15, 30 and 60 FPS, each with the events worked out by hand. Also: the shared `InteractionClassifier` contract suite against zone rules, the wrapper's fallbacks (`FakeClassifier` raising, plus test doubles that hang or answer badly), never-drop with `FakeStateClient` failing on cue, the 500 ms edge latency on a `FakeClock`, and the CLI end to end.

## Known gaps and open questions

- **Clip timing.** Live change masks are per camera frame (about 33 ms at 30 FPS) while the sim clips the ConvLSTM trains on have masks about 62 ms apart, and a pair younger than a second gets a clip of fewer than 16 steps. Worth settling before the benchmark.
- **ConvLSTM probabilities.** Its training picks low decision thresholds (about 0.1 to 0.2), but `event_floor` is 0.5: an adopted ConvLSTM's `classify` should return probabilities calibrated so 0.5 means "probably", or the floor needs a per-classifier value.
- **`PairWindow.clip` and `tensor` can be `None`** here (no change mask, or clips off), though `contracts/apis.md` types them as always present. The wrapper never hands such a window to a primary.
- OQ-9 (edge direction), OQ-12 (real clips), OQ-19 and OQ-20 (ConvLSTM outputs and clip geometry). See [AGENTS.md §11](../../AGENTS.md#11-open-questions).

## How to hand off

- Open PRs from `partner/<slug>` branches.
- The schemas and interfaces in `contracts/` are the deal. Changing them goes through the Director as an RFC.
- Agents reach the partner with issues labeled `owner:partner`.
