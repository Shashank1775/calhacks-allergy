# web: Tablet and expo screens

**Owner:** team `outputs` (agents). Also owns [`kitchen/gate/`](../kitchen/gate/README.md).

What people see. Servers enter tickets on the tablet; expo sees which plates are held and why.

## What happens here

- **Tablet (`/tablet`):** the server types a note (or speaks it), sees the parsed constraints, answers one-tap questions, and sends. Send stays disabled until the draft is `sendable`.
- **Expo board (`/expo`):** tickets, plates and verdicts. For each held plate it shows reasons, flagged allergens and the chain step by step with per-step confidence. A "start plate" button per ticket calls vision's `POST /plates/start`. Camera overlays color entities by allergen risk.
- We never decide anything and write no database tables. We change state only through the orders and vision HTTP APIs.

## Layout

Per the [web framework decision](../docs/decisions/web-framework.md): one React + TypeScript app on Vite, Node 24 LTS, with the paths `/tablet`, `/expo` and `/`, and no router library. The look is the "ticket rail": order chits on a steel rail, rubber stamps for verdicts. What's built and what's left is tracked in [SCOPE.md](SCOPE.md).

```
web/
  package.json, vite.config.ts   app, build and test (vitest)
  SCOPE.md     what's built, what's drawn but not wired up, what's not started
  src/
    main.tsx   creates the state source from WEB_STATE_SOURCE (scenario | local | spacetime;
               missing or unknown: no source, every screen shows FEED LOST) and mounts the app
    app.tsx    screen_for_path(): /tablet, /expo, any other path the / home page
    badge.ts   plate_badge(): the only way a plate shows PASS
    styles.css shared rules, including 44 px touch targets
    state/     StateSource interface; FakeStateSource (replays a scenario); LocalStateSource (polls the local runner);
               SpacetimeSource later (partner's TS bindings)
    api/       clients for the Orders and Vision HTTP APIs; MSW mocks (api/mocks/) built from kitchen/orders/fakes/http/
               (orders) and the vision API table
    types/     TEMPORARY.ts: hand-written contract types until platform generates src/generated/ (never hand-edit that)
    components/ chit, rail, stamp, banner, "to be implemented" marker
    home/      the / page: video source, live camera source (to be implemented)
    tablet/    ticket entry, one-tap questions, send gate
    expo/      tickets, plates, hold reasons and chains, start-plate button, camera panel
```

## Run it

Use Node 24.15 or newer (`web/.nvmrc`).

```bash
npm --prefix web install
```

Put the settings in `web/.env.local` (git ignores it). To click through every screen with no services running:

```
WEB_STATE_SOURCE=scenario
WEB_SCENARIO=knife-carries-peanut-to-plate
WEB_MOCK_API=1
```

```bash
npm --prefix web run dev
```

**Against the local runner** (`tools/run_local.py`: orders, verdict and gate on fakes, replaying one golden scenario), put this in `web/.env.local`, with no `WEB_MOCK_API`:

```
WEB_STATE_SOURCE=local
WEB_STATE_URL=http://localhost:8001
WEB_ORDERS_URL=http://localhost:8001
```

Start the runner in one terminal and the web app in another, then open http://localhost:5173/expo:

```bash
uv run python tools/run_local.py knife-carries-peanut-to-plate
```

```bash
npm --prefix web run dev
```

The screens poll the runner's `GET /state` twice a second and show REPLAY, since it's a scenario, not the kitchen. If the runner stops or answers badly, every screen shows FEED LOST until it's back. `WEB_STATE_URL` must be an `http://` or `https://` address, or there's no source.

With real services, drop `WEB_MOCK_API` and set `WEB_ORDERS_URL` and `WEB_VISION_URL`. Each service must list the web origin in its CORS setting (`ORDERS_CORS_ORIGINS`, `VISION_CORS_ORIGINS`), for example `http://localhost:5173`, or `http://<laptop-ip>:5173` for the iPad.

## In and out

| Direction | What | Contract | From / to |
|---|---|---|---|
| Out/In | Parse, answers, send, amend, transcribe | [Orders HTTP API](../contracts/apis.md#orders-http-api) | orders |
| Out/In | Start plate, camera stream, health | [Vision HTTP API](../contracts/apis.md#vision-http-api-on-the-pi) | vision |
| In | `ticket`, `bin_profile`, `entity`, `entity_state`, `plate_binding`, `verdict` | [state-tables.md](../contracts/state-tables.md) via SpacetimeDB TS bindings | partner's `database/` (bindings), written by orders, vision, verdict and the partner |
| Uses | Allergen choices | [`allergen_units.json`](../contracts/allergen_units.json) | contracts (never hardcoded) |

**Data source.** Every screen reads through one `StateSource`, picked at build time by `WEB_STATE_SOURCE`, never by the URL: `scenario` (`FakeStateSource` replays any `contracts/scenarios/*.json` in the browser) or `local` (`LocalStateSource` polls the local runner's `GET /state` at `WEB_STATE_URL`). The SpacetimeDB source plugs in behind the same interface once the partner's database ships its generated TypeScript bindings (`needs-partner`).

## Safety rules

See [Safety rules](../AGENTS.md#3-safety-rules).

| Rule | How we could break it | Test |
|---|---|---|
| Fail closed | Showing PASS on stale data after the feed drops, or falling back to a replay when the source isn't set | Connection loss shows FEED LOST and clears PASS badges. A missing or unknown `WEB_STATE_SOURCE` shows FEED LOST on every screen |
| Plain-code verdict | Client-side decision logic | One tested function renders PASS, only from `verdict.decision == "pass"` |
| No unanswered questions | Tablet sends with an open question | Send is disabled until `sendable`. A `409 unanswered_questions` re-shows the questions. `503 laya_unavailable` shows the error and keeps the note, never a guessed result |
| Late changes recheck everything | Showing an old revision's PASS | A plate whose latest verdict is for an older revision shows RECHECKING |

## Tests

- Run `npm --prefix web test` (vitest) and `npm --prefix web run build`.
- We use `FakeStateSource` and MSW mock servers built from `kitchen/orders/fakes/http/` and the vision API table. We ship `FakeStateSource` for anyone demoing the UI without a database.
- UI PRs include a screenshot running on `FakeStateSource`. Touch targets are at least 44 px.

## Watch out for

- TS bindings may arrive late (partner). `FakeStateSource` sits behind the same interface, so every screen is built and tested first.
- Overlay boxes update at 5 Hz against 30 FPS video. Interpolate client-side.
- The iPad has no microphone over plain HTTP, so voice tickets need HTTPS.
