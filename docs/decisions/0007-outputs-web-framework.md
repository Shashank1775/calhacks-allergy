# 0007: The screens are one React + TypeScript app on Vite

- **Status:** accepted
- **Date:** 2026-10-23
- **Deciders:** director, mgr-outputs

## Context

Team `outputs` builds three screens in `web/`: the **tablet** (servers enter tickets and answer one-tap questions), the **chef** screen (unclear bin ingredients) and the **expo** board (plates, verdicts, held-plate chains, later camera overlays). Issue #36 (outputs-02) asks us to pick the framework and layout. AGENTS.md §8 already fixes TypeScript, Vite and Node, and leaves the framework to this ADR.

[`web/README.md`](../../web/README.md) already sets the shape the framework has to fit:

- Every screen reads live tables through one `StateSource` interface. `ScenarioStateSource` replays a golden scenario in the browser now. A SpacetimeDB source, built on the partner's generated TypeScript bindings, comes later (`needs-partner`).
- The tablet and chef screens call the orders and vision HTTP APIs directly. Tests mock those APIs with MSW.
- The screens decide nothing. A PASS badge renders only from a verdict row (invariants 1, 2, 3 and 9).

What we need from a framework: tables that update live without hand-written DOM code, tests that run without any service, Safari on an iPad, and code a person can read.

### Options considered

| Option | SpacetimeDB TypeScript SDK | Type checks and tests | Verdict |
|---|---|---|---|
| **React + TypeScript** | Official integration in `spacetimedb/react` (`SpacetimeDBProvider`, `useSpacetimeDB`, `useTable`, `useReducer`), compatible with StrictMode [1]. The official `react-ts` starter is Vite + React [2] | Plain `tsc` checks `.tsx`. `@testing-library/react` supports React 18 and 19 [8] | **Chosen** |
| Svelte 5 + TypeScript | `spacetimedb/svelte`; `useTable` returns Svelte stores [1] | `.svelte` files need `svelte-check` as well as `tsc` [4] | Not chosen: a second file format and checker |
| Vue 3 + TypeScript | `spacetimedb/vue`; `useTable` returns Vue refs [1] | `.vue` files need `vue-tsc` [4] | Not chosen: a second template syntax and checker |
| Plain TypeScript, no framework | The core SDK runs in a browser with no framework [1] | Plain `tsc` | Not chosen: we would hand-write re-rendering for live tables |

React wins on the things that matter here. It is the SDK's first quickstart and starter [2], `.tsx` is checked by plain `tsc`, and it is the framework most people and agents already read fluently (a judgment, not measured). Its one cost is a naming exception for hooks (Decision 7).

## Decision

### 1. One React + TypeScript app, built with Vite

- Scaffold `web/` from Vite's `react-ts` template [4]. Today that gives React 19, Vite 8, TypeScript 6 and oxlint [4].
- Use **npm** and commit `web/package-lock.json`, matching the `npm --prefix web ...` commands in the README.
- Use **Node 24 LTS, version 24.15 or newer**. Pin it with `web/.nvmrc` (`24`) and `"engines": {"node": ">=24.15"}` in `web/package.json`. Why: Node 20 reached end of life on 2026-04-30 [12], and today's tool floors are Vite 8 `^20.19 || >=22.12`, Vitest 5 `^22.12 || ^24 || >=26`, MSW 3 `>=22.12` and jsdom 30 `^22.22.2 || ^24.15.0 || >=26` [13]. Node 24 is LTS until 2028-04-30 [12].
- No component library and no CSS framework. Plain CSS files.

### 2. Layout and routing

One `index.html` and one React root. Each device opens one path and stays there:

| Path | Screen | Device |
|---|---|---|
| `/tablet` | Ticket entry and one-tap questions | iPad at the server station |
| `/chef` | Chef confirmations | Kitchen screen |
| `/expo` | Plates, verdicts, chains | Screen at the pass |
| `/` | Links to the three screens | Any |

- **No router library.** `src/app.tsx` holds one small function, `screen_for_path(path)`, that maps the four paths to screen components. Any other path renders the `/` links page, so a mistyped address shows links, not a blank screen. Screens link to each other with plain `<a href>`. A full reload is fine: no screen keeps state across screens, and the data source comes from the build, not the URL (Decision 3). Why: three fixed paths with no parameters don't need a dependency (KISS).
- Vite's default `appType: "spa"` serves `index.html` for these paths in both dev and preview [5].
- If a screen ever needs URL parameters or nested routes, switch to React Router and amend this ADR.

The folder layout in `web/README.md` stands. This ADR adds `src/main.tsx` (creates the state source and mounts the app), `src/app.tsx` (`screen_for_path`), `src/styles.css` (shared rules, including touch targets) and `src/api/mocks/` (MSW handlers).

### 3. State management: React only, no state library

- The `StateSource` holds each table's rows as an immutable array, plus a **feed status**: `connecting`, `live` or `lost`.
- Screens read it through two hooks, `useTableRows(table_name)` and `useFeedStatus()`, built on React's `useSyncExternalStore` [7]. React requires the snapshot to stay the same value until the store changes [7], so the source replaces a table's array when the table changes and never mutates it.
- **The data source comes from the build, not the URL.** `main.tsx` passes the Vite env variable `WEB_STATE_SOURCE` to one pure function in `src/state/`, `state_source_for(name)`, and shares the result through one React context. Vite compiles the value into the bundle [19], so every screen, after every full-reload link, uses the same source.
  - `scenario` creates `ScenarioStateSource`, which replays the golden scenario named by `WEB_SCENARIO` (a file name in `contracts/scenarios/`). While it runs, every screen shows a REPLAY banner, so replayed badges can't pass for live plates.
  - `spacetime` creates the SpacetimeDB source once outputs-08 lands. Until then it counts as unknown.
  - **Anything else creates no source** (invariant 1): a missing or empty value, a typo, a different case, or `scenario` without a known `WEB_SCENARIO`. With no source, `useFeedStatus()` returns `lost` and `useTableRows()` returns no rows, so every screen shows FEED LOST and no plate shows PASS. The console logs the bad value. The replay therefore runs only when someone asks for it by name.
  - Why not a `?source=` URL parameter: links between screens reload the page and drop the query string, so a live board could fall back to a replay and show its PASS badges as if they were live plates.
  - Changing the source means restarting the dev server or rebuilding [19].
- Form text and the selected plate live in `useState`. HTTP calls are plain async functions in `src/api/` that use `fetch`. No Redux, Zustand or React Query.
- **Safety display rule.** One pure function decides which badge a plate shows. It returns PASS only when the feed is `live` and the plate's latest verdict has `decision == "pass"` for the ticket's latest revision. Everything else shows a non-pass badge: HOLD, RECHECKING (verdict for an older revision, invariant 9) or FEED LOST (invariant 1). It never computes a verdict (invariant 2). It is unit tested, and no other code may render PASS.

### 4. Where the SpacetimeDB TypeScript SDK fits

- The SDK is the npm package `spacetimedb` [1], version 2.13.0 on 2026-10-23 [3]. The partner generates the bindings with `spacetime generate --lang typescript --out-dir <dir> --module-path <module>` [1].
- **Only `src/state/spacetime_source.ts` imports `spacetimedb` and the bindings.** It uses the framework-free core SDK: `DbConnection.builder()` with `withUri`, `withDatabaseName`, `onConnect`, `onDisconnect` and `onConnectError`; `subscriptionBuilder().onApplied().onError().subscribe(...)`; and each table's `onInsert`, `onUpdate` and `onDelete` [1]. These fill the same store `ScenarioStateSource` fills.
- **We don't use the `spacetimedb/react` hooks in screens.** Why: screens stay source-agnostic and are tested on `ScenarioStateSource`, and the fail-closed feed status lives in one place. The React integration [1] is evidence the SDK works in a React app, not the path we take.
- **Fail closed** (invariant 1). The feed is `connecting` until the subscription's `onApplied` fires, then `live`. It goes to `lost` on `onDisconnect`, `onConnectError` or a subscription `onError`. Only `live` can show PASS. The core SDK never retries a failed first connection, and automatic reconnect is opt-in [1]. If we turn reconnect on, the feed stays `lost` until the new subscription applies, because cached rows can be stale while it reconnects [1].
- Settings come from Vite env variables. We set `envPrefix: "WEB_"` so they follow the per-step prefix rule (AGENTS.md §8). They are `WEB_STATE_SOURCE` and `WEB_SCENARIO` (Decision 3), the SpacetimeDB URI and database name, and the orders and vision base URLs. Set them in the shell or in `web/.env.local`. Vite reads `.env` files from the project root, `web/`, by default, and a variable already set in the shell wins over a file [5][19]. The repo's `.gitignore` already keeps `.env.*` out of git.
- **No secrets in `WEB_` variables.** Vite compiles them into the browser bundle, where anyone who opens the page can read them [19]. A key or token never goes in one (AGENTS.md §6, Data and secrets).
- Where the generated bindings live is the partner's call. `database/README.md` lists them under `database/`. If `web/` imports them from there, Vite needs `server.fs.allow` and the bindings must resolve the same `spacetimedb` package as `web/` (unverified; settle in outputs-08 with the partner).

### 5. Where MSW fits

- **In tests, always.** `setupServer` from `msw/node` starts in a Vitest setup file: `listen({ onUnhandledFrame: "error" })` before all tests, `resetHandlers()` after each, `close()` after all [9][10]. With `"error"`, MSW prints an error and halts any request that has no handler, so no test can reach a real service and a missing handler shows up as a network error in the test. MSW's default, `"warn"`, only warns and lets the request through [10]. (MSW 3 renamed this option from `onUnhandledRequest` [10].)
- Handlers live in `src/api/mocks/` and use `http` and `HttpResponse` from `msw/http` [9]. They are built from the orders team's example responses in `kitchen/orders/fakes/http/` (planned there) and from the vision API table in `contracts/apis.md`.
- **In the browser, only for demos on `localhost`.** `setupWorker` from `msw/browser` runs a service worker [9], and service workers need a secure context, which `http://localhost` is and `http://<laptop-ip>:5173` is not [15]. So the iPad always talks to a real (or fake-mode) orders service. If we use browser mocks, they start in `main.tsx` only when `WEB_MOCK_API` is `1` in a dev build. Like the data source, this comes from the build, not the URL, so it doesn't change between screens.

### 6. Tests and tooling

| Tool | Use |
|---|---|
| Vitest | Test runner, `environment: "jsdom"` [6], MSW started in `setupFiles` [6] |
| `@testing-library/react`, `@testing-library/user-event` | Render screens and simulate taps |
| `tsc` (inside `npm run build`) | Type checks |
| Prettier | Formatting (AGENTS.md §8) |
| oxlint | The template's linter [4] |

Commands: `npm --prefix web test` (`vitest run`), `npm --prefix web run build` (`tsc -b && vite build`) and `npm --prefix web run format`. jsdom does no layout [11], so tests can't measure sizes. Touch targets are checked by the shared CSS rule, review and the screenshot every UI PR already includes. No browser-mode or end-to-end runner for now. Revisit for the M2 overlays.

### 7. Naming (the owner's style rules)

- snake_case for file names, TypeScript functions and variables, and our own component props (`feed_status`). Contract fields are already snake_case.
- PascalCase for components and types (`ExpoBoard` in `expo_board.tsx`).
- **Exception: hooks are named `useSomething`.** React's lint rules recognize a hook only by its name: `use` followed by a capital letter [7]. Hooks are the only camelCase functions we write. React and DOM names (`onClick`, `className`) stay as they are.

### 8. Tablet constraints

- **Touch targets of at least 44 × 44 px.** Apple's guidance is a 44 × 44 pt hit region for buttons [14]. WCAG 2.5.5 asks for 44 × 44 CSS px [16]. That one CSS px equals one pt on iPad Safari at default zoom is unverified. One rule in `src/styles.css` gives buttons, links, inputs and selects `min-width: 44px; min-height: 44px` and `touch-action: manipulation`, which turns off double-tap zoom [17].
- **Safari on an iPad running iPadOS 16.4 or newer.** Vite 8's default build target is `safari16.4` and `ios16.4` [5]. Nothing may depend on hover. Layouts work in portrait and landscape.
- **Reaching the laptop over the LAN.** Run `npm --prefix web run dev -- --host` (or `vite preview --host`). Vite then listens on LAN addresses, and IP addresses are allowed hosts by default [5]. The iPad's origin, `http://<laptop-ip>:5173`, must be in `ORDERS_CORS_ORIGINS` and `VISION_CORS_ORIGINS` (`contracts/apis.md`, HTTP conventions).
- **Secure-context limits over plain HTTP.** On `http://<laptop-ip>` the iPad gets no service worker (no browser MSW) [15] and no microphone (`getUserMedia`) [18]. M3 voice ticket entry therefore needs HTTPS, for example Vite's `server.https` with `@vitejs/plugin-basic-ssl` [5]. Whether an iPad accepts that self-signed certificate is unverified; check in M3.

## Consequences

- outputs-01 can scaffold today. Every screen is built and tested on `ScenarioStateSource` and MSW, with no service and no database.
- The SpacetimeDB swap (outputs-08) touches one file, plus one case in `state_source_for`.
- All PASS display goes through one tested function, so reviewers have one place to check invariants 1, 2 and 9 in the UI.
- Picking the data source takes a restart or a rebuild, not a URL. That is the point: nobody can switch a live board to a replay by tapping a link.
- **AGENTS.md updates for the Director**, once this ADR merges:
  - **Node (§8 and §14).** Both say Node ≥ 20. Change them to Node ≥ 24.15, and platform's web CI job (platform-07) uses Node 24. The owner's Mac had Node 20.15.0 on 2026-10-23, below Vite 8's floor, so the owner installs Node 24 LTS before outputs-01.
  - **Hook names (§8).** The owner's style rule says snake_case for TypeScript functions. Add Decision 7's exception: hooks are named `useSomething`, because React's lint finds hooks only by that name.
- **Watch:** the `spacetimedb` npm version should match the partner's SpacetimeDB CLI and server version (whether minor versions may differ is unverified). React Router stays out until a screen needs it.
- **Enforced by:** `npm --prefix web test` and `npm --prefix web run build` in CI (platform-07), the badge function's unit tests, the README's feed-loss and send-gate tests, and review of the PR screenshot for touch targets. outputs-01 writes the source-selection safety tests first: `state_source_for` returns no source for a missing, empty, misspelled or wrong-case value and for `scenario` without a known `WEB_SCENARIO`, and with no source every screen shows FEED LOST and no PASS badge.

## Unverified

- We have not run React 19 with `spacetimedb` 2.13. React 19 is inside the package's optional peer range [3], but the official starter pins React 18.3 [2].
- Where the partner puts the generated bindings, and whether `web/` can import them from `database/` (Decision 4).
- Whether the `spacetimedb` client must match the server's exact version.
- That one CSS px equals one pt on iPad Safari at default zoom.
- Whether an iPad trusts a self-signed dev certificate (M3 only).
- Whether oxlint's default config in the template enables the rules-of-hooks check.

## Sources

All checked on 2026-10-23.

1. SpacetimeDB TypeScript SDK reference (install, `spacetime generate`, `DbConnection` builder, subscriptions, row callbacks, React, Vue and Svelte integrations, reconnect behavior): https://github.com/clockworklabs/SpacetimeDB/blob/master/docs/docs/00200-core-concepts/00600-clients/00700-typescript-reference.md
2. SpacetimeDB React quickstart and `react-ts` starter (Vite, React 18.3, `SpacetimeDBProvider`): https://github.com/clockworklabs/SpacetimeDB/blob/master/docs/docs/00100-intro/00200-quickstarts/00100-react.md and https://github.com/clockworklabs/SpacetimeDB/tree/master/templates/react-ts
3. npm `spacetimedb` 2.13.0 (optional peers React `^18 || ^19`, Vue, Svelte; subpath exports): https://www.npmjs.com/package/spacetimedb (`npm view spacetimedb peerDependencies peerDependenciesMeta exports`)
4. Vite getting started (templates, Node floor) and the create-vite templates: https://vite.dev/guide/, https://github.com/vitejs/vite/tree/main/packages/create-vite (`template-react-ts`, `template-vue-ts`, `template-svelte-ts` `package.json`)
5. Vite config: default build target https://vite.dev/config/build-options, `appType`, `envDir` (default: the project root) and `envPrefix` https://vite.dev/config/shared-options, `server.host`, `allowedHosts`, `https` https://vite.dev/config/server-options
6. Vitest `environment` and `setupFiles`: https://vitest.dev/config/environment, https://vitest.dev/config/setupfiles
7. React `useSyncExternalStore` and hook naming: https://react.dev/reference/react/useSyncExternalStore, https://react.dev/learn/reusing-logic-with-custom-hooks
8. npm `@testing-library/react` 16.3.3 peer range (React `^18 || ^19`): https://www.npmjs.com/package/@testing-library/react
9. MSW quick start (`msw/http`, `msw/node`) and integrations: https://mswjs.io/docs/quick-start, https://mswjs.io/docs/integrations/node, https://mswjs.io/docs/integrations/browser
10. MSW v3.0.0 release notes (Node ≥ 22, ESM only, `onUnhandledRequest` renamed to `onUnhandledFrame`): https://github.com/mswjs/msw/releases/tag/v3.0.0, and `server.listen()` options (`onUnhandledFrame`: `"warn"` by default, `"error"`, `"bypass"` or a function): https://mswjs.io/docs/api/setup-server/listen
11. jsdom, unimplemented parts of the web platform (layout): https://github.com/jsdom/jsdom#unimplemented-parts-of-the-web-platform
12. Node.js release schedule: https://github.com/nodejs/Release/blob/main/schedule.json
13. npm `engines.node` for vite 8.3.5, vitest 5.0.5, msw 3.0.3 and jsdom 30.1.3 (`npm view <package> engines.node`)
14. Apple Human Interface Guidelines, Buttons ("a hit region of at least 44x44 pt"): https://developer.apple.com/design/human-interface-guidelines/buttons
15. MDN, Service Worker API (secure contexts only; `http://localhost` counts): https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API
16. WCAG 2.2, Understanding SC 2.5.5 Target Size (Enhanced): https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html
17. MDN, `touch-action` (`manipulation` turns off double-tap zoom): https://developer.mozilla.org/en-US/docs/Web/CSS/touch-action
18. MDN, `getUserMedia` (secure contexts only): https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
19. Vite, Env Variables and Modes (`import.meta.env` values are strings replaced at build time and bundled into the source, so they must not hold API keys; which `.env` files load; shell variables win over files; restart after changes): https://vite.dev/guide/env-and-mode
