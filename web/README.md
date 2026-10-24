# web: Tablet, chef and expo screens

**Owner:** team `outputs` (agents). Also owns [`kitchen/gate/`](../kitchen/gate/README.md).

**Pipeline step:** what people see. Servers enter tickets, chefs confirm ingredients, and expo sees which plates are held and why. See [AGENTS.md §2](../AGENTS.md#2-pipeline-and-code-map).

## What happens here

- **Tablet:** the server types a note (voice in M3), sees the parsed constraints, answers one-tap questions, and sends. Send stays disabled until the draft is `sendable`.
- **Chef:** lists open chef confirmations for unclear bin ingredients. The chef picks allergen units, and "none of these" is an explicit choice.
- **Expo board:** tickets, plates and verdicts. For each held plate it shows reasons, flagged allergens and the chain step by step with per-step confidence. It also shows camera overlays coloring entities by allergen status (M2) and the Repeated Mistakes view (M4).
- **Start ticket:** a cook-facing button that calls vision's `POST /v1/plates/start`.
- We never decide anything and write no database tables. We change state only through the orders and vision HTTP APIs.

## Planned layout

Nothing is here yet; outputs-01 creates it. Per [ADR 0007](../docs/decisions/0007-outputs-web-framework.md): one React + TypeScript app on Vite, Node 24 LTS, with the paths `/tablet`, `/chef` and `/expo` and no router library.

```
web/
  package.json, vite.config.ts   app, build and test (vitest)
  src/
    main.tsx   creates the state source from WEB_STATE_SOURCE (scenario | spacetime;
               missing or unknown: no source, every screen shows FEED LOST) and mounts the app
    app.tsx    screen_for_path(): /tablet, /chef, /expo, any other path the / links page
    styles.css shared rules, including 44 px touch targets
    state/     StateSource interface; ScenarioStateSource (fake); SpacetimeSource (partner's TS bindings)
    api/       clients for the Orders and Vision HTTP APIs; MSW mocks (api/mocks/) built from kitchen/orders/fakes/http/
    generated/ TS types generated from contracts/schemas by platform (never hand-edit)
    tablet/    ticket entry, one-tap questions, send gate
    chef/      chef confirmation
    expo/      held plates, chains, overlays, Repeated Mistakes
```

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| Out/In | Parse, answers, send, amend, transcribe, chef confirm | [Orders HTTP API](../contracts/apis.md#orders-http-api) | orders |
| Out/In | Start ticket, camera stream, health | [Vision HTTP API](../contracts/apis.md#vision-http-api-on-the-pi) | vision |
| In | `ticket`, `ticket_constraint`, `bin_profile`, `chef_confirmation`, `entity`, `entity_risk`, `plate_binding`, `verdict`, `verdict_step`, `mistake_pattern` | [state-tables.md](../contracts/state-tables.md) via SpacetimeDB TS bindings | partner's `database/` (bindings), written by orders, vision, verdict and the partner |
| Uses | Allergen choices | [`allergen_units.json`](../contracts/allergen_units.json) | contracts (never hardcoded) |

**Data source.** Every screen reads through one `StateSource` interface, picked at build time by `WEB_STATE_SOURCE`, never by the URL (ADR 0007). Until the partner's database ships its generated TypeScript bindings, `ScenarioStateSource` replays any `contracts/scenarios/*.json` in the browser. The SpacetimeDB source plugs in behind the same interface (`needs-partner`). Browser calls to the Pi and the orders service rely on the CORS rule in [HTTP conventions](../contracts/apis.md#http-conventions).

## Safety rules that apply

See [AGENTS.md §3](../AGENTS.md#3-safety-rules).

| Rule | How we could break it | Test |
|---|---|---|
| 1. Fail closed | Showing PASS on stale data after the feed drops, or falling back to a replay when the source isn't set | Connection loss shows a "feed lost" banner and clears PASS badges. A missing or unknown `WEB_STATE_SOURCE` shows FEED LOST on every screen |
| 2. Models only tighten | Client-side decision logic | The pass badge renders only from `verdict.decision == "pass"`. Code review checks there is no other logic |
| 3. No unanswered questions | Tablet sends with an open question | Send is disabled until `sendable`. A `409 unanswered_questions` re-shows the questions. `jev_unavailable` shows manual entry, never a guessed result |
| 9. Late changes recheck everything | Showing an old revision's PASS | A plate whose latest verdict is for an older revision shows "rechecking" |

## Fakes

- **We use:** `ScenarioStateSource`, and MSW mock servers built from `kitchen/orders/fakes/http/` and from the vision API table.
- **We ship:** `ScenarioStateSource`, for anyone demoing the UI without a database.

## Tests

Run `npm --prefix web test` (vitest) and `npm --prefix web run build`. UI PRs include a screenshot of the change running on `ScenarioStateSource`. Touch targets are at least 44 px.

## Milestones

- **M0:** expo board skeleton on `ScenarioStateSource` (outputs-01). Framework ADR (outputs-02).
- **M1:** tablet with the send gate. Expo chain view. Chef screen. Start-ticket button against a mock. SpacetimeDB bindings (needs partner).
- **M2:** camera overlays colored by `entity_risk` (OQ-6). Start ticket against the real Pi.
- **M3:** voice ticket entry through `/v1/transcribe`.
- **M4:** Repeated Mistakes view with suggested fixes. Amendment flow with the recheck visible on expo. Demo polish.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| TS bindings arrive late (partner) | `ScenarioStateSource` behind the same interface, so every screen is built and tested first |
| Overlay boxes (5 Hz) lag 30 FPS video (OQ-6) | Interpolate client-side. Revisit burned-in overlays with vision |
| Browser can't reach the Pi (CORS, LAN) | CORS origins set per the HTTP conventions. Mock servers in tests |
| TS types from schemas not ready | One hand-written `src/types/TEMPORARY.ts` linked to the platform request, deleted when `src/generated/` lands |
