import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initialTheme } from "./lib/theme";
import "./styles.css";

// tema prima del primo render (scuro, chiaro o quello del sistema), senza lampi dell'altro tema
let storage: Storage | null = null;
try {
  storage = localStorage; // può non essere disponibile (dati del sito bloccati)
} catch {
  storage = null;
}
document.documentElement.dataset.theme = initialTheme(storage);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
