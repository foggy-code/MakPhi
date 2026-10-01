// Rifinitura energetica (F9.1): opzioni della richiesta, link (Qualità, Confronto) e testi del confronto prima/dopo.
// Logica pura; il protocollo è nel backend (domain/refinement.py).

import type { RefineComparisonRow, RefineResult, StructureSource } from "../api/types";
import { compareLink } from "./compare";
import { fmtNum } from "./format";

/** Durata della dinamica (ps): predefinita e massima, come domain/refinement.MD_DEFAULT_PS e MD_MAX_PS. */
export const MD_DEFAULT_PS = 10;
export const MD_MAX_PS = 50;

/** Corpo della richiesta dalle scelte della finestra, oppure il motivo per cui non si può lanciare. */
export function refineRequest(md: boolean, ps: number, hydrogens: boolean): { body: { md_ps: number; hydrogens: boolean } | null; error: string | null } {
  if (!md) return { body: { md_ps: 0, hydrogens }, error: null };
  if (!Number.isFinite(ps) || ps <= 0) return { body: null, error: "Indica la durata della dinamica (ps)." };
  if (ps > MD_MAX_PS) return { body: null, error: `La dinamica dura al massimo ${MD_MAX_PS} ps.` };
  return { body: { md_ps: ps, hydrogens }, error: null };
}

/** Il modello rifinito come sorgente di struttura (Qualità, Confronto, rifinitura successiva). */
export function refinedSource(jobId: string, result: Pick<RefineResult, "refined_file">): StructureSource {
  return { type: "job", job_id: jobId, file: result.refined_file };
}

/** Pagina Qualità con il rapporto del modello rifinito (salvato dal job accanto al modello). */
export function refinedQualityLink(jobId: string, result: Pick<RefineResult, "refined_file">): string {
  return `/quality?job=${encodeURIComponent(jobId)}&file=${encodeURIComponent(result.refined_file)}`;
}

/** Confronto strutturale: la struttura di partenza (A, riferimento) e il modello rifinito (B). */
export function refineCompareLink(jobId: string, result: Pick<RefineResult, "refined_file" | "source">): string {
  return compareLink(result.source, refinedSource(jobId, result));
}

const DIGITS: Record<RefineComparisonRow["key"], number> = { grade_score: 1, rama_favored: 1, rama_outliers: 1, clashscore: 2, dope: 0, zdope: 2 };

export function fmtRefineValue(row: Pick<RefineComparisonRow, "key">, value: number | null | undefined): string {
  return fmtNum(value ?? null, DIGITS[row.key]);
}

/** Differenza con il segno ("+1,2", "−6,50"); "—" se manca un valore. */
export function fmtDelta(row: Pick<RefineComparisonRow, "key" | "delta">): string {
  if (row.delta == null) return "—";
  const text = fmtNum(Math.abs(row.delta), DIGITS[row.key]);
  return row.delta > 0 ? `+${text}` : row.delta < 0 ? `−${text}` : text;
}

export function verdictTone(verdict: RefineComparisonRow["verdict"]): "ok" | "err" | "info" | undefined {
  return verdict === "meglio" ? "ok" : verdict === "peggio" ? "err" : verdict === "invariato" ? "info" : undefined;
}

/** "710 → −12 448 kJ/mol (−13 158)". */
export function energyText(energy: Pick<RefineResult["energy"], "before" | "after" | "delta" | "unit">): string {
  if (energy.before == null || energy.after == null) return "—";
  const signed = (v: number) => (v < 0 ? `−${fmtNum(Math.abs(v), 0)}` : fmtNum(v, 0));
  return `${signed(energy.before)} → ${signed(energy.after)} ${energy.unit}${energy.delta != null ? ` (${energy.delta > 0 ? "+" : ""}${signed(energy.delta)})` : ""}`;
}

/** Sintesi del confronto: quante metriche migliorano e quante peggiorano. */
export function comparisonSummary(rows: RefineComparisonRow[]): { better: number; worse: number; same: number } {
  return {
    better: rows.filter((r) => r.verdict === "meglio").length,
    worse: rows.filter((r) => r.verdict === "peggio").length,
    same: rows.filter((r) => r.verdict === "invariato").length,
  };
}
