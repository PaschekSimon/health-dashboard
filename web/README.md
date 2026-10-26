# web: the dashboard in React + TypeScript

The dashboard, built with Vite. The backend serves the build at
http://127.0.0.1:8765/app/ (the installed phone app uses the same address).

## Build it (once, and after each change or `git pull`)

Needs [Node.js](https://nodejs.org/) 20.19+ or 22.12+ (`winget install OpenJS.NodeJS.LTS`).

```
cd web
npm ci
npm run build
```

The build lands in `web/dist/` (not committed) and shows up on the next
reload; the backend needs no restart.

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
src/lib/             formatting, metric explanations, charts.js
src/sync.ts          the sync indicator and its polling
src/ui.tsx           range / filter settings kept in localStorage
```

`src/lib/charts.js` (the SVG chart helper, plain JavaScript with types in
`charts.d.ts`), `src/style.css` and `src/vendor/` (Leaflet and the Geist
fonts, so nothing loads from a CDN) come from the first, plain-JavaScript
version of the app. `public/` holds the manifest and icons for the phone
install.

## API types

`src/api/schema.d.ts` is generated from the backend's response models
(`/openapi.json`) with openapi-typescript; `src/api/types.ts` gives its
shapes short names. After changing a response model in
`src/health_dashboard/core/schemas.py`, regenerate it with the backend running and
commit the result:

```
npm run gen:types
```

`npm run typecheck` then shows every place the app needs to follow the
change. CI fails when the committed file no longer matches the backend.
