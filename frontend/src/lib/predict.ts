// Confronto con un predittore esterno (F9.3): consenso all'invio, richiesta e controlli preventivi del job `predict`,
// link e testi dell'esito. Logica pura; le regole sono nel backend (domain/prediction.py, engines/remote.py).

import type {
  ConsensusResult, LatestToolJob, Plausibility, PlausibilityStatus, PlausibilityTone, PredictorName, PredictRequest, PredictResult, PreflightRequest, Settings,
  StructureSource,
} from "../api/types";
import { compareLink } from "./compare";
import { fmtNum } from "./format";

/** Limiti della sequenza per ESMFold, come engines/remote.ESMFOLD_MAX_LENGTH e ESMFOLD_MIN_LENGTH (il servizio pubblico
 *  risponde 413 oltre 400 residui). */
export const ESMFOLD_MAX_LENGTH = 400;
export const ESMFOLD_MIN_LENGTH = 10;
/** Servizio pubblico di ESM Atlas (impostazione `esmfold_url` vuota), come engines/remote.ESMFOLD_URL. */
export const ESMFOLD_PUBLIC_URL = "https://api.esmatlas.com/foldSequence/v1/pdb/";

export const PREDICTOR_LABEL: Record<PredictorName, string> = { esmfold: "ESMFold", alphafold: "AlphaFold DB" };

export const STATUS_TONE: Record<PlausibilityStatus, PlausibilityTone> = {
  concordano: "ok", "divergenze locali": "warn", "ripiegamento diverso": "err", "non conclusivo": "info",
};

const STATUS_TITLE: Record<PlausibilityStatus, string> = {
  concordano: "Il modello concorda con la predizione",
  "divergenze locali": "Il modello diverge dalla predizione dove questa è sicura",
  "ripiegamento diverso": "Il modello ha un ripiegamento diverso dalla predizione",
  "non conclusivo": "Confronto non conclusivo",
};

/** Indirizzo effettivo del servizio ESMFold: quello delle Impostazioni o il servizio pubblico. */
export function esmfoldService(settings: Pick<Settings, "esmfold_url"> | null | undefined): string {
  return settings?.esmfold_url?.trim() || ESMFOLD_PUBLIC_URL;
}

/** Il consenso all'invio va chiesto prima dei controlli e dell'avvio: solo per ESMFold (la sequenza lascia il
 *  computer) e solo se non è già dato. Con le impostazioni non ancora lette si chiede (nel dubbio, non si invia). */
export function needsConsent(predictor: PredictorName, settings: Pick<Settings, "esmfold_remote"> | null | undefined): boolean {
  return predictor === "esmfold" && settings?.esmfold_remote !== true;
}

/** Testo della richiesta di consenso: che cosa lascia il computer, verso dove e che cosa no. */
export function consentText(service: string, residues: number | null): string {
  const what = residues ? `la sequenza della catena (${residues} residui)` : "la sequenza della catena";
  return `Per la predizione ${what} viene inviata al servizio ESMFold (${service}). Le coordinate del modello non lasciano il computer; la predizione resta in cache su questo computer per 30 giorni. Il consenso vale per tutte le predizioni successive e si revoca in Impostazioni → Generale.`;
}

/** Corpo di POST /api/predict per la struttura da confrontare, oppure il motivo per cui non si può lanciare. */
export function predictRequest(
  predictor: PredictorName,
  source: StructureSource | null | undefined,
  opts: { accession?: string; projectId?: string | null; chain?: string | null } = {},
): { body: PredictRequest | null; error: string | null } {
  if (!source) return { body: null, error: "Scegli la struttura da confrontare." };
  const accession = (opts.accession ?? "").trim();
  if (predictor === "alphafold" && !accession) return { body: null, error: "Indica l'accessione UniProt del modello di AlphaFold DB." };
  const chain = (opts.chain ?? source.chain ?? "").trim() || null;
  return {
    body: {
      ...(opts.projectId ? { project_id: opts.projectId } : {}),
      predictor, source, chain, ...(predictor === "alphafold" ? { accession } : {}),
    },
    error: null,
  };
}

/** Controlli preventivi del job `predict` (lo stesso corpo della rotta di avvio); null senza richiesta valida. */
export function predictPreflight(body: PredictRequest | null): PreflightRequest | null {
  return body ? { kind: "predict", params: body as unknown as Record<string, unknown> } : null;
}

/** Sequenza troppo lunga per ESMFold (il controllo vero è nel backend, prima dell'invio). */
export function tooLongForEsmfold(residues: number | null | undefined): boolean {
  return residues != null && residues > ESMFOLD_MAX_LENGTH;
}

/** Job da seguire per una struttura: quello già scelto, altrimenti quello in corso, altrimenti il più recente. */
export function followedJob(current: string | null, latest: LatestToolJob | null | undefined): string | null {
  return current ?? latest?.active_job_id ?? latest?.job_id ?? null;
}

/** Titolo dell'esito. */
export function verdictTitle(p: Pick<Plausibility, "status">): string {
  return STATUS_TITLE[p.status] ?? p.status;
}

/** Riga di sintesi delle metriche: "TM-score 0,927 · lDDT 0,878 (0,872 dove la predizione è sicura)". */
export function metricsLine(p: Pick<Plausibility, "tm_score" | "lddt" | "lddt_confident" | "confident_residues" | "pairs">): string {
  return `TM-score ${fmtNum(p.tm_score, 3)} · lDDT ${fmtNum(p.lddt, 3)}`
    + (p.lddt_confident != null ? ` (${fmtNum(p.lddt_confident, 3)} sui ${p.confident_residues} residui su ${p.pairs} dove la predizione è sicura)` : "");
}

/** Origine della predizione: servizio, cache, scala del pLDDT. */
export function originText(result: Pick<PredictResult, "predictor" | "origin" | "accession">): string {
  if (result.predictor === "alphafold") return `AlphaFold DB, modello ${result.origin.entry ?? result.accession ?? "—"} (a EBI va solo l'accessione)`;
  const parts = [`servizio ${result.origin.service}`];
  if (result.origin.cached) parts.push("dalla cache di questo computer");
  if (result.origin.scale === "0-1") parts.push("pLDDT del servizio su 0–1, riportato a 0–100");
  return parts.join(" · ");
}

/** La predizione come sorgente di struttura (Qualità, Confronto). */
export function predictionSource(jobId: string, result: Pick<PredictResult, "prediction_file">): StructureSource {
  return { type: "job", job_id: jobId, file: result.prediction_file };
}

/** Pagina Qualità della predizione (pLDDT riconosciuto dal riconoscitore unico). */
export function predictionQualityLink(jobId: string, result: Pick<PredictResult, "prediction_file">): string {
  return `/quality?job=${encodeURIComponent(jobId)}&file=${encodeURIComponent(result.prediction_file)}`;
}

/** Pagina Confronto: il modello (A, riferimento) e la predizione (B), da rilanciare con altre opzioni. */
export function predictionCompareLink(jobId: string, result: Pick<PredictResult, "prediction_file" | "source" | "chain">): string {
  const a = result.source ? { ...result.source, chain: result.chain ?? result.source.chain } : null;
  return compareLink(a, predictionSource(jobId, result));
}

/** Nota dell'accordo con la predizione nel consenso (solo se il confronto è incluso). */
export function consensusPredictorNote(c: ConsensusResult | null | undefined): { text: string; tone: PlausibilityTone } | null {
  const p = c?.predictor;
  if (!p) return null;
  return { text: p.text, tone: STATUS_TONE[p.status] ?? "info" };
}
