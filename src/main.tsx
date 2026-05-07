import React from "react";
import ReactDOM from "react-dom/client";
import "./theme.css";
import "./App.css";
import App from "./App";
import Screen2 from "./Screen2";

// ── Router — detekterer om dette er skærm 2 ───────────────────
// Tauri sætter vinduets label tilgængeligt via internals.
// Vi tjekker både URL-parameter og window label.
function isScreen2(): boolean {
  // Metode 1: URL parameter (virker i dev-mode)
  if (window.location.search.includes("screen=2")) return true;

  // Metode 2: Tauri window label (virker i production)
  try {
    const internals = (window as any).__TAURI_INTERNALS__;
    if (internals?.metadata?.currentWindow?.label === "screen2") return true;
  } catch {}

  // Metode 3: Tjek window.__TAURI__ metadata
  try {
    const tauri = (window as any).__TAURI__;
    if (tauri?.metadata?.currentWindow?.label === "screen2") return true;
  } catch {}

  return false;
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  isScreen2() ? <Screen2 /> : <App />
);
