import { createRoot } from "react-dom/client";
// the old app's fonts, map library and stylesheet, shared until this app replaces it
import "legacy/vendor/fonts/fonts.css";
import "legacy/vendor/leaflet/leaflet.css";
import "legacy/vendor/leaflet/leaflet.js";   // sets window.L for the route map
import "legacy/style.css";
import { App } from "./App";
import { UiProvider } from "./ui";

createRoot(document.getElementById("root")!).render(<UiProvider><App /></UiProvider>);
