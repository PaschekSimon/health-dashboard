Third-party files served with the dashboard, so it loads without any CDN.

- `leaflet/`: Leaflet 1.9.4 (`dist/` of the npm package, same file hashes as
  the unpkg build), BSD-2-Clause, see `leaflet/LICENSE`. Map tiles still come
  from openstreetmap.org; offline, the route falls back to a plain SVG line.
- `fonts/`: Geist and Geist Mono variable fonts (latin + latin-ext subsets)
  from `@fontsource-variable/geist` and `@fontsource-variable/geist-mono` 5.3.0,
  SIL Open Font License 1.1, see `fonts/LICENSE`.
