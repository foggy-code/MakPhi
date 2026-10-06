// Pipeline M × N (ogni sequenza su ogni template): metriche della matrice sequenze × template, colori delle celle e
// riepilogo per sequenza. Logica pura, usata da components/PipelineMatrix.tsx.
import type { PipelineMatrix, PipelineMatrixCell } from "../api/types";
import { colorFor, mixColors, type ContinuousScale } from "./colorscale";

export type MatrixMetric = "zdope" | "dope" | "ga341" | "molpdf" | "identity" | "coverage" | "grade_score";

export interface MatrixMetricDef {
  key: MatrixMetric;
  label: string;
  /** come si legge il valore (anche nel suggerimento del selettore) */
  note: string;
  digits: number;
  /** colori relativi alla riga: DOPE e molpdf si confrontano solo fra template della stessa sequenza */
  perRow?: "low";
  scale?: ContinuousScale;
}

// verde = buono, giallo = intermedio, rosso = scarso; esadecimali fissi (il testo della cella sceglie nero o bianco)
export const GOOD = "#1a9850";
export const MID = "#fee08b";
export const BAD = "#d73027";

export const MATRIX_METRICS: Record<MatrixMetric, MatrixMetricDef> = {
  zdope: {
    key: "zdope", label: "zDOPE", digits: 2,
    note: "DOPE normalizzato: più basso è migliore, sotto −1 simile a una struttura nativa; è il criterio del migliore della sequenza",
    scale: { kind: "continuous", stops: [{ value: -2, color: GOOD }, { value: -1, color: "#91cf60" }, { value: 0, color: MID }, { value: 1, color: BAD }] },
  },
  dope: { key: "dope", label: "DOPE", digits: 0, perRow: "low", note: "energia DOPE: più bassa è migliore; colori relativi alla sequenza (confrontabile solo fra i suoi template)" },
  ga341: {
    key: "ga341", label: "GA341", digits: 2, note: "affidabilità del ripiegamento: sopra 0,7 affidabile",
    scale: { kind: "continuous", stops: [{ value: 0, color: BAD }, { value: 0.7, color: MID }, { value: 1, color: GOOD }] },
  },
  molpdf: { key: "molpdf", label: "molpdf", digits: 0, perRow: "low", note: "funzione obiettivo di MODELLER: più bassa è migliore; colori relativi alla sequenza" },
  identity: {
    key: "identity", label: "Identità %", digits: 1, note: "identità del template sulla sequenza nell'allineamento: sotto il 25–30% zona crepuscolare",
    scale: { kind: "continuous", stops: [{ value: 0, color: BAD }, { value: 30, color: MID }, { value: 60, color: GOOD }, { value: 100, color: GOOD }] },
  },
  coverage: {
    key: "coverage", label: "Copertura %", digits: 1, note: "residui della sequenza coperti dal template",
    scale: { kind: "continuous", stops: [{ value: 0, color: BAD }, { value: 40, color: MID }, { value: 100, color: GOOD }] },
  },
  grade_score: {
    key: "grade_score", label: "Voto", digits: 0, note: "punteggio del voto BioLoom (Ramachandran, clash, zDOPE): più alto è migliore",
    scale: { kind: "continuous", stops: [{ value: 40, color: BAD }, { value: 55, color: BAD }, { value: 70, color: MID }, { value: 85, color: GOOD }, { value: 100, color: GOOD }] },
  },
};

export const MATRIX_ORDER: MatrixMetric[] = ["zdope", "dope", "ga341", "molpdf", "identity", "coverage", "grade_score"];

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function cellValue(cell: PipelineMatrixCell | null | undefined, metric: MatrixMetric): number | null {
  const v = cell?.[metric];
  return isNum(v) ? v : null;
}

/** Metriche con almeno un valore nella matrice, nell'ordine del selettore. */
export function availableMetrics(matrix: PipelineMatrix): MatrixMetric[] {
  return MATRIX_ORDER.filter((m) => matrix.cells.some((row) => row.some((c) => cellValue(c, m) != null)));
}

/**
 * Colore della cella: scala fissa della metrica, oppure (DOPE, molpdf) posizione fra il minimo (verde) e il massimo
 * (rosso) della stessa riga; null senza valore.
 */
export function cellColor(row: (PipelineMatrixCell | null)[], cell: PipelineMatrixCell | null, metric: MatrixMetric): string | null {
  const value = cellValue(cell, metric);
  if (value == null) return null;
  const def = MATRIX_METRICS[metric];
  if (def.scale) return colorFor(def.scale, value);
  const values = row.map((c) => cellValue(c, metric)).filter((v): v is number => v != null);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (hi === lo) return GOOD;
  const t = (value - lo) / (hi - lo); // 0 = il più basso della riga (migliore)
  return t <= 0.5 ? mixColors(GOOD, MID, t * 2) : mixColors(MID, BAD, (t - 0.5) * 2);
}

export interface MatrixSummary {
  pairs: number;
  completed: number;
  failed: number;
  open: number;
  /** sequenze con il migliore già scelto (tutte le coppie concluse) */
  closed: number;
  /** quante volte ogni template è il migliore di una sequenza, nell'ordine delle colonne */
  wins: number[];
}

const OPEN_STATUSES = new Set(["pending", "running", "searching", "modeling"]);

export function matrixSummary(matrix: PipelineMatrix): MatrixSummary {
  const wins = matrix.templates.map(() => 0);
  let pairs = 0;
  let completed = 0;
  let failed = 0;
  let open = 0;
  let closed = 0;
  for (const row of matrix.cells) {
    let best = false;
    row.forEach((c, t) => {
      if (!c) return;
      pairs += 1;
      if (c.status === "completed") completed += 1;
      else if (c.status === "failed") failed += 1;
      else if (OPEN_STATUSES.has(c.status)) open += 1;
      if (c.best) {
        best = true;
        wins[t] += 1;
      }
    });
    if (best) closed += 1;
  }
  return { pairs, completed, failed, open, closed, wins };
}

/** Testo di una cella senza valore: stato della coppia (in attesa, in corso, fallita…). */
export function cellStatusText(cell: PipelineMatrixCell | null): string {
  if (!cell) return "—";
  if (cell.status === "failed") return "fallita";
  if (cell.status === "cancelled") return "annullata";
  if (cell.status === "pending") return "in attesa";
  if (OPEN_STATUSES.has(cell.status)) return "in corso";
  return "—";
}

/** Righe della pagina (le matrici con migliaia di sequenze si sfogliano a pagine). */
export function matrixPage<T>(rows: T[], page: number, size: number): { rows: T[]; page: number; pages: number; start: number } {
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const current = Math.min(Math.max(1, page), pages);
  const start = (current - 1) * size;
  return { rows: rows.slice(start, start + size), page: current, pages, start };
}
