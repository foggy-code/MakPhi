import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// carattere Inter incluso nel pacchetto (funziona anche senza rete) e foglio di stile di base, prima dei fogli delle
// singole aree (src/styles/*.css, importati dai componenti) che così possono ritoccarne le regole
import "@fontsource-variable/inter";
import "./styles.css";
import { App } from "./App";
import { initialTheme } from "./lib/theme";

// tema prima del primo render (scuro, chiaro o quello del sistema), senza lampi dell'altro tema
let storage: Storage | null;
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
