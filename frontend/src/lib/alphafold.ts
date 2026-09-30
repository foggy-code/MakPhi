// AlphaFold DB: accessioni UniProt, identificativi dei modelli e fasce di pLDDT (come domain/plddt.py del backend).
import type { PreparedTemplate, Sequence, TemplateItem, TemplateRef } from "../api/types";
import { fmtNum } from "./format";

/** Soglia predefinita del ritaglio: sotto 70 lo scheletro predetto non è affidabile (fasce di AlphaFold DB). */
export const PLDDT_DEFAULT = 70;

// Formato ufficiale delle accessioni UniProtKB (https://www.uniprot.org/help/accession_numbers)
const UNIPROT = /^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$/;
const AF_ID = /^AF-([A-Z0-9]{6,10})-F([1-9][0-9]{0,3})$/;

export const normalizeAccession = (value: string) => value.trim().toUpperCase();

export function isUniProtAccession(value: string): boolean {
  return UNIPROT.test(normalizeAccession(value));
}

/** Identificativo di un modello di AlphaFold DB nella libreria (AF-P69905-F1). */
export function isAlphaFoldId(id: string): boolean {
  const m = AF_ID.exec(id.trim().toUpperCase());
  return !!m && UNIPROT.test(m[1]);
}

/** Accessione UniProt di una sequenza del progetto importata da UniProt (meta.accession), se valida. */
export function sequenceAccession(seq: Pick<Sequence, "meta"> | null | undefined): string {
  const acc = seq?.meta?.accession;
  return typeof acc === "string" && isUniProtAccession(acc) ? normalizeAccession(acc) : "";
}

export type PlddtBand = "very_high" | "confident" | "low" | "very_low";

export const PLDDT_BAND_LABEL: Record<PlddtBand, string> = {
  very_high: "molto alta",
  confident: "confidente",
  low: "bassa",
  very_low: "molto bassa",
};

/** Fascia di confidenza (limite inferiore compreso): ≥ 90, 70–90, 50–70, < 50. */
export function plddtBand(value: number): PlddtBand {
  if (value >= 90) return "very_high";
  if (value >= 70) return "confident";
  if (value >= 50) return "low";
  return "very_low";
}

export const PLDDT_BAND_TONE: Record<PlddtBand, "ok" | "info" | "warn" | "err"> = {
  very_high: "ok",
  confident: "info",
  low: "warn",
  very_low: "err",
};

export function plddtTone(value: number | null | undefined): "ok" | "info" | "warn" | "err" | undefined {
  if (value === null || value === undefined || Number.isNaN(value)) return undefined;
  return PLDDT_BAND_TONE[plddtBand(value)];
}

/** "pLDDT 88,6 (confidente)"; "pLDDT —" se non noto. */
export function plddtLabel(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "pLDDT —";
  return `pLDDT ${fmtNum(value, 1)} (${PLDDT_BAND_LABEL[plddtBand(value)]})`;
}

/** Il template scelto è un modello predetto? (dalla libreria o, se non ancora scaricato, dall'identificativo) */
export function isPredictedTemplate(id: string, library: TemplateItem[] | null | undefined): boolean {
  const item = library?.find((t) => t.id === id.toUpperCase());
  return item ? !!item.predicted : isAlphaFoldId(id);
}

/** Almeno un template AlphaFold tra quelli scelti: l'opzione della soglia pLDDT ha senso solo allora. */
export function hasPredictedTemplate(templates: TemplateRef[], library: TemplateItem[] | null | undefined): boolean {
  return templates.some((t) => isPredictedTemplate(t.id, library));
}

/** Soglia valida (0–100) o la predefinita. */
export function clampThreshold(value: number): number {
  if (!Number.isFinite(value)) return PLDDT_DEFAULT;
  return Math.min(100, Math.max(0, value));
}

/** Riepilogo del ritaglio di un template preparato: "rimossi 11 residui con pLDDT < 70 (restano 65 di 76)". */
export function trimSummary(t: Pick<PreparedTemplate, "predicted" | "plddt">): string | null {
  if (!t.predicted) return null;
  const p = t.plddt;
  if (!p) return "modello predetto, usato senza ritaglio pLDDT";
  if (!p.removed) return `nessun residuo con pLDDT < ${fmtNum(p.threshold, 0)}`;
  const n = p.removed === 1 ? "1 residuo" : `${p.removed} residui`;
  return `${p.removed === 1 ? "rimosso" : "rimossi"} ${n} con pLDDT < ${fmtNum(p.threshold, 0)} (restano ${p.kept} di ${p.total})`;
}

/** Intervalli di residui tolti: "1–5:A, 71–76:A". */
export function removedRanges(t: Pick<PreparedTemplate, "plddt">): string {
  return (t.plddt?.removed_ranges ?? []).map(([a, b, c]) => (a === b ? `${a}:${c}` : `${a}–${b}:${c}`)).join(", ");
}
