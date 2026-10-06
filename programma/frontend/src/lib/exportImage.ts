/**
 * Esportazione dei grafici: PNG di un nodo della pagina (grafico SVG o HTML, con titolo e legenda) e CSV dei dati.
 *
 * Il PNG nasce da `html-to-image`, che copia nel clone gli stili calcolati di ogni elemento: i colori dei temi
 * (`var(--accent)` negli attributi SVG) arrivano già risolti. Gli elementi con `data-export-ignore` (pulsanti,
 * selettori) restano fuori dall'immagine. Lo sfondo è quello del pannello del tema corrente.
 */

export interface DataColumn {
  key: string;
  label: string;
  /** colonna numerica: allineata a destra, ordinata come numero */
  num?: boolean;
}

export type DataValue = string | number | null | undefined;

export interface ChartData {
  columns: DataColumn[];
  rows: Record<string, DataValue>[];
}

/** Nome di file sicuro: lettere, cifre, trattini; niente percorsi né caratteri riservati di Windows. */
export function safeFilename(name: string, ext: string): string {
  const base = name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^\w.-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^[_.-]+|[_.-]+$/g, "")
    .slice(0, 120);
  return `${base || "grafico"}.${ext.replace(/^\./, "")}`;
}

/** Cella CSV: virgolette quando servono; i numeri col punto decimale (leggibili da qualunque programma). */
function csvCell(v: DataValue): string {
  if (v === null || v === undefined) return "";
  const s = typeof v === "number" ? (Number.isFinite(v) ? String(v) : "") : String(v);
  // formule nei fogli di calcolo: un testo che inizia con = + @ (o - non seguito da una cifra, es. un residuo "-3")
  // viene preceduto da un apostrofo
  const safe = typeof v === "string" && /^(?:[=+@\t\r]|-(?![\d.]))/.test(s) ? `'${s}` : s;
  return /[",;\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** Testo CSV (separatore virgola, righe CRLF) con intestazione dalle etichette delle colonne. */
export function toCsv(data: ChartData): string {
  const head = data.columns.map((c) => csvCell(c.label)).join(",");
  const body = data.rows.map((r) => data.columns.map((c) => csvCell(r[c.key])).join(","));
  return [head, ...body].join("\r\n") + "\r\n";
}

/** Scarica un Blob con il nome dato (l'unico punto dell'interfaccia che salva file). */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function downloadCsv(data: ChartData, name: string): void {
  // BOM: Excel riconosce l'UTF-8 (lettere accentate, φ, ψ, Å)
  saveFile(new Blob(["﻿", toCsv(data)], { type: "text/csv;charset=utf-8" }), safeFilename(name, "csv"));
}

/** Colore di sfondo per l'immagine: il pannello del tema corrente (mai trasparente). */
function exportBackground(node: HTMLElement): string {
  const panel = getComputedStyle(node).getPropertyValue("--panel").trim();
  return panel || (document.documentElement.dataset.theme === "light" ? "#ffffff" : "#111a2d");
}

/** Proprietà di presentazione degli SVG che arrivano dai fogli di stile o dalle variabili del tema. */
const SVG_STYLE = [
  "fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray", "stroke-linecap", "stroke-linejoin",
  "opacity", "color", "font-family", "font-size", "font-weight", "font-style", "letter-spacing", "text-anchor",
  "dominant-baseline", "paint-order", "visibility", "display", "shape-rendering",
];

/**
 * Copia negli elementi SVG di `copy` gli stili calcolati dei corrispondenti in `live`. html-to-image clona ogni `<svg>`
 * così com'è, senza gli stili: senza questo passo classi (`.chart text`, `.grid-line`) e colori `var(--…)` negli
 * attributi andrebbero persi nell'immagine (testi neri, griglie invisibili).
 */
function inlineSvgStyles(live: HTMLElement, copy: HTMLElement): void {
  const sources = live.querySelectorAll("svg");
  const targets = copy.querySelectorAll("svg");
  sources.forEach((svg, i) => {
    const target = targets[i];
    if (!target) return;
    const from = [svg, ...Array.from(svg.querySelectorAll("*"))];
    const to = [target, ...Array.from(target.querySelectorAll("*"))];
    from.forEach((el, j) => {
      const t = to[j];
      if (!(t instanceof SVGElement)) return;
      const cs = getComputedStyle(el);
      for (const p of SVG_STYLE) {
        const v = cs.getPropertyValue(p);
        if (v) t.style.setProperty(p, v);
      }
    });
  });
}

/** CSS dei caratteri da incorporare (Inter), calcolato una volta: con un errore si riprova alla prossima immagine. */
let fontCss: Promise<string> | null = null;

/**
 * PNG del nodo (Blob: un data URI di qualche MB può non scaricarsi), a doppia risoluzione. Si cattura una copia
 * fuori schermo, accanto all'originale (stessi stili ereditati, stessa larghezza del contenuto), con la classe
 * `exporting` (mostra la didascalia con titolo e sottotitolo, aggiunge un margine fuori dalla larghezza: box-sizing
 * content-box in styles.css, così gli SVG restano in scala 1:1): la pagina non cambia durante la cattura. Senza i
 * caratteri incorporati l'immagine usa quelli di sistema.
 */
export async function nodeToPng(node: HTMLElement, pixelRatio = 2): Promise<Blob> {
  const { getFontEmbedCSS, toBlob } = await import("html-to-image");
  const filter = (n: Node) => !(n instanceof Element && n.hasAttribute("data-export-ignore"));
  // `style` vale per la radice clonata: nell'immagine la copia non è più spostata fuori schermo
  const style = { position: "static", left: "auto", top: "auto", margin: "0" };
  const copy = node.cloneNode(true) as HTMLElement;
  copy.classList.add("exporting");
  copy.setAttribute("aria-hidden", "true");
  Object.assign(copy.style, { position: "fixed", left: "-100000px", top: "0", width: `${node.getBoundingClientRect().width}px`, pointerEvents: "none" });
  node.after(copy);
  try {
    inlineSvgStyles(node, copy);
    fontCss ??= getFontEmbedCSS(copy).catch(() => {
      fontCss = null;
      return "";
    });
    const css = await fontCss;
    const blob = await toBlob(copy, {
      pixelRatio, backgroundColor: exportBackground(node), filter, cacheBust: false, style,
      ...(css ? { fontEmbedCSS: css } : { skipFonts: true }),
    });
    if (!blob) throw new Error("immagine vuota");
    return blob;
  } finally {
    copy.remove();
  }
}

export async function downloadPng(node: HTMLElement, name: string, pixelRatio = 2): Promise<void> {
  saveFile(await nodeToPng(node, pixelRatio), safeFilename(name, "png"));
}
