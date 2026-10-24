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
| `events.py`: `live_consumer(state, clock)` | Built: the slot the local runner's live mode plugs into vision's frame loop. See "Running it live" |

### Zone rules

Read from the newest feature row and the window's rows; thresholds and their reasons are in `settings.py`.

| Action | When | Probability | Direction |
|---|---|---|---|
| `transfer.scoop` | a tool overlaps a bin, inside that bin's slot, for `scoop_min_ms` (300) | 0.9 | `b_to_a` (bin to tool) |
| `transfer.contact` | any pair overlaps (at least 0.1 of the smaller box) for `contact_min_ms` (200) | 0.8 | `both` |
| `clean.wash` | a tool or hand paired with itself, in the wash zone for `wash_min_dwell_ms` (3000), and that long since a transfer last touched it (see Events) | 0.95 | self-loop |
| `unimportant` | anything else: hovering near, passing over | — | no edge |

Overlap time counts from one frame interval before the overlap was first seen (when it may have begun), so a touch measures the same at 10 or 60 FPS: a 220 ms tap is a contact at any frame rate. A self pair's dwell bridges a loss of up to `wash_gap_ms` (1000, vision's `track_lost_ms`), so a hand over the knife or its box jittering across the sink's edge doesn't restart it; time unseen for longer than `pair_gap_ms` isn't counted as rinsing.

A scoop is also a contact, so a tool in its bin gives both edges: the scoop moves food bin to tool, and the contact lets a dirty tool dirty the bin. `transfer.wipe` isn't produced yet: it moves both ways like a contact, which covers it until a rule can tell the two apart.

### Events

The graph applies edges in `t_start_ms` order, so an event counts as happening at its start. `Segmenter.add_frame` takes a whole frame's labels at once, transfers before washes.

- **Touches.** A transfer's hits on one pair and action less than `merge_gap_ms` (300) apart are one touch, whichever classifier answered each frame. **A touch is one edge**: its first hit, written the frame it is seen as a one-hit event (`t_start_ms == t_end_ms`), so a plate carried to the pass by a dirty hand already has its edge when it gets there. The graph's noisy-OR counts every edge as another chance to move allergens, and a contact moves them both ways, so a touch written as several edges would let the two sides raise each other the longer it lasted: a washed knife resting on a plate held an allergy plate. Its later hits write nothing, except for news and surer hits.
- **Cause and effect (news).** A touch's first hit is news to its two entities. At the end of the frame, every other touch going on either of them (on another pair) owes that news, and writes one more edge at its next hit, in a later frame, so it applies after the news. That edge is news to its own entities in turn, so news carries on along a chain of things all touching: a dirty hand that grabs a spoon resting on a plate dirties the plate. Touches first seen in the same frame owe each other's news, so the carry never depends on the order of their event IDs. A touch carries each piece of news once, so a ring of touching things, or two touches on one entity, can't pass news back and forth forever. Transfers on the same pair carry nothing new to each other and owe each other nothing.
- **Surer hits.** A later hit surer than the touch's strongest so far (a primary and zone rules answering in turn) writes an edge for the difference, `(p - p_before) / (1 - p_before)`, so the touch's edges add up to its surest hit under noisy-OR. It is news like a first hit. Zone rules alone give every hit of an action the same probability, so this never happens with them.
- **Washes.** One rinse is one wash: a wash is never cut, and it is bridged across a loss of up to `wash_gap_ms` (1000). A wash label counts only once no transfer has touched the entity for `wash_min_dwell_ms`, so a dirty touch during the soak is never washed away by a wash moments later, and a transfer on the entity ends its open wash. A new wash on an entity that no transfer has touched since its last wash is dropped: it can't make it cleaner than the safe reading. A wash is written `wash_gap_ms` after its last hit.
- **Probabilities.** A touch counts at its highest per-frame probability (its first hit, then surer hits); an edge that carries news has the touch's strongest so far. A wash keeps its lowest, since a wash lowers risk. Each event names the classifier that gave that probability. Transfers below `event_floor` (0.5) and washes below `wash_floor` (0.9) make no edge.
- **Self-loops.** Only a self pair can make a `clean.wash`, and a self pair can make nothing else.
- **IDs.** `ev-<t_start_ms>-<a_id>-<b_id>-<action>`: deterministic, so a replay writes no duplicates (`record_interaction` is idempotent by `event_id`).
- **Order.** Each pair and action's events come out in time order with `t_start_ms <= t_end_ms` and never overlap, whichever classifier answered. A transfer is written the frame of its hit; a wash is written once its rinse ends, so it can come out after transfers that started later. The graph orders edges by `t_start_ms`, `t_end_ms`, `event_id`.
- **Writing.** `EventWriter` keeps every event until `record_interaction` succeeds. On `StateUnavailable` it stops, and tries again no sooner than `retry_ms` (250) later by the injected `Clock`. `len(writer.pending)` is the buffer depth for health checks.

### The classifier wrapper

`FallbackClassifier(ZoneRules(settings), primary=None)`. With no primary (the default until the benchmark ADR adopts the ConvLSTM), zone rules answer everything. With a primary, zone rules answer instead, and `fallback_count` goes up, whenever:

- the window has no clip (no change mask, a pair too young for two steps, or a clip that failed to build);
- the primary raises (any exception, even `SystemExit`) or leaves no answer, or returns anything other than a non-empty list of `(action, probability)` with probabilities in [0, 1];
- the primary returns `clean.wash` (only zone rules judge washes);
- the primary takes longer than `primary_timeout_ms` (100). It runs in a daemon thread; while a call is stuck, later windows go straight to zone rules without waiting.

Self pairs (washes) always go to zone rules and don't count as fallbacks. Every event records the classifier that gave its probability.

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

`live_consumer(state, clock)` is a vision consumer: call it with each `FrameOutput`. It does the following:
- Builds one `InteractionStep` at the first frame, from the zones in `state` and the frame size, so the calibration has to be written before the loop starts.
- Writes each event the step gives with `state.record_interaction`, a touch's first hit the frame it is seen.
- Raises any error instead of swallowing it, so live mode can hold every plate.
- While the database is down, keeps the unsent events and raises `StateUnavailable` on every frame until they land.

```python
from kitchen.interactions.events import live_consumer

consumers = [entity_store_consumer(store), live_consumer(state, clock)]
```

Zone rules answer unless you pass `classifier=FallbackClassifier(rules, primary)`, which is for after a primary has been adopted.

Pass `with_clips=True` (and a `classifier=FallbackClassifier(rules, primary)`) only once a primary is adopted: drawing a clip per pair per frame costs about 1 ms on a laptop, several times that on the Pi, and zone rules never read them.

## Safety rules

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). Tests guarding them carry the `safety` marker.

- **Blind classifier.** Features come from geometry, kinds and zone kinds only; the schema refuses extra fields, and features carry `zone_kind`, never the zone ID. Clips are built only by the shared builder: box masks and the change mask, no pixels, classes or kinds.
- **Rules stay as the fallback.** As above. Zone rules need nothing but features, so they always answer.
- **Fail closed.** No event is dropped while the database is down. A window with no change mask gets no clip rather than a made-up one. An unknown zone ID is `other`, never a wash. A wash needs the wash zone, 3 s of dwell, 3 s since a transfer last touched the entity, a self pair and probability 0.9 or more; a brief pass through the sink is nothing, and one rinse is never two washes. A transfer's first hit is written the frame it is seen, so a verdict at the pass never runs ahead of it. A primary that raises anything, even `SystemExit`, falls back to zone rules.
- **Time runs forward.** A frame not after the previous one is refused. `t_start_ms <= t_end_ms`, and each pair and action's events are in time order and never overlap. News from a new touch crosses every touch going on its entities after it, so edge order follows cause and effect. A re-acquired entity has a new ID from vision, so its events are on the new ID.

## Tests

`uv run pytest kitchen/interactions`. The scenes in `tests/scenes.py` are hand-written keyframes (a knife scooping from a bin then touching a plate, a hover, a pass-over, a wash, a wash with the knife unseen or jittering out of the sink, a brief wash, a hand on a plate) rendered at 15, 30 and 60 FPS, each with the events worked out by hand. `tests/test_edges_in_the_graph.py` runs scenes a review found letting a plate pass through main's graph (`kitchen/graph/propagate.py`) and checks the plate ends up flagged: a rinse with a dropout, a dirty touch during the soak, a held spoon, a hand on a rim then a bin, a chain of three things touching, and a plate carried to the pass. It also checks that a washed knife resting on a plate is one edge, so an allergy plate can pass while an anaphylactic one holds. Also: taps just over the contact and scoop thresholds at 10, 15, 30 and 60 FPS, a hypothesis property test of the Segmenter (one edge per touch at its first hit, written at once; any other edge carries news; a new touch's news crosses every touch going on its entities), news bouncing between touches and round a ring only once, the shared `InteractionClassifier` contract suite against zone rules, the wrapper's fallbacks (`FakeClassifier` raising, `SystemExit`, a primary failing now and then, and test doubles that hang or answer badly), never-drop with `FakeStateClient` failing on cue, the edge latency on a `FakeClock`, and the CLI end to end.

## Known gaps and open questions

- **Clip timing.** Live change masks are per camera frame (about 33 ms at 30 FPS) while the sim clips the ConvLSTM trains on have masks about 62 ms apart, and a pair younger than a second gets a clip of fewer than 16 steps. Worth settling before the benchmark.
- **ConvLSTM probabilities.** Its training picks low decision thresholds (about 0.1 to 0.2), but `event_floor` is 0.5: an adopted ConvLSTM's `classify` should return probabilities calibrated so 0.5 means "probably", or the floor needs a per-classifier value.
- **`PairWindow.clip` and `tensor` can be `None`** here (no change mask, or clips off), though `contracts/apis.md` types them as always present. The wrapper never hands such a window to a primary.
- **A tool held while it is rinsed is never washed.** A hand holding the knife under the water is a hand-knife contact, a transfer, so the knife's wash dwell keeps starting over: the knife has to sit in the sink untouched for 3 s. That leans to HOLD (an unwashed knife holds plates) but may hold more than the kitchen expects.
- **The verdict judges once.** A transfer's first hit now reaches the database the frame it is seen, but the verdict service on `main` judges a plate once, when it reaches the pass, and doesn't recheck when an edge lands later (for example after a database outage). That recheck belongs to the verdict side.
- **News re-counts.** An edge that carries news re-applies everything on the source, not only the news, because the step is allergen-blind and can't tell the two apart. So a washed knife resting on a plate is counted twice once a hand also touches the plate. That only raises a probability (towards HOLD).
- OQ-9 (edge direction), OQ-12 (real clips), OQ-19 and OQ-20 (ConvLSTM outputs and clip geometry). See [AGENTS.md §11](../../AGENTS.md#11-open-questions).

## How to hand off

- Open PRs from `partner/<slug>` branches.
- The schemas and interfaces in `contracts/` are the deal. Changing them goes through the Director as an RFC.
- Agents reach the partner with issues labeled `owner:partner`.
