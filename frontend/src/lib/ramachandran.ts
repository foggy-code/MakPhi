import type { QualityReport, RamaRect } from "../api/types";
import type { ChartData } from "./exportImage";
import { fmtNum } from "./format";
import { residueName } from "./residues";

/**
 * Grafico di Ramachandran: viste per classe di residui, etichette in italiano, conteggi per regione, dati per la
 * tabella e il CSV, ricerca di un residuo. Il disegno è in components/charts/Ramachandran.tsx.
 */
export type RamaPoint = QualityReport["ramachandran"]["points"][number];
export type RamaRegion = RamaPoint["region"];
export interface RamaRegions {
  favored: RamaRect[];
  allowed: RamaRect[];
}

/** "all": tutti i residui sulle regioni del caso generale, glicine a triangolo (il grafico classico di PROCHECK). */
export type RamaView = "all" | "general" | "glycine" | "proline";
/** Filtro dei punti nella vista espansa. */
export type RamaShow = "all" | "flagged" | "outliers";

export const RAMA_VIEWS: { value: RamaView; label: string }[] = [
  { value: "all", label: "Tutti" },
  { value: "general", label: "Generale" },
  { value: "glycine", label: "Glicina" },
  { value: "proline", label: "Prolina" },
];

export const RAMA_SHOW: { value: RamaShow; label: string }[] = [
  { value: "all", label: "Tutti" },
  { value: "flagged", label: "Consentiti e outlier" },
  { value: "outliers", label: "Solo outlier" },
];

export const VIEW_TITLE: Record<RamaView, string> = {
  all: "Tutti i residui",
  general: "Caso generale (anche pre-prolina)",
  glycine: "Glicina",
  proline: "Prolina",
};

/** Etichetta lunga della regione (legenda, dettaglio e dati del grafico). */
export const REGION_LABEL: Record<RamaRegion, string> = {
  favored: "Favorita",
  allowed: "Consentita",
  outlier: "Non consentita (outlier)",
};

/** Etichetta breve della regione, per tabelle, suggerimenti e dettagli dei residui: la stessa in tutta l'interfaccia. */
export const RAMA_SHORT_LABEL: Record<RamaRegion, string> = { favored: "favorita", allowed: "consentita", outlier: "outlier" };

/** Esito della regione (colore di badge e statistiche). */
export const RAMA_TONE = { favored: "ok", allowed: "info", outlier: "err" } as const satisfies Record<RamaRegion, string>;

const CLASS_LABEL: Record<string, string> = { general: "Generale", prepro: "Pre-prolina", glycine: "Glicina", proline: "Prolina" };

export const classLabel = (cls: string) => CLASS_LABEL[cls] ?? cls;

/** Il punto appartiene alla vista (classe di residui). Nome distinto da `inView` dei grafici lineari (tratti e asse). */
export function inRamaView(p: RamaPoint, view: RamaView): boolean {
  if (view === "all") return true;
  // la pre-prolina si valuta con le regioni del caso generale (come nel server)
  if (view === "general") return p.class === "general" || p.class === "prepro";
  return p.class === view;
}

/** Regioni sotto i punti: la vista "Tutti" usa quelle del caso generale; vuote se il rapporto non le ha. */
export function viewRegions(regions: Record<string, RamaRegions> | undefined, view: RamaView): RamaRegions {
  return regions?.[view === "all" ? "general" : view] ?? { favored: [], allowed: [] };
}

export function showPoint(p: RamaPoint, show: RamaShow): boolean {
  if (show === "outliers") return p.region === "outlier";
  return show === "all" || p.region !== "favored";
}

export function regionCounts(points: RamaPoint[]) {
  const c = { total: points.length, favored: 0, allowed: 0, outlier: 0 };
  for (const p of points) {
    if (p.region === "favored" || p.region === "allowed" || p.region === "outlier") c[p.region]++;
  }
  return c;
}

/** Sottotitolo del grafico: vista, numero di residui, quota nelle regioni favorite, outlier. */
export function ramaSubtitle(view: RamaView, points: RamaPoint[]): string {
  const c = regionCounts(points);
  if (!c.total) return `${VIEW_TITLE[view]} · nessun residuo`;
  const parts = [VIEW_TITLE[view], `${c.total} ${c.total === 1 ? "residuo" : "residui"}`, `${fmtNum((100 * c.favored) / c.total, 1)}% in regioni favorite`];
  if (c.outlier) parts.push(`${c.outlier} outlier`);
  return parts.join(" · ");
}

/** Angolo in gradi all'italiana con il segno meno tipografico ("−63,2"). */
export function fmtAngle(v: number, digits = 0): string {
  return fmtNum(v, digits).replace("-", "−");
}

const isDigit = (c: string | undefined) => c !== undefined && c >= "0" && c <= "9";

/**
 * Ricerca di un residuo per nome, catena e numero ("ASP A45", "a45", "A:45", "gly", "45"), senza badare a maiuscole,
 * spazi e due punti. I numeri valgono interi: "A45" non trova A450 né "45" A145 (il codice di inserzione di A45A
 * sì). Testo vuoto: null (nessuna ricerca).
 */
export function residueMatcher(query: string): ((p: RamaPoint) => boolean) | null {
  const compact = (s: string) => s.toLowerCase().replace(/[\s:]+/g, "");
  const q = compact(query);
  if (!q) return null;
  return (p) => {
    const s = compact(residueName(p));
    // inizio del numero: con una catena numerica ("1" + "12") la cifra prima non lo allunga
    const numAt = s.length - compact(p.resnum).length;
    for (let i = s.indexOf(q); i >= 0; i = s.indexOf(q, i + 1)) {
      if (isDigit(q[0]) && isDigit(s[i - 1]) && i !== numAt) continue;
      if (isDigit(q[q.length - 1]) && isDigit(s[i + q.length])) continue;
      return true;
    }
    return false;
  };
}

/** Dati della scheda "Dati" e del CSV: un residuo per riga, etichette in italiano. */
export function ramaData(points: RamaPoint[]): ChartData {
  return {
    columns: [
      { key: "residuo", label: "Residuo" },
      { key: "catena", label: "Catena" },
      { key: "numero", label: "Numero" },
      { key: "nome", label: "Nome" },
      { key: "classe", label: "Classe" },
      { key: "phi", label: "φ (°)", num: true },
      { key: "psi", label: "ψ (°)", num: true },
      { key: "regione", label: "Regione" },
    ],
    rows: points.map((p) => ({
      residuo: residueName(p),
      catena: p.chain,
      numero: p.resnum,
      nome: p.resname,
      classe: classLabel(p.class),
      phi: p.phi,
      psi: p.psi,
      regione: REGION_LABEL[p.region] ?? p.region,
    })),
  };
}
