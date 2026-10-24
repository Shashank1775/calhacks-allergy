# web: scope and what's left

The running list for the web screens. When something lands, move it to **Built**. When you draw something that isn't wired up, wrap it in `<ToBeImplemented>` and add a row under **To be implemented**.

Last updated: 2026-10-23.

## Screens

| Path | Screen | State |
|---|---|---|
| `/` | Home: what this is, the two sources (video file, live camera), links to the stations | Built. Video is picked and previewed locally only |
| `/tablet` | New ticket (note → Laya parse → one-tap questions → send) and bin setup | Built against MSW mocks of the orders API |
| `/expo` | Tickets on the rail, plates on the pass, why a plate is held, start plate, camera panel | Built against `FakeStateSource` |

`/chef` is gone: the architecture simplification cut chef confirmation, and bin setup moved onto the tablet.

## Built

- **Scaffold** per the [web framework decision](../docs/decisions/web-framework.md): Vite 8, React 19, TypeScript, plain CSS, no router. Node 24 pinned in `.nvmrc` and `engines`.
- **State.** `StateSource` interface; `FakeStateSource` replays any golden scenario; `state_source_for()` returns no source for anything but an exact known value (fail closed); `useTableRows()` and `useFeedStatus()`.
- **One badge function.** `plate_badge()` is the only path to PASS: live feed, latest verdict, latest ticket revision. Otherwise HOLD, RECHECKING, FEED LOST or NO VERDICT YET.
- **Banner.** FEED LOST with no source or a dropped feed, CONNECTING, and REPLAY whenever the data is a scenario.
- **API clients** for orders and vision that never throw: every failure comes back as a code the screen shows.
- **MSW mocks** built from the contract fixtures, for tests and for `WEB_MOCK_API=1` demos.
- **Tablet: ticket entry.** Send stays disabled until the draft is `sendable` and every question has an answer; a `409 unanswered_questions` keeps the questions up; `laya_unavailable` shows manual entry, never a guess.
- **Tablet: bin setup.** Lists bins, reads a label through Laya, lets the person adjust each allergen, saves (as `manual` when adjusted). If Laya is down the profile starts as unknown (every allergen), never as clean.
- **Expo.** Ticket chits with "start plate" and a distinct message for each vision 409; plate cards with stamps; held plates show every reason, the flagged allergens and the chain with per-step confidence.
- **Home.** Video picker with local preview and a REPLAY marker; live camera card stamped 86'd.
- **Tests.** 55 Vitest tests; the ones guarding a safety rule are named `safety: …`.

## To be implemented

Each of these is drawn on screen with a "To be implemented" marker.

| Item | Where | Waiting on |
|---|---|---|
| Send an uploaded video to vision | `home/video_source.tsx` | The Vision HTTP API has no upload endpoint. Needs a contracts change (vision, Director). Until then, run vision on the file locally |
| Live camera feed | `home/camera_source.tsx` | Pi camera and Hailo (hardware), then `GET /stream.mjpg` |
| Camera with overlays | `expo/camera_panel.tsx` | The live camera, plus `entity_state` rows from the partner's database |
| Voice ticket entry (`POST /transcribe`) | `tablet/ticket_entry.tsx` | HTTPS on the iPad, for the microphone |
| Amend a sent ticket (`POST /tickets/{id}/amend`) | `tablet/ticket_entry.tsx` | Nothing. Next up |
| Send a hand-entered ticket when Laya is down | `tablet/ticket_entry.tsx` (`ManualEntry`) | The orders API has no manual-ticket endpoint. Needs a contracts change (orders, Director) |

## Not started

| Item | Waiting on |
|---|---|
| `SpacetimeSource` behind `StateSource` (#75). Until then `WEB_STATE_SOURCE=spacetime` means no source, so FEED LOST | The partner's TypeScript bindings |
| Generated TS types in `src/generated/`; then delete `src/types/TEMPORARY.ts` | Platform |
| Web job in CI (`npm --prefix web test`, `npm --prefix web run build`) | Platform |
| Styling pass on the ticket-rail look | Nothing. Deliberately rough for now |
| Self-hosted fonts, so the screens work offline | Nothing (demo hardening). Fonts load from Google Fonts today |
| Show the vision feed's health (`GET /health`) on expo | Nothing. The gate already goes to `fault`; expo could say so too |

## Issues this covers

| Issue | State |
|---|---|
| [#35](https://github.com/Shashank1775/calhacks-allergy/issues/35) expo board skeleton | Done. The issue's `ScenarioStateSource` is `FakeStateSource` after the renames |
| [#73](https://github.com/Shashank1775/calhacks-allergy/issues/73) tablet send gate | Done. Mocks come from contract fixtures; `kitchen/orders/fakes/http/` doesn't exist |
| [#74](https://github.com/Shashank1775/calhacks-allergy/issues/74) held-plate chain view | Done |
| [#77](https://github.com/Shashank1775/calhacks-allergy/issues/77) start plate button | Done. The path is now `/plates/start` |
| [#76](https://github.com/Shashank1775/calhacks-allergy/issues/76) chef confirmation | Obsolete: cut by the simplification. Bin setup on the tablet replaces it |
| [#75](https://github.com/Shashank1775/calhacks-allergy/issues/75) SpacetimeDB bindings | Not started (needs the partner) |

## Known gaps

- `FakeStateSource` builds verdicts from each scenario's `expected` entries, so confidence is 1 or 0, flagged probabilities are 1, and `versions.policy` is `fake-replay`. Real numbers arrive with the real database.
- A plate with no verdict yet shows NO VERDICT YET. The web framework decision only lists HOLD, RECHECKING and FEED LOST; confirm the wording.
- The production bundle carries the MSW browser chunk, though it only loads when `WEB_MOCK_API=1` in a dev build.
