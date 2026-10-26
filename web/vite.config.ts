import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend serves the build (dist/) at /app/. Asset paths are relative, so
// the build doesn't depend on that prefix.

const API = ["/status", "/sync", "/dashboard", "/recovery", "/sleep", "/runs", "/trend", "/explain", "/activity",
  "/day", "/training", "/workout-series", "/longterm", "/ecg", "/data", "/annotations", "/meta", "/openapi.json"];

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    // `npm run dev` talks to the running backend (`health serve`)
    proxy: Object.fromEntries(API.map(p => [p, "http://127.0.0.1:8765"])),
  },
  build: { outDir: "dist", emptyOutDir: true },
});
