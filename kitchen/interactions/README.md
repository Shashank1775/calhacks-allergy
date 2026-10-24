# kitchen/interactions: Who touched what (partner)

**Owner:** the partner (a human). Agents never edit this folder. This README says what the agent teams expect from it, and what they give it.

## What this step does

It turns tracked entity pairs into time-stamped, classified interactions: the edges of the provenance graph.
- Pick candidate pairs that are near or overlapping: hand–tool, tool–bin, tool–plate, hand–bin, hand–plate.
- For each pair, build a `PairWindow`:
  - vector features (distance, overlap, zone kind, speeds, dwell, wrist proximity) for the zone rules;
  - a **clip** (box A, box B, change mask, elapsed time) for the ConvLSTM in [`kitchen/lstm/`](../lstm/README.md).
- Classify with zone rules (always available) or the ConvLSTM (once it wins the benchmark). Write each non-trivial interaction through `StateClient.record_interactions`.

## What the agents rely on

- **Edges:** [`interaction_event`](../../contracts/schemas/interaction_event.schema.json) rows in the `interaction` table, written within about 500 ms of each event's end. The partner's graph and the agents' screens read them.
- **The classifier interface:** [`InteractionClassifier`](../../contracts/apis.md#interactionclassifier), with `classify(PairWindow)`.
- **The safety rules** in [AGENTS.md §3](../../AGENTS.md#3-safety-rules):
  - **Invariant 6.** Features and clips carry no allergen data; clips carry no object pixels, classes or kinds.
  - **Invariant 7.** Zone rules stay the fallback whenever the ConvLSTM fails, raises or times out.
  - **Invariant 1.** Never drop events silently.
  - **Invariant 5.** Events stay in time order, and a re-acquired entity uses its new ID.
- **A wash is a self-loop:** `clean.wash` edges have `a_id == b_id`.

## What the agents give you

- **Frames and change masks (vision team).** An `ObservationFrame` for every frame, plus its change mask from the shared function, in-process on the Pi and in recordings (`frames.jsonl` + `change.npz`). Tasks: vision-01, vision-05, vision-13.
- **The shared builders (platform team).** `kitchen/shared/change_mask.py` and `kitchen/shared/clips.py` (platform-12): use them for live clips, so live input matches the sim-trained model. `ScriptedClassifier` and the classifier contract tests (platform-06).
- **Training clips (sim team).** Rendered, labeled clips with hard negatives (sim-12 to sim-15), and scenario event streams for testing (sim-05).

## Suggested task list

What the agents had planned for this folder is now on GitHub as issues labeled `owner:partner`:
- zone rules v0;
- hand-written frame fixtures;
- candidate pairs and features;
- the edge-direction ADR (OQ-9);
- the event fixture pack;
- the classifier wrapper with fallback;
- the streaming pipeline;
- tuning on recorded frames;
- the labeled-clip format and labeling tool;
- the benchmark;
- real-clip export;
- live pair windows.

Use them, change them or close them; they're yours.

## Until yours lands

The agents' end-to-end harness feeds the graph scenario event streams (from `contracts/scenarios/` and sim) instead of live edges, so nothing agent-side waits on you.

## How to hand off

- Open PRs from `partner/<slug>` branches. Any internal layout is fine; the skeleton files are only suggestions.
- The schemas and interfaces in `contracts/` are the deal. Changing them goes through the Director as an RFC.
- Agents reach you with issues labeled `owner:partner`.

Open questions that touch you: OQ-9 (edge direction), OQ-12 (real clips), OQ-19 and OQ-20 (ConvLSTM outputs and clip geometry). See [AGENTS.md §11](../../AGENTS.md#11-open-questions).
