import { createRoot } from "react-dom/client";
// vendored fonts and map library (no CDN), then the stylesheet
import "./vendor/fonts/fonts.css";
import "./vendor/leaflet/leaflet.css";
import "./vendor/leaflet/leaflet.js";   // sets window.L for the route map
import "./style.css";
import { App } from "./App";
import { UiProvider } from "./ui";

createRoot(document.getElementById("root")!).render(<UiProvider><App /></UiProvider>);
