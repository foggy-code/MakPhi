import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

try {
  const theme = JSON.parse(localStorage.getItem("bioloom.theme") || '"dark"');
  document.documentElement.dataset.theme = theme;
} catch {
  /* tema predefinito */
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
