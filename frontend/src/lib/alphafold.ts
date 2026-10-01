// AlphaFold DB: accessioni UniProt, identificativi dei modelli e fasce di pLDDT (come domain/plddt.py del backend).
import type { PredictorName, PreparedTemplate, Sequence, TemplateItem, TemplateRef } from "../api/types";
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

/** Nome del predittore di un modello predetto, come domain/plddt.predicted_source del backend: «ESMFold» o
 *  «AlphaFold» dal predittore riconosciuto; senza predittore noto (metadati precedenti) «AlphaFold» per un
 *  identificativo di AlphaFold DB, altrimenti «predetto». */
export function predictorBadge(item: { predictor?: PredictorName | null; id?: string; code?: string } | null | undefined): string {
  if (item?.predictor === "esmfold") return "ESMFold";
  if (item?.predictor === "alphafold") return "AlphaFold";
  const id = item?.id ?? item?.code?.replace(/_[^_]*$/, "") ?? "";
  return isAlphaFoldId(id) ? "AlphaFold" : "predetto";
}

/** Suggerimento del distintivo: «Modello predetto da ESMFold, non struttura sperimentale». */
export function predictorTitle(item: { predictor?: PredictorName | null; id?: string; code?: string } | null | undefined): string {
  const name = predictorBadge(item);
  return name === "predetto" ? "Modello predetto, non struttura sperimentale" : `Modello predetto da ${name}, non struttura sperimentale`;
}

/** Accessione UniProt di una sequenza del progetto importata da UniProt (meta.accession), se valida. */
export function sequenceAccession(seq: Pick<Sequence, "meta"> | null | undefined): string {
  const acc = seq?.meta?.accession;
  return typeof acc === "string" && isUniProtAccession(acc) ? normalizeAccession(acc) : "";
}

// Nome con l'intestazione FASTA di UniProt: "sp|P69905|HBA_HUMAN", anche già ripulito ("sp_P69905_HBA_HUMAN")
const UNIPROT_NAME = /^(?:sp|tr)[|_]([A-Z0-9]{6,10})(?:[|_]|$)/i;

/** Accessione da un nome con l'intestazione FASTA di UniProt (sp|ACC|NOME o tr|ACC|NOME), altrimenti "". */
export function accessionFromName(name: string | null | undefined): string {
  const m = UNIPROT_NAME.exec((name ?? "").trim());
  return m && isUniProtAccession(m[1]) ? normalizeAccession(m[1]) : "";
}

/**
 * Nome per il multi-FASTA della pipeline: con l'accessione di una sequenza importata da UniProt diventa
 * "sp|ACC|NOME" ("tr|…" per le voci non revisionate), così la pipeline la usa con il motore AlphaFold DB (senza,
 * la sequenza andrebbe a NCBI BLAST); il backend riconosce entrambe le forme. Gli altri nomi restano come sono.
 */
export function pipelineFastaName(seq: Pick<Sequence, "name" | "meta">): string {
  const acc = sequenceAccession(seq);
  if (!acc || accessionFromName(seq.name)) return seq.name;
  return `${seq.meta?.reviewed === false ? "tr" : "sp"}|${acc}|${seq.name}`;
}

/** Accessione precompilata e la sequenza da cui viene (pagina Ricerca). */
export interface AutoAccession {
  accession: string;
  sequence: string;
}

const cleanSequence = (s: string) => s.replace(/[^A-Za-z]/g, "").toUpperCase();

/**
 * Campo "Accessione UniProt" dopo un cambio della sequenza senza scegliere una sequenza del progetto (incollata o
 * da file). L'accessione precompilata segue la sequenza da cui viene: con un'altra sequenza si toglie, a meno che
 * l'utente non l'abbia cambiata a mano; un'intestazione FASTA di UniProt nel nome (sp|ACC|…) la precompila.
 */
export function followAccession(current: string, auto: AutoAccession | null, next: { name: string; sequence: string }): { uniprot: string; auto: AutoAccession | null } {
  const fromName = accessionFromName(next.name);
  const untouched = !current.trim() || (!!auto && normalizeAccession(current) === auto.accession);
  if (fromName && untouched) return { uniprot: fromName, auto: { accession: fromName, sequence: cleanSequence(next.sequence) } };
  if (!auto || cleanSequence(next.sequence) === auto.sequence) return { uniprot: current, auto };
  return { uniprot: untouched ? "" : current, auto: null };
}

/** Precompilazione da una sequenza del progetto (o da ?seq=): l'accessione e la sequenza a cui appartiene. */
export function autoAccessionFor(seq: Pick<Sequence, "meta" | "sequence">): AutoAccession | null {
  const accession = sequenceAccession(seq);
  return accession ? { accession, sequence: cleanSequence(seq.sequence) } : null;
}

export type PlddtBand = "very_high" | "confident" | "low" | "very_low";

export const PLDDT_BAND_LABEL: Record<PlddtBand, string> = {
  very_high: "molto alta",
  confident: "confidente",
  low: "bassa",
  very_low: "molto bassa",
};

/** Limite inferiore (compreso) delle fasce di AlphaFold DB, come domain/plddt.BANDS e il ritaglio del backend. */
export const PLDDT_BAND_MIN = { very_high: 90, confident: 70, low: 50 } as const;

/** Fascia di confidenza (limite inferiore compreso): ≥ 90, 70–90, 50–70, < 50. */
export function plddtBand(value: number): PlddtBand {
  if (value >= PLDDT_BAND_MIN.very_high) return "very_high";
  if (value >= PLDDT_BAND_MIN.confident) return "confident";
  if (value >= PLDDT_BAND_MIN.low) return "low";
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

/**
 * Riepilogo del ritaglio di un template preparato: "rimossi 11 residui con pLDDT < 70 (restano 65 di 76)", con i
 * frammenti isolati se ce ne sono ("rimossi 20 residui con pLDDT < 70 e 4 in frammenti isolati (restano …)").
 */
export function trimSummary(t: Pick<PreparedTemplate, "predicted" | "plddt">): string | null {
  if (!t.predicted) return null;
  const p = t.plddt;
  if (!p) return "modello predetto, usato senza ritaglio pLDDT";
  if (!p.removed) return `nessun residuo con pLDDT < ${fmtNum(p.threshold, 0)}`;
  const below = p.removed_threshold ?? p.removed;
  const fragments = p.removed_fragments ?? 0;
  const n = below === 1 ? "1 residuo" : `${below} residui`;
  const verb = p.removed === 1 ? "rimosso" : "rimossi";
  const extra = fragments ? ` e ${fragments} in frammenti isolati (< ${p.min_fragment ?? 5} residui)` : "";
  return `${verb} ${n} con pLDDT < ${fmtNum(p.threshold, 0)}${extra} (restano ${p.kept} di ${p.total})`;
}

/** Intervalli di residui tolti: "1–5:A, 71–76:A". */
export function removedRanges(t: Pick<PreparedTemplate, "plddt">): string {
  return (t.plddt?.removed_ranges ?? []).map(([a, b, c]) => (a === b ? `${a}:${c}` : `${a}–${b}:${c}`)).join(", ");
}
