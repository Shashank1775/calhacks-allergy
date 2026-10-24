# The screens are one React + TypeScript app on Vite

- **Status:** accepted
- **Date:** 2026-10-23
- **Decided by:** director, outputs manager

## Context

Team `outputs` builds two screens in `web/`: the **tablet** (tickets, one-tap questions, bin labels at shift start) and the **expo** board (plates, verdicts, held-plate chains, camera overlays). We need live tables without hand-written DOM code, tests that run without any service, Safari on an iPad, and code a person can read. React was picked over Svelte, Vue and plain TypeScript: it is SpacetimeDB's first-class starter, plain `tsc` checks `.tsx`, and most people and agents read it fluently.

## Decision

- **Stack.** Vite's `react-ts` template (React 19, Vite 8, TypeScript), npm with a committed `package-lock.json`, Node 24 LTS (24.15 or newer, pinned in `.nvmrc` and `engines`). Plain CSS: no component library, no CSS framework.
- **Paths, no router.** `/tablet`, `/expo`, and `/` (links to both). `src/app.tsx` has one `screen_for_path(path)` function; any other path shows the links page.
- **State: React only.** A `StateSource` holds each table's rows as immutable arrays plus a feed status (`connecting`, `live`, `lost`). Screens read it with `useTableRows(table_name)` and `useFeedStatus()`, built on `useSyncExternalStore`. Form text lives in `useState`; HTTP calls are plain `fetch` functions in `src/api/`. No Redux, Zustand or React Query.
- **The data source comes from the build, never the URL.** `WEB_STATE_SOURCE=scenario` creates `FakeStateSource`, which replays the golden scenario named by `WEB_SCENARIO` behind a REPLAY banner. `spacetime` creates the SpacetimeDB source. Anything else (missing, typo, wrong case) creates no source, so every screen shows FEED LOST (safety rule: fail closed).
- **One function decides the badge.** It returns PASS only when the feed is `live` and the plate's latest verdict says `pass` for the ticket's latest revision. Otherwise HOLD, RECHECKING (older revision) or FEED LOST. It never computes a verdict, it is unit tested, and nothing else may render PASS.
- **SpacetimeDB.** Only `src/state/spacetime_source.ts` imports the `spacetimedb` SDK and the partner's generated bindings. The feed is `live` only after the subscription applies, and goes `lost` on any disconnect or error.
- **Settings** are Vite env variables with the `WEB_` prefix. They are compiled into the bundle, so no secret ever goes in one.
- **Tests.** Vitest with jsdom and Testing Library; MSW mocks the orders and vision APIs with `onUnhandledFrame: "error"`, so no test reaches a real service. Commands: `npm --prefix web test`, `npm --prefix web run build`.
- **Naming.** snake_case files, functions and props; PascalCase components and types; hooks are `useSomething` because React's lint finds hooks by that name.
- **Tablet constraints.** Touch targets at least 44 × 44 px. iPad Safari 16.4 or newer. Run Vite with `--host` and put `http://<laptop-ip>:5173` in `ORDERS_CORS_ORIGINS` and `VISION_CORS_ORIGINS`. Over plain HTTP the iPad has no microphone, so voice tickets need HTTPS.

## Consequences

- Every screen is built and tested on `FakeStateSource` and MSW, with no service and no database.
- Swapping in SpacetimeDB touches one file.
- Unverified: React 19 with the `spacetimedb` client, and where the partner puts the generated bindings.
