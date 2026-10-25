# kitchen/interactions: Who touched what (partner)

**Owner:** the partner (a human). The first version was written by an agent at the owner's request, on a `partner/` branch. This README says what the step does, what the agent teams can rely on, and how to run it.

## What this step does

It turns tracked entity pairs into time-stamped, classified interactions: the edges of the provenance graph.

1. **Candidate pairs** (`candidates.py`). Per frame: hand–tool, tool–bin, tool–plate, hand–bin and hand–plate pairs whose boxes are within `candidate_max_gap` frame widths, with the actor (hand or tool) always as `a`. A tool or hand inside a wash zone is also paired with itself, because a wash is a self-loop.
2. **Features** (`features.py`). One `interaction_features` row per pair per frame: center distance and speeds in frame widths (per second), overlap as intersection over the smaller box, `inside_zone`, `zone_kind` (never the zone ID), `dwell_ms`, `wrist_proximity`.
3. **Pair windows** (`windows.py`). Per pair per frame, a `PairWindow`: the feature rows from the last `window_ms` the pair was seen (bridging a loss, see Zone rules), plus a clip of up to 16 steps over about 1 s, built only by `kitchen/shared/clips.build_clip` from the frames' boxes and vision's change masks.
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

Overlap time counts from one frame interval before the overlap was first seen (when it may have begun), so a touch measures the same at 10 or 60 FPS: a 220 ms tap is a contact at any frame rate. A pair keeps its window and dwell through a loss of up to `track_gap_ms` (1000, vision's `track_lost_ms`) while one of its entities is out of view: a touch confirmed after the detector lost a hand still shows it began before, and a hand over the knife in the sink or its box jittering across the sink's edge doesn't restart a wash. Time unseen for longer than `pair_gap_ms` counts as neither overlap, dwell nor rinsing. A pair seen apart (both in view) for longer than `pair_gap_ms` starts over.

A scoop is also a contact, so a tool in its bin gives both edges: the scoop moves food bin to tool, and the contact lets a dirty tool dirty the bin. `transfer.wipe` isn't produced yet: it moves both ways like a contact, which covers it until a rule can tell the two apart.

### Events

The graph applies edges in `t_start_ms` order, so an event counts as happening at its start. `Segmenter.add_frame` takes a whole frame's labels at once, transfers before washes.

- **Touches.** A transfer's hits on one pair and action less than `merge_gap_ms` (300) apart are one touch, whichever classifier answered each frame. **A touch is one edge**: its first hit, written the frame it is seen as a one-hit event (`t_start_ms == t_end_ms`), so a plate carried to the pass by a dirty hand already has its edge when it gets there. The graph's noisy-OR counts every edge as another chance to move allergens, and a contact moves them both ways, so a touch written as several edges would let the two sides raise each other the longer it lasted: a washed knife resting on a plate held an allergy plate. Its later hits write nothing, except for news and surer hits.
- **Cause and effect (news).** A touch's first hit is news to the entities it can move allergens into (both for a contact, the tool for a scoop from a bin). Once all of a frame's hits are in, the news crosses at once every other touch seen on such an entity since the new touch may have begun (on another pair): each writes one edge in that frame, directed only from the side the news came in on (`a_to_b` or `b_to_a`), so it adds nothing the touch had already moved. Wave n's edges span `[t_ms, t_ms + n]` (frame time to n ms after) and their IDs end in `-news<n>`: the graph orders edges by start, then end, so they apply after the frame's hits and earlier waves and before the next frame, however close frames are. That edge is news to the far side in turn, the next wave, so news carries on along a chain of things all touching: a dirty hand that grabs a spoon resting on a plate dirties the plate. When a touch may have begun comes from its window (`touch_began_ms`): zone rules count a touch's overlap from the window's first overlapping row, across dropouts (a window bridges a loss of up to `track_gap_ms`), and confirm it only `contact_min_ms` (`scoop_min_ms` for a scoop) into that, so it may have begun up to a second of seen time, plus a loss, before its first hit; less two frames, for the frame before it was seen and a late one. A touch that ended in that time, or in the very frame the new one is confirmed, may still have been touching when it began. A caller with no window (a test feeding hits) gets `lead_ms = max(contact_min_ms, scoop_min_ms)` before the hit. News stays on offer for `merge_gap_ms`: a touch that was going on when it came but not seen since the new touch began carries it if it is seen again as the same touch. Touches first seen in the same frame carry each other's news, so the carry never depends on the order of their event IDs. A touch carries each piece of news once per side and per touch it came through, so news that comes by parallel paths all gets across. It never carries news back into the two entities whose touch made it, so a ring of touching things can't echo it, and the waves always end. Transfers on the same pair carry nothing new to each other.
- **Surer hits.** A later hit surer than the touch's strongest so far (a primary and zone rules answering in turn) writes an edge for the difference, `(p - p_before) / (1 - p_before)`, so the touch's edges add up to its surest hit under noisy-OR. It is news like a first hit. Zone rules alone give every hit of an action the same probability, so this never happens with them.
- **Washes.** One rinse is one wash: a wash is never cut, and it is bridged across a loss of up to `track_gap_ms` (1000). A wash label counts only once nothing has overlapped the entity for `wash_min_dwell_ms`, a confirmed transfer or not, so a dirty touch during the soak is never washed away by a wash moments later, even one the detector loses before it is confirmed, and an overlap or a transfer on the entity ends its open wash. A wash that opened after that touch may have begun is dropped instead of written: the touch may have come first. Only a transfer, not a bare overlap, lets an entity be washed a second time. A new wash on an entity that no transfer has touched since its last wash is dropped: it can't make it cleaner than the safe reading. A wash is written `track_gap_ms` after its last hit.
- **Probabilities.** A touch counts at its highest per-frame probability (its first hit, then surer hits); an edge that carries news has the touch's strongest so far. A wash keeps its lowest, since a wash lowers risk. Each event names the classifier that gave that probability. Transfers below `event_floor` (0.5) and washes below `wash_floor` (0.9) make no edge.
- **Self-loops.** Only a self pair can make a `clean.wash`, and a self pair can make nothing else.
- **IDs.** `ev-<t_start_ms>-<a_id>-<b_id>-<action>`: deterministic, so a replay writes no duplicates (`record_interaction` is idempotent by `event_id`).
- **Order.** Each pair and action's events come out in time order (by start, then end) with `t_start_ms <= t_end_ms`, whichever classifier answered. A transfer is written the frame of its hit, as a one-hit event; news it carries is written in the same frame, spanning one ms per wave; a wash is written once its rinse ends, so it can come out after transfers that started later. The graph orders edges by `t_start_ms`, `t_end_ms`, `event_id`.
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
- **Fail closed.** No event is dropped while the database is down. A window with no change mask gets no clip rather than a made-up one. An unknown zone ID is `other`, never a wash. A wash needs the wash zone, 3 s of dwell, 3 s since a transfer last touched the entity, a self pair and probability 0.9 or more; a brief pass through the sink is nothing, one rinse is never two washes, and a wash that came due while a touch was being confirmed is dropped. A transfer's first hit is written the frame it is seen, so a verdict at the pass never runs ahead of it. A primary that raises anything, even `SystemExit`, falls back to zone rules.
- **Time runs forward.** A frame not after the previous one is refused. `t_start_ms <= t_end_ms`, and each pair and action's events are in time order and never overlap. News from a new touch crosses every touch going on its entities after it, so edge order follows cause and effect. A re-acquired entity has a new ID from vision, so its events are on the new ID.

## Tests

`uv run pytest kitchen/interactions`. The scenes in `tests/scenes.py` are hand-written keyframes (a knife scooping from a bin then touching a plate, a hover, a pass-over, a wash, a wash with the knife unseen or jittering out of the sink, a brief wash, a hand on a plate) rendered at 15, 30 and 60 FPS, each with the events worked out by hand. `tests/test_edges_in_the_graph.py` runs scenes a review found letting a plate pass through main's graph (`kitchen/graph/propagate.py`) and checks the plate ends up flagged: a rinse with a dropout, a dirty touch during the soak (with no wash at all), a wash coming due while a touch is being confirmed (both ways round, grabbed 0 to 70 ms before it is due, with the hand unseen for 283 ms, and with the detector losing the hand for 333 ms), a spoon lifted as it is grabbed then unseen, a grab confirmed after the hand or spoon is lost for 400 or 900 ms (lifted at once, or during the loss), a held spoon lifted 0 to 700 ms after the grab, a hand on a rim then a bin that leaves 100 ms after reaching it, a hand that lets go of a handle for a moment, a chain of three things touching (lifted at several times), and a plate carried to the pass. It also checks that a washed knife resting on a plate is one edge, so an allergy plate can pass while an anaphylactic one holds. Also: taps just over the contact and scoop thresholds at 10, 15, 30 and 60 FPS, a touch on both sides of a loss counting only the time it was seen, windows and dwell bridging an entity out of view but not a pair seen apart, a hypothesis property test of the Segmenter (one edge per touch at its first hit, written at once; any other edge carries news from a neighbouring edge of the wave before it; a new touch's news crosses every touch seen on its entities since it may have begun), news carried by a touch seen again, news reaching back as far as a window that bridged a loss can show, news bouncing between touches and round a ring only once, the shared `InteractionClassifier` contract suite against zone rules, the wrapper's fallbacks (`FakeClassifier` raising, `SystemExit`, a primary failing now and then, and test doubles that hang or answer badly), never-drop with `FakeStateClient` failing on cue, the edge latency on a `FakeClock`, and the CLI end to end.

## Known gaps and open questions

- **Clip timing.** Live change masks are per camera frame (about 33 ms at 30 FPS) while the sim clips the ConvLSTM trains on have masks about 62 ms apart, and a pair younger than a second gets a clip of fewer than 16 steps. A window that bridged a loss gives a clip with fewer steps and a jump in its step times. Worth settling before the benchmark.
- **ConvLSTM probabilities.** Its training picks low decision thresholds (about 0.1 to 0.2), but `event_floor` is 0.5: an adopted ConvLSTM's `classify` should return probabilities calibrated so 0.5 means "probably", or the floor needs a per-classifier value.
- **`PairWindow.clip` and `tensor` can be `None`** here (no change mask, or clips off), though `contracts/apis.md` types them as always present. The wrapper never hands such a window to a primary.
- **A tool held while it is rinsed is never washed.** A hand holding the knife under the water is a hand-knife contact, a transfer, so the knife's wash dwell keeps starting over: the knife has to sit in the sink untouched for 3 s. That leans to HOLD (an unwashed knife holds plates) but may hold more than the kitchen expects.
- **Edges that land after a verdict.** A touch's first hit reaches the database the frame it is seen, and news it carries in the same frame; an edge that still lands after a plate was judged (a database outage, a touch confirmed as the plate reaches the pass) makes the verdict service judge the plate again (`late_edge`), so the gate can go from PASS to HOLD a moment later.
- **News re-counts its source.** An edge that carries news re-applies everything on the side the news came in on, not only the news, because the step is allergen-blind and can't tell the two apart. So a washed knife resting on a plate is counted twice on the plate once a hand touches the knife (a hand on the plate is carried the other way only, and changes nothing). That only raises a probability (towards HOLD).
- **A primary whose confidence creeps up.** Each rise in a touch's probability writes an edge for the difference and is news to its neighbours, so a primary that answers 0.80, 0.81, 0.82, ... through a touch writes an edge a frame. That only raises probabilities, but a plate's history can then pass the plate check's `MAX_EDGES` (a `laya_unavailable` HOLD). Settle it before adopting a primary, for example by rounding its probabilities up to steps of 0.05.
- **How far news looks back.** News reaches touches seen since the new touch may have begun, as its window shows. That can be up to two frames before the overlap was first seen, so a touch that ended just before also gets it, which only leans HOLD. A window bridges a loss for as long as vision keeps the track, so a grab confirmed after the detector lost the hand (or the spoon) still reaches a spoon-plate touch that ended during the loss. An entity lost for longer comes back with a new ID and unknown origin (safety rule 8). It starts clean, so what the old ID carried is not carried over. News across a loss reaches further back, so video with dropouts writes more edges (in random kitchens, 8.5% more with a dropout on most tracks, 1.8% with one on a third of them), which only leans HOLD.
- OQ-9 (edge direction), OQ-12 (real clips), OQ-19 and OQ-20 (ConvLSTM outputs and clip geometry). See [AGENTS.md §11](../../AGENTS.md#11-open-questions).

## How to hand off

- Open PRs from `partner/<slug>` branches.
- The schemas and interfaces in `contracts/` are the deal. Changing them goes through the Director as an RFC.
- Agents reach the partner with issues labeled `owner:partner`.
