# web: Tablet and expo screens

**Owner:** team `outputs` (agents). Also owns [`kitchen/gate/`](../kitchen/gate/README.md).

What people see. Servers enter tickets and set up bins on the tablet; expo sees which plates are held and why.

## What happens here

- **Tablet (`/tablet`):** the server types a note (or speaks it), sees the parsed constraints, answers one-tap questions, and sends. Send stays disabled until the draft is `sendable`. At shift start, the same screen reads each bin's label through Laya, shows the allergen profile, lets the person adjust it, and saves it.
- **Expo board (`/expo`):** tickets, plates and verdicts. For each held plate it shows reasons, flagged allergens and the chain step by step with per-step confidence. A "start plate" button per ticket calls vision's `POST /plates/start`. Camera overlays color entities by allergen risk.
- We never decide anything and write no database tables. We change state only through the orders and vision HTTP APIs.

## Planned layout

Nothing is here yet. Per the [web framework decision](../docs/decisions/web-framework.md): one React + TypeScript app on Vite, Node 24 LTS, with the paths `/tablet`, `/expo` and `/`, and no router library.

```
web/
  package.json, vite.config.ts   app, build and test (vitest)
  src/
    main.tsx   creates the state source from WEB_STATE_SOURCE (scenario | spacetime;
               missing or unknown: no source, every screen shows FEED LOST) and mounts the app
    app.tsx    screen_for_path(): /tablet, /expo, any other path the / links page
    styles.css shared rules, including 44 px touch targets
    state/     StateSource interface; FakeStateSource (replays a scenario); SpacetimeSource (partner's TS bindings)
    api/       clients for the Orders and Vision HTTP APIs; MSW mocks (api/mocks/) built from kitchen/orders/fakes/http/
    generated/ TS types generated from contracts/schemas by platform (never hand-edit)
    tablet/    ticket entry, one-tap questions, send gate, bin setup
    expo/      held plates, chains, start-plate button, overlays
```

## In and out

| Direction | What | Contract | From / to |
|---|---|---|---|
| Out/In | Parse, answers, send, amend, transcribe, bin labels | [Orders HTTP API](../contracts/apis.md#orders-http-api) | orders |
| Out/In | Start plate, camera stream, health | [Vision HTTP API](../contracts/apis.md#vision-http-api-on-the-pi) | vision |
| In | `ticket`, `bin_profile`, `entity`, `entity_risk`, `plate_binding`, `verdict` | [state-tables.md](../contracts/state-tables.md) via SpacetimeDB TS bindings | partner's `database/` (bindings), written by orders, vision, verdict and the partner |
| Uses | Allergen choices | [`allergen_units.json`](../contracts/allergen_units.json) | contracts (never hardcoded) |

**Data source.** Every screen reads through one `StateSource`, picked at build time by `WEB_STATE_SOURCE`, never by the URL. Until the partner's database ships its generated TypeScript bindings, `FakeStateSource` replays any `contracts/scenarios/*.json` in the browser. The SpacetimeDB source plugs in behind the same interface (`needs-partner`).

## Safety rules

See [Safety rules](../AGENTS.md#3-safety-rules).

| Rule | How we could break it | Test |
|---|---|---|
| Fail closed | Showing PASS on stale data after the feed drops, or falling back to a replay when the source isn't set | Connection loss shows FEED LOST and clears PASS badges. A missing or unknown `WEB_STATE_SOURCE` shows FEED LOST on every screen |
| Plain-code verdict | Client-side decision logic | One tested function renders PASS, only from `verdict.decision == "pass"` |
| No unanswered questions | Tablet sends with an open question | Send is disabled until `sendable`. A `409 unanswered_questions` re-shows the questions. `laya_unavailable` shows manual entry, never a guessed result |
| Late changes recheck everything | Showing an old revision's PASS | A plate whose latest verdict is for an older revision shows RECHECKING |

## Tests

- Run `npm --prefix web test` (vitest) and `npm --prefix web run build`.
- We use `FakeStateSource` and MSW mock servers built from `kitchen/orders/fakes/http/` and the vision API table. We ship `FakeStateSource` for anyone demoing the UI without a database.
- UI PRs include a screenshot running on `FakeStateSource`. Touch targets are at least 44 px.

## Watch out for

- TS bindings may arrive late (partner). `FakeStateSource` sits behind the same interface, so every screen is built and tested first.
- Overlay boxes update at 5 Hz against 30 FPS video. Interpolate client-side.
- The iPad has no microphone over plain HTTP, so voice tickets need HTTPS.
