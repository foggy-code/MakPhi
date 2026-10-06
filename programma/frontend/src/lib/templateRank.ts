// Scelta ragionata dei template (domain/template_rank.py del backend): fasce del punteggio, motivazioni, template
// consigliati e collegamento alla Modellazione, template scelti di un elemento di pipeline in modalità multi.
import type { PipelineItem, RankBand, RankReason, SearchHit, SearchResult, SuggestedTemplate, TemplateRef } from "../api/types";
import { fmtNum } from "./format";

/** Confini delle fasce, come nel backend (e nel consenso di qualità): buona ≥ 70, intermedia 40–70, scarsa < 40. */
export const RANK_GOOD = 70;
export const RANK_POOR = 40;

/** Template complementari nella Pipeline: predefinito 3 (Larsson et al. 2008), da 2 a 5 (api/compute.PipelineIn). */
export const MAX_TEMPLATES_DEFAULT = 3;
export const MAX_TEMPLATES_MIN = 2;
export const MAX_TEMPLATES_MAX = 5;

export function rankBand(score: number): RankBand {
  if (score >= RANK_GOOD) return "buona";
  if (score >= RANK_POOR) return "intermedia";
  return "scarsa";
}

export const RANK_BAND_TONE: Record<RankBand, "ok" | "warn" | "err"> = { buona: "ok", intermedia: "warn", scarsa: "err" };

/** Colore del badge del punteggio; undefined per i risultati senza punteggio (versioni precedenti). */
export function rankTone(score: number | null | undefined): "ok" | "warn" | "err" | undefined {
  if (score === null || score === undefined || Number.isNaN(score)) return undefined;
  return RANK_BAND_TONE[rankBand(score)];
}

/** Riga di una componente: "Identità · 97,6/100 (peso 30): …"; informativa o non disponibile senza punteggio. */
export function reasonLine(r: RankReason): string {
  const head = r.score === null ? (r.weight > 0 ? `${r.label} · non conteggiata` : `${r.label} · informativa`) : `${r.label} · ${fmtNum(r.score, 1)}/100 (peso ${fmtNum(r.weight, 0)})`;
  return `${head}: ${r.text}`;
}

/** Testo del tooltip del punteggio: punteggio, fascia e una riga per componente. */
export function rankTooltip(hit: Pick<SearchHit, "rank_score" | "rank_reasons" | "rank_twilight">): string {
  if (hit.rank_score === undefined) return "Punteggio non disponibile: ricerca di una versione precedente (rilanciala).";
  const lines = [`Punteggio ${fmtNum(hit.rank_score, 1)}/100, fascia ${rankBand(hit.rank_score)}${hit.rank_twilight ? " · zona crepuscolare (sotto la curva di Rost)" : ""}`];
  for (const r of hit.rank_reasons ?? []) lines.push(`• ${reasonLine(r)}`);
  return lines.join("\n");
}

/** Componenti che contano nel punteggio prima, poi quelle informative o non disponibili. */
export function sortedReasons(reasons: RankReason[] | undefined): RankReason[] {
  return [...(reasons ?? [])].sort((a, b) => Number(b.score !== null) - Number(a.score !== null) || b.weight - a.weight);
}

/**
 * Ruolo di ogni template consigliato (per id): il principale e i complementari della selezione del backend. Per i
 * risultati senza selezione (versioni precedenti) il solo `best`, come principale.
 */
export function suggestedRoles(result: Pick<SearchResult, "suggested" | "best" | "hits">): Map<string, SuggestedTemplate | null> {
  const out = new Map<string, SuggestedTemplate | null>();
  if (result.suggested?.templates?.length) {
    for (const t of result.suggested.templates) out.set(t.template_id, t);
  } else if (result.best) {
    out.set(result.best, null);
  }
  return out;
}

/** Template consigliati nel formato della Modellazione (id della libreria e catena). */
export function suggestionTemplates(result: Pick<SearchResult, "suggested" | "best" | "hits">): TemplateRef[] {
  if (result.suggested?.templates?.length) {
    return result.suggested.templates.map((t) => ({ id: t.pdb_id, chains: t.chain ? [t.chain] : [] }));
  }
  const best = result.hits.find((h) => h.template_id === result.best);
  return best ? [{ id: best.pdb_id, chains: best.chain ? [best.chain] : [] }] : [];
}

/**
 * Collegamento alla pagina Modellazione precompilata con la sequenza e i template (un `t=ID:CATENA` per template,
 * come «Modella con questo template»); vuoto se non c'è alcun template.
 */
export function modelingLink(result: Pick<SearchResult, "query">, templates: TemplateRef[]): string {
  if (!templates.length) return "";
  const params = new URLSearchParams();
  params.set("name", result.query.name);
  params.set("seqtext", result.query.sequence);
  for (const t of templates) params.append("t", t.chains.length ? `${t.id}:${t.chains.join("")}` : t.id);
  return `/modeling?${params.toString()}`;
}

/** Numero di template della modalità multi entro i limiti del server; il predefinito se non è un numero. */
export function clampMaxTemplates(value: number): number {
  if (!Number.isFinite(value)) return MAX_TEMPLATES_DEFAULT;
  return Math.min(MAX_TEMPLATES_MAX, Math.max(MAX_TEMPLATES_MIN, Math.round(value)));
}

export interface ItemTemplate {
  id: string;
  role?: "principale" | "complementare";
  reason?: string;
}

/**
 * Template usati da un elemento di pipeline: quelli scelti in modalità multi (con ruolo e motivazione, dalle
 * metriche) oppure l'elenco della colonna `template` ("1UBQ_A,2XYZ_B"), com'è per le modalità auto e fixed.
 */
export function itemTemplates(item: Pick<PipelineItem, "template" | "metrics">): ItemTemplate[] {
  const chosen = item.metrics?.templates;
  if (Array.isArray(chosen) && chosen.length) {
    return chosen.map((t) => ({ id: t.template_id, role: t.role, reason: t.reason }));
  }
  return (item.template ?? "").split(",").map((s) => s.trim()).filter(Boolean).map((id) => ({ id }));
}

/** Tooltip dei template di un elemento: una riga per template con ruolo e motivazione, più la copertura complessiva. */
export function itemTemplatesTooltip(item: Pick<PipelineItem, "template" | "metrics">): string | undefined {
  const list = itemTemplates(item).filter((t) => t.role);
  if (!list.length) return undefined;
  const lines = list.map((t) => `${t.id} (${t.role}): ${t.reason ?? ""}`.trim());
  const cov = item.metrics?.combined_coverage;
  if (typeof cov === "number") lines.push(`Copertura complessiva ${fmtNum(cov, 1)}%`);
  const excluded = item.metrics?.excluded_templates;
  if (typeof excluded === "number" && excluded > 0) lines.push(`${excluded} ${excluded === 1 ? "candidato escluso" : "candidati esclusi"} (motivi nel log)`);
  return lines.join("\n");
}
