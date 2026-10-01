// Controlli preventivi (F8a, domain/preflight.py del backend): esiti per livello, decisione sul pulsante di avvio
// (bloccato, conferma, pronto), testi e richieste di Modellazione e Pipeline. Logica pura, testata.
import type {
  PipelineRequest, PreflightCheck, PreflightEstimate, PreflightLevel, PreflightRequest, PreflightResult, TemplateRef,
} from "../api/types";
import { modelingCoverageRequest } from "./disorder";

export const LEVEL_TONE: Record<PreflightLevel, "err" | "warn" | "info"> = { error: "err", warning: "warn", info: "info" };
export const LEVEL_LABEL: Record<PreflightLevel, string> = { error: "errore", warning: "avviso", info: "informazione" };

/** Esiti divisi per livello; quelli di copertura (disordine e domini, `group: "coverage"`) a parte: l'interfaccia li
 *  mostra con CoverageAlerts, dalla verifica di copertura della stessa risposta. */
export function splitChecks(result: PreflightResult | null | undefined): {
  errors: PreflightCheck[]; warnings: PreflightCheck[]; infos: PreflightCheck[]; coverage: PreflightCheck[];
} {
  const out = { errors: [] as PreflightCheck[], warnings: [] as PreflightCheck[], infos: [] as PreflightCheck[], coverage: [] as PreflightCheck[] };
  for (const c of result?.checks ?? []) {
    if (c.group === "coverage") out.coverage.push(c);
    else if (c.level === "error") out.errors.push(c);
    else if (c.level === "warning") out.warnings.push(c);
    else out.infos.push(c);
  }
  return out;
}

export type LaunchState = "ready" | "confirm" | "blocked" | "checking" | "unchecked";

export interface LaunchDecision {
  state: LaunchState;
  /** perché il pulsante è disattivato (il primo errore) */
  reason: string | null;
  /** avvisi da confermare («Avvia comunque»), copertura compresa */
  warnings: PreflightCheck[];
}

/**
 * Che cosa fa il pulsante di avvio con l'esito dei controlli.
 * - `checking`: controlli in corso o superati da una modifica (al clic si ripetono subito);
 * - `unchecked`: i controlli non sono riusciti (server): l'avvio resta possibile, come prima dei controlli, perché
 *   la rotta di avvio valida comunque la richiesta;
 * - `blocked`: almeno un errore, con il primo come motivo;
 * - `confirm`: avvisi da confermare;
 * - `ready`: nessun errore né avviso.
 */
export function launchDecision(result: PreflightResult | null | undefined, o: { pending?: boolean; failed?: boolean } = {}): LaunchDecision {
  if (o.pending) return { state: "checking", reason: null, warnings: [] };
  if (o.failed || !result) return { state: o.failed ? "unchecked" : "checking", reason: null, warnings: [] };
  const errors = result.checks.filter((c) => c.level === "error");
  if (errors.length) return { state: "blocked", reason: errors[0].message, warnings: [] };
  const warnings = result.checks.filter((c) => c.level === "warning");
  return warnings.length ? { state: "confirm", reason: null, warnings } : { state: "ready", reason: null, warnings: [] };
}

/** Testo della richiesta di conferma. */
export function confirmTitle(warnings: PreflightCheck[]): string {
  return warnings.length === 1 ? "C'è un avviso: vuoi avviare comunque?" : `Ci sono ${warnings.length} avvisi: vuoi avviare comunque?`;
}

/** Secondi come nel backend (domain/preflight.fmt_seconds): "45 s", "3 min", "1 h 20 min", "2 giorni". */
export function fmtSeconds(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 60) return `${Math.max(1, Math.round(s))} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 48 * 3600) {
    const total = Math.round(s / 60);
    const h = Math.floor(total / 60);
    const m = total % 60;
    return `${h} h${m ? ` ${m} min` : ""}`;
  }
  return `${Math.round(s / 86400)} giorni`;
}

/** "circa 14 s (da 7 s a 28 s)". */
export function estimateText(est: PreflightEstimate | null | undefined): string | null {
  if (!est) return null;
  return `circa ${fmtSeconds(est.seconds)} (da ${fmtSeconds(est.low)} a ${fmtSeconds(est.high)})`;
}

/** Riga di sintesi: "2 errori · 1 avviso · 3 informazioni"; "nessun problema" senza errori né avvisi. */
export function countsText(result: PreflightResult | null | undefined): string {
  if (!result) return "";
  const c = result.counts;
  const parts: string[] = [];
  if (c.error) parts.push(`${c.error} ${c.error === 1 ? "errore" : "errori"}`);
  if (c.warning) parts.push(`${c.warning} ${c.warning === 1 ? "avviso" : "avvisi"}`);
  if (!parts.length) parts.push("nessun problema");
  if (c.info) parts.push(`${c.info} ${c.info === 1 ? "informazione" : "informazioni"}`);
  return parts.join(" · ");
}

/** Chiave della richiesta: una risposta vale solo per la richiesta che l'ha prodotta. */
export const requestKey = (r: PreflightRequest | null | undefined) => (r ? JSON.stringify(r) : "");

/**
 * Controlli della Modellazione: stesso corpo di POST /api/model; null finché bersaglio e template (o il PIR) non sono
 * pronti, come la verifica di copertura che i controlli comprendono.
 */
export function modelingPreflight(o: {
  mode: "auto" | "pir";
  target: { name: string; sequence: string };
  templates: TemplateRef[];
  pirText: string | null;
  accession: string;
  body: Record<string, unknown>;
  searchJob?: string | null;
}): PreflightRequest | null {
  const ready = modelingCoverageRequest({ mode: o.mode, target: o.target, templates: o.templates, pirText: o.pirText, accession: o.accession, plddtMin: null });
  if (!ready) return null;
  const params = { ...o.body, alignment: o.mode === "pir" ? o.pirText : null };
  return { kind: "model", params, context: { accession: o.accession || null, search_job: o.searchJob ?? null } };
}

/** Controlli della Pipeline (stesso corpo di POST /api/pipeline); null senza sequenze. */
export function pipelinePreflight(body: PipelineRequest, sequences: number): PreflightRequest | null {
  if (sequences < 1) return null;
  return { kind: "pipeline", params: body as Record<string, unknown> };
}
