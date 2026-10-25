# web: scope and what's left

The running list for the web screens. When something lands, move it to **Built**. When you draw something that isn't wired up, wrap it in `<ToBeImplemented>` and add a row under **To be implemented**.

Last updated: 2026-10-24.

## Screens

| Path | Screen | State |
|---|---|---|
| `/` | Home: what this is, the two sources (video file, live camera), links to the stations | Built. Video is picked and previewed locally only |
| `/tablet` | New ticket (note → Laya parse → one-tap questions → send), and changing a sent ticket | Built; checked against the local runner's orders API |
| `/expo` | The gate panel, tickets on the rail, plates on the pass, why a plate is held, start plate, the live camera | Built; the gate panel checked against the local runner |
| `/zones` | Kitchen layout: the zones over the camera; add a bin, washing area, serving area, plating spot, clean stack or other zone by dragging a rectangle; move, resize and delete; save to vision | Built against the Vision API's `GET`/`PUT /zones` |
| `/summary` | Upload a recorded video and read its scene summary: the text, every touch, and what each entity could carry | Built against MSW mocks of the summary service's API (#198); the service itself is #197 |

There's no bin setup screen: the architecture simplification cut chef confirmation, and the orders API change (#149) made the demo's bin profiles hard-coded, so `/bins/read` and `PUT /bins/{id}` are gone.

## Built

- **Scaffold** per the [web framework decision](../docs/decisions/web-framework.md): Vite 8, React 19, TypeScript, plain CSS, no router. Node 24 pinned in `.nvmrc` and `engines`.
- **State.** `StateSource` interface; `FakeStateSource` replays any golden scenario; `state_source_for()` returns no source for anything but an exact known value (fail closed); `useTableRows()` and `useFeedStatus()`.
- **Local runner source.** `WEB_STATE_SOURCE=local` with an `http(s)` `WEB_STATE_URL`: `LocalStateSource` polls `tools/run_local.py`'s `GET /state` every 500 ms (one poll at a time, 2 s timeout) and shows REPLAY unless the snapshot says `mode: "live"` (a missing or unknown mode is a replay). Any failed poll (network error, timeout, error status, a body that isn't JSON or is missing a table) means FEED LOST, keeping the last rows, until a good poll comes back.
- **One badge function.** `plate_badge()` is the only path to PASS: live feed, latest verdict, latest ticket revision. Otherwise HOLD, RECHECKING, FEED LOST or NO VERDICT YET.
- **Banner.** FEED LOST with no source or a dropped feed, CONNECTING, and REPLAY whenever the data is a scenario.
- **API clients** for orders and vision that never throw: every failure comes back as a code the screen shows.
- **MSW mocks** built from the orders team's examples (`kitchen/orders/fakes/http/`) and the vision API table, for tests and for `WEB_MOCK_API=1` demos.
- **Tablet: ticket entry.** Send stays disabled until the draft is `sendable` and every question has an answer; a `409 unanswered_questions` keeps the questions up; `409 number_in_use` says the table already has a ticket; `503 laya_unavailable` shows the error and keeps the note, never a guess.
- **Expo.** Ticket chits with "start plate" and a distinct message for each vision 409; plate cards with stamps; held plates show every reason, the flagged allergens and the chain with per-step confidence.
- **Gate panel** (`expo/gate_panel.tsx`, `gate.ts`): the plate at the pass in large type, PASS, HOLD, FAULT or WAITING, with the ticket's table and the hold's reasons. It applies the gate's own rules (kitchen/gate/service.py): FAULT when the feed is lost or vision's frames stop for 1 s, HOLD for two plates at the pass, and PASS only from `plate_badge()`.
- **Vision health** (`vision_health.ts`): polls `GET /health` when `WEB_VISION_URL` is set, and calls it stale until `last_frame_t_ms` advances, as the gate does.
- **Spoken holds** (`voice.ts`): the browser says "Hold seven, peanut" once per hold, with the gate's own number and allergen words. The expo screen's "turn on voice" button is needed because browsers only speak after a click.
- **Tablet: changing a ticket** (`tablet/amend_ticket.tsx`): pick a sent ticket, type the change, answer Laya's questions, send. It uses the same send gate, plus a message for `409 ticket_changed`.
- **Live camera** (`expo/camera_panel.tsx`): vision's `GET /stream.mjpg`, with each entity's latest box from `entity_state` colored by risk (unknown history and strong allergens red, weak amber, clean green). Only entities tracked now are listed and drawn: the newest `entity_state` time is "now", and an entity last seen more than 1 s before it (vision's `track_lost_ms`) has lost its track, so its last box is dropped. A broken stream says "camera offline" and changes no verdict. The ZONES toggle (`expo/zones_overlay.tsx`) outlines the zones from `GET /zones` under the boxes in either box mode, refetching every 3 s so a save on `/zones` shows up; a failed fetch draws none and says "Zones unavailable". Remembered per viewer in `localStorage`, off by default.
- **Kitchen layout** (`zones/`): `GET /zones` drawn over the camera (or a plain 16:9 frame with no camera), one color per kind, bin slots labeled with their bin from the state. A new bin takes the lowest free `slot-<n>`; other kinds get `wash`, `pass`, `plating`, `clean-stack`, `other`, then `-2`, `-3`. Drawn zones are rectangles with corner handles; other shapes from the zones file draw as they are and can be moved or deleted. Save is `PUT /zones` and stays off until a layout has loaded; a `422 bad_zones` shows vision's message and keeps the edits; Revert reloads. A bin in the state with no `slot-<n>` gets a warning above Save (a bowl there would count as a clean plate), which doesn't block the save. Only a plain primary press drags, one pointer at a time.
- **Home.** Video picker with local preview and a REPLAY marker; live camera card stamped 86'd.
- **Tests.** Vitest, with the ones guarding a safety rule named `safety: …`.
- **CI.** The `web` job in `.github/workflows/ci.yml` runs on every PR and every push to `main`: `npm ci`, the tests, the type check and build (`npm run build`), lint, and Prettier on `src`.

## To be implemented

Each of these is drawn on screen with a "To be implemented" marker.

| Item | Where | Waiting on |
|---|---|---|
| Send an uploaded video to vision | `home/video_source.tsx` | The Vision HTTP API has no upload endpoint. Needs a contracts change (vision, Director). Until then, run vision on the file locally |
| Live camera on the home page | `home/camera_source.tsx` | Nothing: `/expo` already shows the stream; this card is still the 86'd placeholder |
| Voice ticket entry (`POST /transcribe`) | `tablet/ticket_entry.tsx` | HTTPS on the iPad, for the microphone |

## Not started

| Item | Waiting on |
|---|---|
| `SpacetimeSource` behind `StateSource` (#75). Until then `WEB_STATE_SOURCE=spacetime` means no source, so FEED LOST | The partner's TypeScript bindings |
| Generated TS types in `src/generated/`; then delete `src/types/TEMPORARY.ts` | Platform |
| Styling pass on the ticket-rail look | Nothing. Deliberately rough for now |
| Self-hosted fonts, so the screens work offline | Nothing (demo hardening). Fonts load from Google Fonts today |

## Issues this covers

| Issue | State |
|---|---|
| [#35](https://github.com/Shashank1775/calhacks-allergy/issues/35) expo board skeleton | Done. The issue's `ScenarioStateSource` is `FakeStateSource` after the renames |
| [#73](https://github.com/Shashank1775/calhacks-allergy/issues/73) tablet send gate | Done. Mocks come from `kitchen/orders/fakes/http/`. The issue's `jev_unavailable` manual entry is now `503 laya_unavailable`: show the error, keep the note |
| [#74](https://github.com/Shashank1775/calhacks-allergy/issues/74) held-plate chain view | Done |
| [#77](https://github.com/Shashank1775/calhacks-allergy/issues/77) start plate button | Done. The path is now `/plates/start` |
| [#76](https://github.com/Shashank1775/calhacks-allergy/issues/76) chef confirmation | Obsolete: cut by the simplification, and bin profiles are now hard-coded (#149) |
| [#75](https://github.com/Shashank1775/calhacks-allergy/issues/75) SpacetimeDB bindings | Not started (needs the partner) |

## Known gaps

- `FakeStateSource` builds verdicts from each scenario's `expected` entries, so confidence is 1 or 0, flagged probabilities are 1, and `versions.policy` is `fake-replay`. Real numbers arrive with the real database.
- A plate with no verdict yet shows NO VERDICT YET. The web framework decision only lists HOLD, RECHECKING and FEED LOST; confirm the wording.
- `LocalStateSource` checks the snapshot's outline (a scenario id and an array for each table), not each row. `plate_badge()` still shows PASS only for a verdict whose decision is pass for the ticket's current revision.
- The production bundle carries the MSW browser chunk, though it only loads when `WEB_MOCK_API=1` in a dev build.
