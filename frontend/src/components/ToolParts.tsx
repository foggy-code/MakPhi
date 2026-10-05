import { useEffect, useRef } from "react";
import { rowScrollDelta } from "../lib/compare";
import "../styles/tools.css";

/** Parti comuni dei pannelli di analisi: riga selezionata portata in vista (più DetailPanel ed ExpandAll da ui.tsx). */

/**
 * Contenitore che scorre di una tabella: quando cambia `key` (selezione dal 3D o da un grafico) la riga con
 * `data-row-key={key}` si porta in vista scorrendo solo la tabella, non la pagina.
 */
export function useRevealRow<T extends HTMLElement = HTMLDivElement>(key: string | null) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const box = ref.current;
    if (!box || key === null) return;
    const row = Array.from(box.querySelectorAll<HTMLElement>("[data-row-key]")).find((el) => el.dataset.rowKey === key);
    if (!row) return;
    const head = box.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    const delta = rowScrollDelta(row.getBoundingClientRect(), box.getBoundingClientRect(), head);
    if (delta) box.scrollTop += delta;
  }, [key]);
  return ref;
}

// "Apri tutte" e il pannello di dettaglio stanno in ui.tsx (usati anche fuori dai pannelli di analisi)
export { DetailPanel, ExpandAll } from "./ui";
