// Procedura guidata "dalla sequenza al modello" (F8a): stato dei passi, validazione, passaggio dei dati fra i passi,
// ripresa dall'URL e dalla bozza salvata nel browser, motore di ricerca consigliato, opzioni predefinite motivate,
// richieste delle rotte di avvio. Logica pura, testata in wizard.test.ts; la pagina è pages/Wizard.tsx.
import type {
  Align2dResult, DatabaseStatus, Job, LigandRef, ModelResult, PreflightRequest, QualityReport, SearchEngine, SearchHit,
  SearchResult, SuggestedTemplate, TemplateRef,
} from "../api/types";
import { accessionFromName, isUniProtAccession, normalizeAccession, PLDDT_DEFAULT } from "./alphafold";
import { suggestedRoles, suggestionTemplates } from "./templateRank";

export type WizardStep = 1 | 2 | 3 | 4 | 5 | 6;

export const STEPS: { step: WizardStep; title: string; hint: string }[] = [
  { step: 1, title: "Sequenza", hint: "Il bersaglio: incollato, dal progetto o da UniProt" },
  { step: 2, title: "Template", hint: "Ricerca delle strutture omologhe e scelta" },
  { step: 3, title: "Allineamento", hint: "Bersaglio sui template con MODELLER" },
  { step: 4, title: "Opzioni", hint: "Modelli, raffinamento, ligandi e controlli" },
  { step: 5, title: "Avvio", hint: "Costruzione dei modelli in tempo reale" },
  { step: 6, title: "Risultati", hint: "Modello migliore, voto e consenso" },
];

/** Come services/compute.MAX_SEQUENCE: la sequenza più lunga che BioLoom modella. */
export const MAX_LENGTH = 5000;
/** Template al massimo per modello (services/compute._template_specs). */
export const MAX_TEMPLATES = 10;
/** Modelli predefiniti: 5, come l'esempio di base del tutorial di MODELLER (`a.ending_model = 5`). */
export const DEFAULT_MODELS = 5;

export type Refinement = "very_fast" | "fast" | "normal" | "thorough";

export interface WizardOptions {
  models: number;
  refinement: Refinement;
  quality: "best" | "all" | "none";
  /** PROCHECK e ProSA nell'analisi */
  extended: boolean;
  ligands: boolean;
  /** scelta dei gruppi eteroatomici (chiavi di lib/ligands.ligandKey) */
  ligandSel: Record<string, boolean>;
  plddtTrim: boolean;
  plddtMin: number;
}

export interface WizardState {
  step: WizardStep;
  target: { name: string; sequence: string };
  /** accessione UniProt del bersaglio (domini InterPro, AlphaFold DB); "" se non nota */
  accession: string;
  engine: SearchEngine;
  searchJob: string | null;
  templates: TemplateRef[];
  /** ricerca per cui i template sono stati scelti (o preselezionati): una volta sola per ricerca */
  templatesFor: string | null;
  alignJob: string | null;
  /** documento PIR del progetto (corretto nell'Editor PIR) al posto dell'allineamento del passo 3 */
  pirDoc: string | null;
  options: WizardOptions;
  /** l'utente ha cambiato le opzioni: i predefiniti non si ricalcolano più */
  optionsTouched: boolean;
  modelJob: string | null;
}

export function defaultOptions(): WizardOptions {
  return { models: DEFAULT_MODELS, refinement: "fast", quality: "best", extended: true, ligands: false, ligandSel: {}, plddtTrim: true, plddtMin: PLDDT_DEFAULT };
}

export function initialState(): WizardState {
  return {
    step: 1, target: { name: "", sequence: "" }, accession: "", engine: "rcsb", searchJob: null, templates: [], templatesFor: null,
    alignJob: null, pirDoc: null, options: defaultOptions(), optionsTouched: false, modelJob: null,
  };
}

// ---------------------------------------------------------------------------------- sequenza
export const cleanSequence = (s: string) => s.replace(/[^A-Za-z]/g, "").toUpperCase();

/** Sequenza nucleotidica: almeno il 90% di A, C, G, T/U, N (come sequences.detect_kind del backend). */
export function looksNucleic(sequence: string): boolean {
  const seq = cleanSequence(sequence);
  if (!seq) return false;
  const nucleic = [...seq].filter((c) => "ACGTUN".includes(c)).length;
  return nucleic / seq.length >= 0.9;
}

/** Perché la sequenza non si può usare (nessun motivo = pronta). */
export function sequenceIssues(target: { sequence: string }): string[] {
  const seq = cleanSequence(target.sequence);
  if (!seq) return ["Inserisci la sequenza bersaglio."];
  const out: string[] = [];
  if (seq.length > MAX_LENGTH) out.push(`Sequenza troppo lunga (${seq.length} residui, massimo ${MAX_LENGTH}): modella un dominio alla volta.`);
  if (looksNucleic(seq)) out.push("La sequenza sembra di un acido nucleico: serve la sequenza della proteina.");
  return out;
}

// ---------------------------------------------------------------------------------- template
/** Aggiunge o toglie il template di un hit: una catena per voce PDB (un'altra catena della stessa voce sostituisce la
 *  prima: due catene insieme sarebbero un template a più catene per un bersaglio a una catena). */
export function toggleTemplate(list: TemplateRef[], hit: Pick<SearchHit, "pdb_id" | "chain">): TemplateRef[] {
  const same = list.find((t) => t.id === hit.pdb_id);
  if (same && same.chains.length === 1 && same.chains[0] === hit.chain) return list.filter((t) => t !== same);
  if (same) return list.map((t) => (t === same ? { id: t.id, chains: [hit.chain] } : t));
  if (list.length >= MAX_TEMPLATES) return list;
  return [...list, { id: hit.pdb_id, chains: [hit.chain] }];
}

export const isChosen = (list: TemplateRef[], hit: Pick<SearchHit, "pdb_id" | "chain">) =>
  list.some((t) => t.id === hit.pdb_id && t.chains.includes(hit.chain));

/** Hit in ordine di punteggio (domain/template_rank.py); i risultati senza punteggio (versioni precedenti) per
 *  E-value. */
export function rankedHits(result: Pick<SearchResult, "hits">): SearchHit[] {
  return [...result.hits].sort((a, b) => (b.rank_score ?? -1) - (a.rank_score ?? -1) || a.evalue - b.evalue);
}

/** Ruolo di un hit fra i consigliati ("principale", "complementare") o null. */
export function hitRole(result: Pick<SearchResult, "suggested" | "best" | "hits">, hit: SearchHit): SuggestedTemplate["role"] | null {
  const roles = suggestedRoles(result);
  if (!roles.has(hit.template_id)) return null;
  return roles.get(hit.template_id)?.role ?? "principale";
}

/** Template preselezionati: i consigliati della ricerca, una volta sola per ricerca (poi decide l'utente). */
export function preselect(state: WizardState, searchJob: string, result: Pick<SearchResult, "suggested" | "best" | "hits">): WizardState {
  if (state.templatesFor === searchJob) return state;
  return { ...state, templates: suggestionTemplates(result).slice(0, MAX_TEMPLATES), templatesFor: searchJob };
}

export function templatesIssues(templates: TemplateRef[]): string[] {
  if (!templates.length) return ["Scegli almeno un template."];
  if (templates.some((t) => !t.chains.length)) return ["Ogni template deve avere una catena."];
  return [];
}

/** Suggerimento dopo la ricerca: nessun template, oppure il migliore in zona crepuscolare o in fascia scarsa. */
export function nextEngineHint(result: Pick<SearchResult, "hits" | "engine">, accession: string): string | null {
  const other = result.engine === "ncbi" ? "" : "NCBI BLAST (più sensibile per le omologie lontane, 1–5 min)";
  const af = result.engine === "alphafold" ? "" : `AlphaFold DB (modelli predetti${accession ? `, accessione ${accession}` : ""})`;
  const alternatives = [other, af].filter(Boolean).join(" oppure ");
  if (!result.hits.length) return alternatives ? `Nessun template: prova ${alternatives}.` : "Nessun template trovato.";
  const best = rankedHits(result)[0];
  if (best.rank_twilight || (best.rank_score !== undefined && best.rank_score < 40)) {
    return `Il template migliore (${best.template_id}) è ${best.rank_twilight ? "in zona crepuscolare" : "in fascia scarsa"}: il modello sarà poco affidabile.${alternatives ? ` Prova anche ${alternatives}.` : ""}`;
  }
  return null;
}

// ---------------------------------------------------------------------------------- motore consigliato
export interface EngineChoice {
  engine: SearchEngine;
  label: string;
  available: boolean;
  note: string;
}

/**
 * Motore consigliato e alternative spiegate. Predefinito RCSB (ricerca MMseqs2 del server su tutto il PDB, aggiornato
 * ogni settimana, pochi secondi); il motore delle Impostazioni se è un motore locale pronto (chi lo sceglie lavora
 * senza rete). Le alternative dicono quando servono e se sono disponibili su questo computer.
 */
export function engineChoices(o: { databases: DatabaseStatus | null; defaultEngine?: SearchEngine | null; accession: string }): { recommended: SearchEngine; reason: string; choices: EngineChoice[] } {
  const db = o.databases;
  const localOk = !!db?.pdbaa.usable && !!db.pdbaa.blast;
  const mmseqsOk = !!db?.mmseqs.usable && !!db.mmseqs.tool;
  const choices: EngineChoice[] = [
    { engine: "rcsb", label: "RCSB PDB", available: true, note: "Ricerca MMseqs2 sul server di RCSB in tutto il PDB aggiornato ogni settimana: di solito pochi secondi. Serve internet; la sequenza va a RCSB." },
    { engine: "ncbi", label: "NCBI BLAST", available: true, note: "Più sensibile per le omologie lontane (identità sotto il 30%), da 1 a 5 minuti; la sequenza va a NCBI." },
    { engine: "mmseqs", label: "MMseqs2 locale", available: mmseqsOk, note: mmseqsOk ? `Senza internet, sul database pdbaa del computer${db?.mmseqs.indexed_here ? " (indice veloce: circa 2 s)" : " (senza indice: circa 12 s)"}.` : "Non pronto: Impostazioni → Database → Prepara MMseqs2." },
    { engine: "local", label: "BLAST+ locale", available: localOk, note: localOk ? "Senza internet, BLAST sul database pdbaa del computer." : "Non pronto: serve il database pdbaa (Impostazioni → Database)." },
    { engine: "alphafold", label: "AlphaFold DB", available: true, note: o.accession ? `Modelli predetti per l'accessione ${o.accession} (a EBI va solo l'accessione): per le proteine senza strutture sperimentali omologhe.` : "Modelli predetti, per le proteine senza strutture sperimentali omologhe; senza accessione UniProt la sequenza va a NCBI BLAST (1–5 min)." },
  ];
  const preferred = o.defaultEngine && o.defaultEngine !== "rcsb" ? choices.find((c) => c.engine === o.defaultEngine && c.available) : undefined;
  if (preferred) return { recommended: preferred.engine, reason: `${preferred.label}: il motore scelto nelle Impostazioni, pronto su questo computer.`, choices };
  return { recommended: "rcsb", reason: "RCSB: rapido e sempre aggiornato; per le omologie lontane prova poi NCBI BLAST, per le proteine senza strutture AlphaFold DB.", choices };
}

// ---------------------------------------------------------------------------------- opzioni predefinite motivate
export interface OptionContext {
  /** almeno un template AlphaFold fra quelli scelti */
  predicted: boolean;
  /** gruppi eteroatomici che la scelta predefinita trasferisce (lib/ligands.defaultTransfer), per nome */
  ligandNames: string[];
  procheck: boolean;
  prosa: boolean;
}

/** Predefiniti ragionati per i template scelti (le scelte dell'utente restano: vedi `optionsTouched`). */
export function reasonedOptions(ctx: OptionContext, base: WizardOptions = defaultOptions()): WizardOptions {
  return { ...base, extended: ctx.procheck || ctx.prosa, ligands: ctx.ligandNames.length > 0, plddtTrim: true, plddtMin: PLDDT_DEFAULT };
}

/** Spiegazione di ogni opzione, per i valori correnti. */
export function optionNotes(o: WizardOptions, ctx: OptionContext): Record<"models" | "refinement" | "quality" | "ligands" | "plddt", string> {
  const models = o.models === DEFAULT_MODELS
    ? "5 modelli, come l'esempio di base del tutorial di MODELLER: MODELLER ne costruisce diversi dallo stesso allineamento e il migliore si sceglie per DOPE."
    : o.models < DEFAULT_MODELS
      ? "Pochi modelli: meno scelta per DOPE, ma più rapido (ogni modello circa 2,3 s per 76 residui, misurato)."
      : "Più modelli: più scelta per DOPE, il tempo cresce in proporzione (circa 2,3 s a modello per 76 residui, misurato).";
  const refinement = {
    very_fast: "Rapidissimo: ottimizzazione minima (circa un quarto del tempo di Rapido, misurato), per prove.",
    fast: "Rapido: il predefinito; in MODELLER 10.8 coincide con Standard (stesso livello di dinamica molecolare, misurato).",
    normal: "Standard: le impostazioni predefinite di MODELLER, che in MODELLER 10.8 coincidono con Rapido.",
    thorough: "Accurato: ottimizzazione ripetuta e dinamica lenta, circa 4 volte il tempo di Rapido (misurato): per il modello finale.",
  }[o.refinement];
  const tools = [ctx.procheck && "PROCHECK", ctx.prosa && "ProSA"].filter(Boolean).join(" e ");
  const quality = o.quality === "none" ? "Nessuna analisi: niente voto né consenso nei risultati."
    : `${o.quality === "all" ? "Su tutti i modelli" : "Sul modello migliore"}: Ramachandran, clash, DOPE e consenso${o.extended ? (tools ? `, più ${tools}` : ", più PROCHECK e ProSA (non disponibili qui: i controlli lo dicono)") : ""}.${!tools ? " PROCHECK e ProSA non sono utilizzabili su questo computer." : ""}`;
  const ligands = ctx.ligandNames.length
    ? `I template contengono ${ctx.ligandNames.join(", ")} (metalli, cofattori o ligandi): trasferirli mantiene il sito nel modello; BioLoom verifica contatti e coordinazione.`
    : "Nessun ligando, cofattore o metallo trasferibile nei template scelti (additivi e ioni del tampone esclusi).";
  const plddt = ctx.predicted
    ? `Template AlphaFold: tolti i residui con pLDDT < ${o.plddtMin} (fasce di AlphaFold DB: sotto 70 lo scheletro non è affidabile).`
    : "Nessun template AlphaFold: il ritaglio pLDDT non si applica.";
  return { models, refinement, quality, ligands, plddt };
}

export function optionsIssues(o: WizardOptions): string[] {
  const out: string[] = [];
  if (!Number.isInteger(o.models) || o.models < 1 || o.models > 100) out.push("Il numero di modelli deve essere un intero fra 1 e 100.");
  if (!Number.isFinite(o.plddtMin) || o.plddtMin < 0 || o.plddtMin > 100) out.push("La soglia pLDDT deve essere fra 0 e 100.");
  return out;
}

// ---------------------------------------------------------------------------------- passaggi di stato
/** Nuovo bersaglio: ricerca, template, allineamento e modello non valgono più se la sequenza cambia. */
export function withTarget(state: WizardState, target: { name: string; sequence: string }, accession?: string): WizardState {
  const changed = cleanSequence(target.sequence) !== cleanSequence(state.target.sequence);
  const next = { ...state, target, accession: accession ?? state.accession };
  return changed ? { ...next, searchJob: null, templates: [], templatesFor: null, alignJob: null, pirDoc: null, modelJob: null } : next;
}

/**
 * Accessione dopo un cambio di sequenza incollata o da file: dall'intestazione FASTA di UniProt (sp|ACC|…); altrimenti
 * resta solo se la sequenza è la stessa (come followAccession della pagina Ricerca).
 */
export function accessionFor(state: WizardState, next: { name: string; sequence: string }): string {
  const fromName = accessionFromName(next.name);
  if (fromName) return fromName;
  return cleanSequence(next.sequence) === cleanSequence(state.target.sequence) ? state.accession : "";
}

export function withAccession(state: WizardState, value: string): WizardState {
  return { ...state, accession: value.trim() ? normalizeAccession(value) : "" };
}

export const accessionValid = (value: string) => !value.trim() || isUniProtAccession(value);

export function withSearchJob(state: WizardState, id: string | null): WizardState {
  if (id === state.searchJob) return state;
  return { ...state, searchJob: id, templates: [], templatesFor: null, alignJob: null, pirDoc: null, modelJob: null };
}

const sameTemplates = (a: TemplateRef[], b: TemplateRef[]) => JSON.stringify(a.map((t) => [t.id, t.chains])) === JSON.stringify(b.map((t) => [t.id, t.chains]));

/** Template cambiati: l'allineamento (e il modello) vanno rifatti. */
export function withTemplates(state: WizardState, templates: TemplateRef[]): WizardState {
  if (sameTemplates(templates, state.templates)) return state;
  return { ...state, templates, templatesFor: state.templatesFor ?? state.searchJob, alignJob: null, pirDoc: null, modelJob: null };
}

export function withAlignJob(state: WizardState, id: string | null): WizardState {
  if (id === state.alignJob) return state;
  return { ...state, alignJob: id, pirDoc: null, modelJob: null };
}

export function withPirDoc(state: WizardState, id: string | null): WizardState {
  if (id === state.pirDoc) return state;
  return { ...state, pirDoc: id, modelJob: null };
}

/** Opzioni cambiate dall'utente: il modello già costruito resta nei job, ma i risultati mostrati saranno quelli nuovi. */
export function withOptions(state: WizardState, patch: Partial<WizardOptions>, byUser = true): WizardState {
  const options = { ...state.options, ...patch };
  if (JSON.stringify(options) === JSON.stringify(state.options)) return state;
  return { ...state, options, optionsTouched: state.optionsTouched || byUser, modelJob: byUser ? null : state.modelJob };
}

export function withModelJob(state: WizardState, id: string | null): WizardState {
  return id === state.modelJob ? state : { ...state, modelJob: id };
}

// ---------------------------------------------------------------------------------- passi raggiungibili
export interface JobsDone {
  /** allineamento del passo 3 completato */
  align: boolean;
  /** modellazione completata */
  model: boolean;
}

/** Il passo più avanzato che si può aprire con lo stato attuale. */
export function reachable(state: WizardState, done: JobsDone): WizardStep {
  if (sequenceIssues(state.target).length) return 1;
  if (templatesIssues(state.templates).length) return 2;
  if (!(done.align || state.pirDoc)) return 3;
  if (optionsIssues(state.options).length || !state.modelJob) return 4;
  if (!done.model) return 5;
  return 6;
}

export const clampStep = (step: number, max: WizardStep): WizardStep => Math.max(1, Math.min(max, Math.round(step) || 1)) as WizardStep;

// ---------------------------------------------------------------------------------- URL e bozza
/** Parametri dell'URL: passo e job (link stabile: un altro browser riprende dai job). */
export function encodeWizardParams(state: WizardState): URLSearchParams {
  const p = new URLSearchParams();
  p.set("step", String(state.step));
  if (state.searchJob) p.set("search", state.searchJob);
  if (state.alignJob) p.set("align", state.alignJob);
  if (state.pirDoc) p.set("doc", state.pirDoc);
  if (state.modelJob) p.set("model", state.modelJob);
  return p;
}

const ID = /^[A-Za-z0-9_-]{1,64}$/;
const idParam = (p: URLSearchParams, key: string): string | null | undefined => {
  const v = p.get(key);
  if (v === null) return undefined;
  return ID.test(v) ? v : null;
};

export function decodeWizardParams(p: URLSearchParams): { step?: WizardStep; searchJob?: string | null; alignJob?: string | null; pirDoc?: string | null; modelJob?: string | null } {
  const raw = Number(p.get("step"));
  return {
    step: p.has("step") && Number.isFinite(raw) ? clampStep(raw, 6) : undefined,
    searchJob: idParam(p, "search"),
    alignJob: idParam(p, "align"),
    pirDoc: idParam(p, "doc"),
    modelJob: idParam(p, "model"),
  };
}

/** Applica l'URL (link, indietro/avanti del browser): passo e job presenti nell'URL; un job diverso da quello dello
 *  stato invalida i passi successivi, uno assente dall'URL resta. */
export function applyUrl(state: WizardState, p: URLSearchParams): WizardState {
  const url = decodeWizardParams(p);
  let next = state;
  if (url.searchJob !== undefined && url.searchJob !== next.searchJob) next = withSearchJob(next, url.searchJob);
  if (url.alignJob !== undefined && url.alignJob !== next.alignJob) next = withAlignJob(next, url.alignJob);
  if (url.pirDoc !== undefined && url.pirDoc !== next.pirDoc) next = withPirDoc(next, url.pirDoc);
  if (url.modelJob !== undefined && url.modelJob !== next.modelJob) next = withModelJob(next, url.modelJob);
  if (url.step !== undefined && url.step !== next.step) next = { ...next, step: url.step };
  return next;
}

const REFINEMENTS: Refinement[] = ["very_fast", "fast", "normal", "thorough"];
const ENGINES: SearchEngine[] = ["rcsb", "ncbi", "local", "mmseqs", "alphafold"];
const str = (v: unknown, fallback = "") => (typeof v === "string" ? v : fallback);
const id = (v: unknown) => (typeof v === "string" && ID.test(v) ? v : null);

function sanitizeTemplates(v: unknown): TemplateRef[] {
  if (!Array.isArray(v)) return [];
  return v.filter((t) => t && typeof t.id === "string" && Array.isArray(t.chains))
    .map((t) => ({ id: String(t.id).slice(0, 40), chains: (t.chains as unknown[]).filter((c) => typeof c === "string").map(String) }))
    .slice(0, MAX_TEMPLATES);
}

/** Bozza letta dal browser (localStorage): qualunque contenuto non valido torna al predefinito, campo per campo. */
export function sanitizeDraft(draft: unknown): WizardState | null {
  if (!draft || typeof draft !== "object") return null;
  const d = draft as Record<string, unknown>;
  const base = initialState();
  const target = (d.target && typeof d.target === "object" ? d.target : {}) as Record<string, unknown>;
  const o = (d.options && typeof d.options === "object" ? d.options : {}) as Record<string, unknown>;
  const models = Number(o.models);
  const plddtMin = Number(o.plddtMin);
  const sel = o.ligandSel && typeof o.ligandSel === "object" ? Object.fromEntries(Object.entries(o.ligandSel as Record<string, unknown>).filter(([, v]) => typeof v === "boolean")) as Record<string, boolean> : {};
  return {
    step: clampStep(Number(d.step), 6),
    target: { name: str(target.name).slice(0, 200), sequence: str(target.sequence).slice(0, 200_000) },
    accession: isUniProtAccession(str(d.accession)) ? normalizeAccession(str(d.accession)) : "",
    engine: ENGINES.includes(d.engine as SearchEngine) ? (d.engine as SearchEngine) : base.engine,
    searchJob: id(d.searchJob),
    templates: sanitizeTemplates(d.templates),
    templatesFor: id(d.templatesFor),
    alignJob: id(d.alignJob),
    pirDoc: id(d.pirDoc),
    options: {
      models: Number.isInteger(models) && models >= 1 && models <= 100 ? models : base.options.models,
      refinement: REFINEMENTS.includes(o.refinement as Refinement) ? (o.refinement as Refinement) : base.options.refinement,
      quality: o.quality === "all" || o.quality === "none" ? o.quality : "best",
      extended: typeof o.extended === "boolean" ? o.extended : base.options.extended,
      ligands: typeof o.ligands === "boolean" ? o.ligands : base.options.ligands,
      ligandSel: sel,
      plddtTrim: typeof o.plddtTrim === "boolean" ? o.plddtTrim : true,
      plddtMin: Number.isFinite(plddtMin) && plddtMin >= 0 && plddtMin <= 100 ? plddtMin : PLDDT_DEFAULT,
    },
    optionsTouched: d.optionsTouched === true,
    modelJob: id(d.modelJob),
  };
}

/** Stato all'apertura della pagina: la bozza del browser, poi l'URL (passo e job, che prevalgono). */
export function restoreState(p: URLSearchParams, draft: unknown): WizardState {
  return applyUrl(sanitizeDraft(draft) ?? initialState(), p);
}

/**
 * Dati dei job completano lo stato arrivando da un link senza bozza (altro browser): bersaglio, motore e accessione
 * dalla ricerca; template (e bersaglio) dall'allineamento; opzioni dalla modellazione, se l'utente non le ha cambiate.
 */
export function fillFromJobs(state: WizardState, jobs: { search?: Job<SearchResult> | null; align?: Job<Align2dResult> | null; model?: Job<ModelResult> | null }): WizardState {
  let next = state;
  // da un link senza bozza manca il bersaglio: motore e accessione vengono dalla ricerca (con la bozza restano quelli
  // scelti, anche se diversi da quelli della ricerca già fatta)
  const fresh = !cleanSequence(state.target.sequence);
  const sp = (jobs.search?.id === state.searchJob ? jobs.search?.params : null) as Record<string, unknown> | null | undefined;
  if (sp && fresh) {
    const q = (sp.query ?? {}) as { name?: string; sequence?: string };
    next = { ...next, target: { name: q.name ?? "target", sequence: q.sequence ?? "" } };
    if (ENGINES.includes(sp.engine as SearchEngine)) next = { ...next, engine: sp.engine as SearchEngine };
    if (!next.accession && typeof sp.uniprot === "string" && isUniProtAccession(sp.uniprot)) next = { ...next, accession: normalizeAccession(sp.uniprot) };
  }
  const ap = (jobs.align?.id === state.alignJob ? jobs.align?.params : null) as Record<string, unknown> | null | undefined;
  if (ap && !next.templates.length) next = { ...next, templates: sanitizeTemplates(ap.templates), templatesFor: next.templatesFor ?? next.searchJob };
  if (ap && !cleanSequence(next.target.sequence)) {
    const t = (ap.target ?? {}) as { name?: string; sequence?: string };
    next = { ...next, target: { name: t.name ?? "target", sequence: t.sequence ?? "" } };
  }
  const mp = (jobs.model?.id === state.modelJob ? jobs.model?.params : null) as Record<string, unknown> | null | undefined;
  if (mp && !next.optionsTouched) {
    const o = (mp.options ?? {}) as Record<string, unknown>;
    const restored = sanitizeDraft({ options: { models: o.models, refinement: o.refinement, quality: mp.quality, extended: mp.extended_quality, ligands: o.hetatm, plddtTrim: o.plddt_trim, plddtMin: o.plddt_min } });
    if (restored) next = { ...next, options: { ...restored.options, ligandSel: next.options.ligandSel } };
  }
  return next;
}

// ---------------------------------------------------------------------------------- richieste
export function searchRequest(state: WizardState, projectId: string | null) {
  return {
    project_id: projectId ?? undefined,
    query: { name: state.target.name || "target", sequence: cleanSequence(state.target.sequence) },
    engine: state.engine,
    evalue: 1e-3,
    max_hits: 50,
    uniprot: state.engine === "alphafold" ? state.accession : undefined,
  };
}

export function align2dRequest(state: WizardState, projectId: string | null) {
  return { project_id: projectId ?? undefined, target: { name: state.target.name || "target", sequence: cleanSequence(state.target.sequence) }, templates: state.templates.map((t) => ({ id: t.id, chains: t.chains })) };
}

/** Corpo di POST /api/model: allineamento del passo 3 (o il documento PIR), template di quell'allineamento, opzioni.
 *  Il bersaglio c'è anche con l'allineamento (che prevale nella modellazione): dà il nome al job. */
export function modelRequest(state: WizardState, o: { projectId: string | null; alignment: string | null; templates?: TemplateRef[] | null; ligandRefs?: LigandRef[] }) {
  const opt = state.options;
  return {
    project_id: o.projectId ?? undefined,
    target: { name: state.target.name || "target", sequence: cleanSequence(state.target.sequence) },
    alignment: o.alignment,
    templates: (o.templates?.length ? o.templates : state.templates).map((t) => ({ id: t.id, chains: t.chains })),
    options: {
      models: opt.models, refinement: opt.refinement, plddt_trim: opt.plddtTrim, plddt_min: opt.plddtMin, hetatm: opt.ligands,
      ...(opt.ligands ? { ligands: o.ligandRefs ?? [] } : {}),
    },
    quality: opt.quality,
    extended_quality: opt.quality !== "none" && opt.extended,
  };
}

/** Controlli preventivi del passo che sta per avviare un calcolo (ricerca, allineamento, modellazione). */
export function preflightFor(kind: "search" | "align2d" | "model", state: WizardState, params: Record<string, unknown>): PreflightRequest {
  return { kind, params, context: { accession: state.accession || null, search_job: kind === "model" ? state.searchJob : null } };
}

// ---------------------------------------------------------------------------------- risultati
export interface ResultSummary {
  file: string;
  grade: string | null;
  gradeScore: number | null;
  dope: number | null;
  zdope: number | null;
  ga341: number | null;
  consensus: number | null;
  consensusBand: string | null;
  models: number;
  warnings: string[];
}

/** Modello migliore (DOPE minimo, scelto dal backend), voto e consenso del suo rapporto di qualità. */
export function resultSummary(result: ModelResult | null | undefined, report?: Pick<QualityReport, "consensus"> | null): ResultSummary | null {
  const best = result?.models.find((m) => m.best);
  if (!result || !best) return null;
  return {
    file: best.file,
    grade: best.quality?.grade ?? null,
    gradeScore: best.quality?.grade_score ?? null,
    dope: best.dope,
    zdope: best.zdope,
    ga341: best.ga341 ?? null,
    consensus: report?.consensus?.score ?? null,
    consensusBand: report?.consensus?.band ?? null,
    models: result.models.filter((m) => !m.failure).length,
    warnings: result.warnings ?? [],
  };
}
