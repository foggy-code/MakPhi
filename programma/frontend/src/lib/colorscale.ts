// Scale di colore per valori per residuo (viewer 3D, strisce, allineamenti) e relative legende.
// Tre tipi: continua (gradiente tra punti di controllo), a fasce (soglie su un valore numerico, es. pLDDT)
// e categoriale (etichette, es. regioni di Ramachandran). Colori sempre esadecimali: 3Dmol non legge le
// variabili CSS.

import type { Theme } from "./theme";

export interface ColorStop {
  value: number;
  color: string;
}

/** Gradiente lineare tra punti di controllo; due punti con lo stesso valore creano un salto netto (soglia). */
export interface ContinuousScale {
  kind: "continuous";
  stops: ColorStop[];
  /** tacche della legenda (valori); se assenti, estremi della scala */
  ticks?: { value: number; label?: string }[];
  digits?: number;
  unit?: string;
}

/**
 * Fasce: vince la prima (in ordine) il cui limite il valore supera: `gt` (escluso, valore > gt) oppure `gte`
 * (compreso, valore ≥ gte, come le fasce di pLDDT di AlphaFold DB); una fascia senza limite prende il resto.
 */
export interface BinnedScale {
  kind: "binned";
  bins: { gt?: number; gte?: number; color: string; label: string }[];
  digits?: number;
  unit?: string;
}

export interface CategoricalScale {
  kind: "categorical";
  categories: { key: string; color: string; label: string }[];
}

export type ColorScale = ContinuousScale | BinnedScale | CategoricalScale;

/** Grigio dei residui senza valore (estremità senza φ/ψ, residui fuori dal profilo…). */
export const NO_VALUE_COLOR = "#6b7280";

// ---------------------------------------------------------------------------------- colori per tema
// Colori fissi pensati per il fondo scuro del 3D, troppo chiari per il fondo del tema chiaro (--viewer-bg #f2f4f8):
// lì si disegna la variante `*_LIGHT`, ≥ 3:1 sul fondo del 3D, sotto la legenda e sui pannelli (WCAG 1.4.11; controllato
// in chartTokens.test.ts). Il tema scuro disegna i colori di sempre.

/**
 * Grigio di contesto dei residui senza niente da segnalare (nessuna segnalazione del consenso, nessun clash, fuori
 * dall'interfaccia, fuori dalle tasche, non scanditi): chiaro, così i residui colorati risaltano sul fondo scuro del 3D.
 * Sul fondo chiaro sparirebbe (1,1:1): lì è `CONTEXT_COLOR_LIGHT`.
 */
export const CONTEXT_COLOR = "#e2e8f0";
export const CONTEXT_COLOR_LIGHT = "#7c8797";

/**
 * Struttura A (riferimento) del confronto strutturale (lib/compare.ts): grigio chiaro sul fondo scuro, ardesia scura sul
 * chiaro (1,4:1 il grigio chiaro). In entrambi i temi è un grigio come «B senza coppia» (`NO_VALUE_COLOR`) ma lontano da
 * lui per luminosità (≥ 2,5:1 fra i due), una differenza che non dipende dalla percezione delle tinte (resta con la
 * deuteranopia e la protanopia); scura e poco satura, non si confonde con il blu della scala delle distanze, come
 * succederebbe a un viola.
 */
export const REFERENCE_COLOR = "#cbd5e1";
export const REFERENCE_COLOR_LIGHT = "#1e293b";

/**
 * «1 metodo» del consenso per residuo (lib/residueColoring.ts): giallo sul fondo scuro (1,2:1 sul chiaro), giallo oliva
 * scuro sul chiaro. Un giallo scuro ha la luminosità del rosso di «3 o più metodi» (#dc2626): con la deuteranopia e la
 * protanopia i due diventano quasi uguali (ΔE2000 2–6 per l'ocra e l'oro); la tinta oliva è quella che se ne allontana di
 * più (ΔE2000 ≈ 9, contro ≈ 12 della coppia più vicina della scala nel tema scuro).
 */
export const ONE_METHOD_COLOR = "#fde047";
export const ONE_METHOD_COLOR_LIGHT = "#666f12";

/** Segno disegnato sopra la struttura (centro delle tasche di fpocket): bianco sul fondo scuro, ardesia quasi nera sul
 *  chiaro, distinta dai colori delle tasche e dal grigio di contesto. */
export const MARKER_COLOR = "#ffffff";
export const MARKER_COLOR_LIGHT = "#1e293b";

/** Varianti per il tema chiaro (chiave: colore del tema scuro, minuscolo). */
export const LIGHT_THEME_COLORS: ReadonlyMap<string, string> = new Map([
  [CONTEXT_COLOR, CONTEXT_COLOR_LIGHT],
  [REFERENCE_COLOR, REFERENCE_COLOR_LIGHT],
  [ONE_METHOD_COLOR, ONE_METHOD_COLOR_LIGHT],
  [MARKER_COLOR, MARKER_COLOR_LIGHT],
]);

/** Colore fisso da disegnare nel tema (3D, strisce, legende): nel tema chiaro la variante di `LIGHT_THEME_COLORS`, se
 *  c'è; gli altri colori restano quelli della scala. */
export function themedColor(color: string, theme: Theme): string {
  return theme === "light" ? LIGHT_THEME_COLORS.get(color.toLowerCase()) ?? color : color;
}

export const PALETTES = {
  /** blu (basso) → bianco → rosso (alto), come lo schema B-factor di PyMOL */
  blueWhiteRed: ["#2166ac", "#f7f7f7", "#b2182b"],
  /** viridis (Smith & van der Walt), percettivamente uniforme */
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
} as const;

function parseHex(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("")}`;
}

/** Colore intermedio (t = 0 → a, t = 1 → b) in RGB. */
export function mixColors(a: string, b: string, t: number): string {
  const x = parseHex(a);
  const y = parseHex(b);
  const k = Math.max(0, Math.min(1, t));
  return toHex([x[0] + (y[0] - x[0]) * k, x[1] + (y[1] - x[1]) * k, x[2] + (y[2] - x[2]) * k]);
}

/** Colore esadecimale come intero 0xRRGGBB (formato nativo di 3Dmol). */
export function hexToInt(hex: string): number {
  const [r, g, b] = parseHex(hex);
  return (r << 16) | (g << 8) | b;
}

/** Punti equidistanti tra min e max con i colori della tavolozza (min = max: un solo colore). */
export function uniformStops(min: number, max: number, palette: readonly string[]): ColorStop[] {
  if (palette.length === 1 || max === min) return [{ value: min, color: palette[0] }, { value: max, color: palette[0] }];
  const last = palette.length - 1;
  return palette.map((color, i) => ({ value: i === last ? max : min + ((max - min) * i) / last, color }));
}

/** Interpolazione su punti ordinati; fuori dall'intervallo si satura. */
export function interpolateStops(stops: ColorStop[], value: number): string {
  if (!stops.length) return NO_VALUE_COLOR;
  if (value <= stops[0].value) return stops[0].color;
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    if (b.value === a.value) continue; // salto netto: il valore della soglia appartiene al tratto successivo
    if (value < b.value || i === stops.length - 2) return mixColors(a.color, b.color, (value - a.value) / (b.value - a.value));
  }
  return stops[stops.length - 1].color;
}

export function binFor(scale: BinnedScale, value: number) {
  const inBin = (b: BinnedScale["bins"][number]) =>
    b.gte !== undefined ? value >= b.gte : b.gt !== undefined ? value > b.gt : true;
  return scale.bins.find(inBin) ?? scale.bins[scale.bins.length - 1];
}

/** Colore di un valore (numero o categoria); grigio se il valore manca o la categoria è sconosciuta. */
export function colorFor(scale: ColorScale, value: number | string | null | undefined): string {
  if (value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value))) return NO_VALUE_COLOR;
  if (scale.kind === "categorical") return scale.categories.find((c) => c.key === String(value))?.color ?? NO_VALUE_COLOR;
  if (typeof value !== "number") return NO_VALUE_COLOR;
  if (scale.kind === "binned") return binFor(scale, value).color;
  return interpolateStops(scale.stops, value);
}

/**
 * Colore da disegnare nel tema per un valore (3D, strisce): il colore fisso (`fixed`, es. il riferimento A) o quello
 * della scala, con la variante del tema chiaro (`themedColor`) per i colori fissi, le fasce e le categorie. I gradienti
 * delle scale continue restano quelli della scala anche dove passano per un colore di `LIGHT_THEME_COLORS` (il bianco a
 * metà della scala di conservazione non è il centro di una tasca): nel tema chiaro li stacca dal fondo il contorno del
 * visualizzatore 3D (`VIEWER_VIEW_STYLE` in lib/theme.ts).
 */
export function drawColor(scale: ColorScale, value: number | string | null | undefined, theme: Theme, fixed?: string): string {
  if (fixed) return themedColor(fixed, theme);
  const color = colorFor(scale, value);
  return scale.kind === "continuous" ? color : themedColor(color, theme);
}

export function fmtScaleValue(value: number, digits = 2, unit?: string): string {
  const text = value.toLocaleString("it-IT", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return unit ? `${text} ${unit}` : text;
}

export type Legend =
  | { kind: "gradient"; title: string; css: string; ticks: { position: number; label: string }[]; noValue: boolean }
  | { kind: "swatches"; title: string; items: { color: string; label: string }[]; noValue: boolean };

/** Legenda di una scala: gradiente CSS con tacche (continua) o elenco di campioni (fasce, categorie). */
export function legendFor(scale: ColorScale, title: string, noValue = false): Legend {
  if (scale.kind === "categorical") return { kind: "swatches", title, items: scale.categories.map((c) => ({ color: c.color, label: c.label })), noValue };
  if (scale.kind === "binned") return { kind: "swatches", title, items: scale.bins.map((b) => ({ color: b.color, label: b.label })), noValue };
  const stops = scale.stops;
  const min = stops[0].value;
  const max = stops[stops.length - 1].value;
  const span = max - min || 1;
  const pos = (v: number) => Math.max(0, Math.min(1, (v - min) / span));
  const css = `linear-gradient(to right, ${stops.map((s) => `${s.color} ${(pos(s.value) * 100).toFixed(1)}%`).join(", ")})`;
  const ticks = (scale.ticks ?? [{ value: min }, { value: max }]).map((t) => ({
    position: pos(t.value),
    label: t.label ?? fmtScaleValue(t.value, scale.digits ?? 2, scale.unit),
  }));
  return { kind: "gradient", title, css, ticks, noValue };
}
