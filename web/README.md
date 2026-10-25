# web: the dashboard in React + TypeScript

A one-to-one port of `frontend/` (same look, same hover boxes, same URLs),
built with Vite. While it is being checked it runs next to the old app:

- old app: http://127.0.0.1:8765/app/
- this one: http://127.0.0.1:8765/next/ (the same `#/sleep/2026-10-24` style links work)

Once it has replaced the old app, the backend serves this build at `/app/`
and `frontend/` goes away.

## Build it (once, and after each change)

Needs [Node.js](https://nodejs.org/) 20.19+ or 22.12+ (`winget install OpenJS.NodeJS.LTS`).

```
cd web
npm ci
npm run build
```

The build lands in `web/dist/` (not committed). The backend picks it up on
its next start and serves it at `/next/`; restart it with `run_backend.ps1`
(or log out and in) after the first build. Later builds show up on a reload.

## Work on it

```
cd web
npm run dev        # http://localhost:5173, live reload, talks to the running backend on :8765
npm run typecheck  # TypeScript only
```

## Layout

```
src/main.tsx         entry: fonts, Leaflet, stylesheet, <App>
src/App.tsx          top bar (tabs, sync), hash router, view swap
src/views/           one file per tab: a load() that fetches, a component that draws
src/components/      Chart (wraps charts.js), Hint (hover boxes), Ring, Paged table, ...
src/api/             fetch + 2-minute read cache, response types
src/lib/             formatting, metric explanations
src/sync.ts          the sync indicator and its polling
src/ui.tsx           range / filter settings kept in localStorage
```

Until the switch, the chart helper (`charts.js`), `style.css` and the
vendored fonts and Leaflet are imported from `../frontend` (the `legacy`
alias in `vite.config.ts`), so a style fix shows in both apps.

The response types in `src/api/types.ts` are written by hand for now; they
will be generated from the backend's OpenAPI schema with openapi-typescript
once the backend has typed responses.
