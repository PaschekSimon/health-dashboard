import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend serves the build (dist/) at /next/ next to the old app at /app/.
// Relative asset paths let the same build move to /app/ once it replaces it.
//
// Until then the port reuses the old app's chart helper, stylesheet and
// vendored fonts/Leaflet from ../frontend through the "legacy" alias, so both
// apps look the same and a style fix lands in both.

const legacy = fileURLToPath(new URL("../frontend", import.meta.url));
const API = ["/status", "/sync", "/dashboard", "/recovery", "/sleep", "/runs", "/trend", "/explain", "/activity",
  "/day", "/training", "/workout-series", "/longterm", "/ecg", "/data", "/annotations", "/meta", "/openapi.json"];

export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: { alias: { legacy } },
  server: {
    port: 5173,
    fs: { allow: [".", legacy] },
    // `npm run dev` talks to the running backend (run_backend.ps1)
    proxy: Object.fromEntries(API.map(p => [p, "http://127.0.0.1:8765"])),
  },
  build: { outDir: "dist", emptyOutDir: true },
});
