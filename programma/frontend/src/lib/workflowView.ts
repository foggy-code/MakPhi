// Presentazione delle pagine del flusso di lavoro (Sequenze, Libreria template, Ricerca template, procedura guidata):
// sequenza numerata, composizione per classe e per residuo, struttura secondaria, residui mancanti a intervalli,
// risoluzione, filtro della libreria, tracce di copertura degli hit e riepilogo dei passi. Logica pura, testata in
// workflowView.test.ts.
import type { RankReason, SearchEngine, SearchHit, TemplateItem, TemplateRef } from "../api/types";
import type { StackPart, Track } from "../components/charts";
import { fmtEvalue, fmtNum } from "./format";
import { SS3_LABEL } from "./residues";
import { rankTone, sortedReasons } from "./templateRank";
import { cleanSequence, type Refinement, type WizardOptions, type WizardState, type WizardStep } from "./wizard";

// ---------------------------------------------------------------------------------- sequenza
/** Sequenza a righe di `width` residui, in blocchi di `block`, con la posizione del primo residuo di ogni riga. */
export function numberedSequence(seq: string, width = 60, block = 10): string {
  const pad = String(Math.max(1, seq.length)).length;
  const lines: string[] = [];
  for (let i = 0; i < seq.length; i += width) {
    const row = seq.slice(i, i + width);
    const blocks: string[] = [];
    for (let j = 0; j < row.length; j += block) blocks.push(row.slice(j, j + block));
    lines.push(`${String(i + 1).padStart(pad, " ")}  ${blocks.join(" ")}`);
  }
  return lines.join("\n");
}

/**
 * Classi di residui della barra di composizione (codici a una lettera, colore della barra): 5 gruppi larghi (His tra i
 * carichi positivi). Colori dai token --aa-* (styles/sequences.css): nel tema scuro sono i colori Clustal X del residuo
 * rappresentativo (A, K, D, N, G), gli stessi dell'allineamento (lib/aminoAcids); nel tema chiaro le stesse tinte più
 * scure, per restare ≥ 3:1 sui pannelli (sequenceTokens.test.ts).
 */
export const AA_CLASSES: { label: string; letters: string; color: string; clustal: string }[] = [
  { label: "Idrofobici", letters: "AILMFWV", color: "var(--aa-hydrophobic)", clustal: "A" },
  { label: "Carichi +", letters: "KRH", color: "var(--aa-positive)", clustal: "K" },
  { label: "Carichi −", letters: "DE", color: "var(--aa-negative)", clustal: "D" },
  { label: "Polari", letters: "NQSTY", color: "var(--aa-polar)", clustal: "N" },
  { label: "G, P, C", letters: "GPC", color: "var(--aa-special)", clustal: "G" },
];

export const AA_NAME: Record<string, string> = {
  A: "Alanina", R: "Arginina", N: "Asparagina", D: "Aspartato", C: "Cisteina", Q: "Glutammina", E: "Glutammato",
  G: "Glicina", H: "Istidina", I: "Isoleucina", L: "Leucina", K: "Lisina", M: "Metionina", F: "Fenilalanina",
  P: "Prolina", S: "Serina", T: "Treonina", W: "Triptofano", Y: "Tirosina", V: "Valina",
};

/** Percentuale di ogni classe sulla lunghezza della sequenza (i residui non standard non entrano in alcuna classe). */
export function compositionParts(comp: Record<string, number>, length: number): StackPart[] {
  const n = Math.max(1, length);
  return AA_CLASSES.map((c) => ({ label: c.label, color: c.color, value: ([...c.letters].reduce((s, l) => s + (comp[l] ?? 0), 0) / n) * 100 }));
}

export interface CompositionRow {
  letter: string;
  name: string;
  group: string;
  count: number;
  pct: number;
}

/** Una riga per amminoacido standard (anche quelli assenti), per conteggio decrescente. */
export function compositionRows(comp: Record<string, number>, length: number): CompositionRow[] {
  const n = Math.max(1, length);
  return Object.keys(AA_NAME)
    .map((l) => ({
      letter: l,
      name: AA_NAME[l],
      group: AA_CLASSES.find((c) => c.letters.includes(l))?.label ?? "—",
      count: comp[l] ?? 0,
      pct: ((comp[l] ?? 0) / n) * 100,
    }))
    .sort((a, b) => b.count - a.count || a.letter.localeCompare(b.letter));
}

/** Nome di uno stato della struttura secondaria a 3 stati con l'iniziale maiuscola ("Filamento β"), da SS3_LABEL. */
export const ssLabel = (state: "H" | "E" | "C") => SS3_LABEL[state].charAt(0).toUpperCase() + SS3_LABEL[state].slice(1);

/** Elica (H), filamento β (E) e coil (il resto) in percentuale, da una stringa a tre stati (DSSP o predizione). */
export function ssParts(ss: string): StackPart[] {
  let h = 0;
  let e = 0;
  for (const c of ss) {
    if (c === "H") h++;
    else if (c === "E") e++;
  }
  const n = ss.length || 1;
  return [
    { label: ssLabel("H"), value: (h / n) * 100, color: "var(--helix)" },
    { label: ssLabel("E"), value: (e / n) * 100, color: "var(--strand)" },
    { label: ssLabel("C"), value: ss.length ? ((ss.length - h - e) / n) * 100 : 0, color: "var(--coil)" },
  ];
}

const UNIPROT_COMMENT: Record<string, string> = {
  FUNCTION: "Funzione", "CATALYTIC ACTIVITY": "Attività catalitica", COFACTOR: "Cofattore", "ACTIVITY REGULATION": "Regolazione",
  PATHWAY: "Via metabolica", SUBUNIT: "Subunità", "SUBCELLULAR LOCATION": "Localizzazione", DOMAIN: "Domini", PTM: "Modifiche post-traduzionali",
  DISEASE: "Malattie", SIMILARITY: "Somiglianza", "TISSUE SPECIFICITY": "Specificità tissutale", INDUCTION: "Induzione",
  "DEVELOPMENTAL STAGE": "Stadio di sviluppo", MISCELLANEOUS: "Varie", CAUTION: "Avvertenze", "BIOPHYSICOCHEMICAL PROPERTIES": "Proprietà biofisiche",
  POLYMORPHISM: "Polimorfismi", "DISRUPTION PHENOTYPE": "Fenotipo del knockout", "ALTERNATIVE PRODUCTS": "Isoforme",
};

/** Titolo italiano di un commento di UniProtKB ("SUBCELLULAR LOCATION" → "Localizzazione"); gli altri in minuscolo. */
export function uniprotCommentLabel(type: string): string {
  const known = UNIPROT_COMMENT[type];
  if (known) return known;
  const text = type.toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// ---------------------------------------------------------------------------------- template
/** Residui per catena a intervalli: "A 1–5, 9, 52A; B 12–14" (numeri consecutivi uniti, codici di inserzione a parte). */
export function residueRanges(list: { chain: string; resnum: string }[]): string {
  const byChain = new Map<string, string[]>();
  for (const r of list) {
    const nums = byChain.get(r.chain) ?? [];
    nums.push(r.resnum.trim());
    byChain.set(r.chain, nums);
  }
  const parts: string[] = [];
  for (const [chain, nums] of byChain) {
    const out: string[] = [];
    let start: number | null = null;
    let prev: number | null = null;
    const flush = () => {
      if (start !== null && prev !== null) out.push(start === prev ? `${start}` : `${start}–${prev}`);
      start = prev = null;
    };
    for (const s of nums) {
      if (!/^-?\d+$/.test(s)) {
        flush();
        out.push(s);
        continue;
      }
      const v = Number(s);
      if (prev !== null && v === prev + 1) prev = v;
      else {
        flush();
        start = prev = v;
      }
    }
    flush();
    parts.push(`${chain || "—"} ${out.join(", ")}`);
  }
  return parts.join("; ");
}

/** Fascia della risoluzione come nel punteggio dei template (domain/template_rank.py, Wlodawer et al. 2008). */
export function resolutionTone(res: number | null | undefined): "ok" | "warn" | "err" | undefined {
  if (res === null || res === undefined || !Number.isFinite(res) || res <= 0) return undefined;
  return res <= 2.5 ? "ok" : res <= 3.5 ? "warn" : "err";
}

export function resolutionNote(res: number | null | undefined): string {
  const tone = resolutionTone(res);
  if (!tone) return "Risoluzione non disponibile (NMR, modello predetto o file senza intestazione).";
  return {
    ok: "Fino a 2,5 Å: catene laterali ben definite.",
    warn: "Tra 2,5 e 3,5 Å: scheletro tracciabile, catene laterali incerte.",
    err: "Oltre 3,5 Å: affidabile solo lo scheletro.",
  }[tone];
}

/** Template della libreria che contengono il testo (codice, titolo, origine, metodo, catene, AlphaFold, etichette). */
export function templateMatches(t: TemplateItem, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [t.id, t.title, t.source, t.method, t.chains?.join(" "), t.alphafold?.accession, t.alphafold?.gene, t.alphafold?.organism, ...(t.tags ?? [])]
    .some((v) => !!v && v.toLowerCase().includes(q));
}

export const templateLabel = (t: TemplateRef) => (t.chains.length ? `${t.id}:${t.chains.join("")}` : t.id);

// ---------------------------------------------------------------------------------- ricerca
const TONE_COLOR = { ok: "var(--ok)", warn: "var(--warn)", err: "var(--err)" } as const;

export const toneColor = (tone: "ok" | "warn" | "err" | undefined) => (tone ? TONE_COLOR[tone] : "var(--accent)");

/**
 * Componenti del punteggio con i punti di ciascuna: il punteggio è la media pesata delle componenti conteggiate
 * (domain/template_rank.score_hit), quindi i punti sommano al punteggio. Informative e non disponibili: null.
 */
export function rankContributions(reasons: RankReason[] | undefined): (RankReason & { points: number | null })[] {
  const weights = (reasons ?? []).reduce((s, r) => s + (r.score !== null && r.weight > 0 ? r.weight : 0), 0);
  return sortedReasons(reasons).map((r) => ({ ...r, points: r.score !== null && r.weight > 0 && weights ? (r.score * r.weight) / weights : null }));
}

/** Tracce della mappa di copertura: un hit per traccia (i primi `limit`), colorato per fascia del punteggio. */
export function hitTracks(hits: SearchHit[], limit = 20): Track[] {
  return hits.slice(0, limit).map((h) => {
    const tone = rankTone(h.rank_score);
    const score = h.rank_score !== undefined ? ` · punteggio ${fmtNum(h.rank_score, 1)}` : "";
    return {
      label: h.template_id,
      color: toneColor(tone),
      items: [{
        start: h.query_start,
        end: h.query_end,
        title: `${h.template_id} · identità ${fmtNum(h.identity, 1)}% · copertura ${fmtNum(h.coverage, 1)}% · E-value ${fmtEvalue(h.evalue)}${score}`,
      }],
    };
  });
}

// ---------------------------------------------------------------------------------- procedura guidata
export const ENGINE_LABEL: Record<SearchEngine, string> = {
  rcsb: "RCSB PDB", ncbi: "NCBI BLAST", local: "BLAST+ locale", mmseqs: "MMseqs2 locale", alphafold: "AlphaFold DB",
};

/**
 * Avviso di privacy sotto la scelta del motore: RCSB (il predefinito) e NCBI ricevono la sequenza. AlphaFold DB ha già
 * il suo avviso (accessione a EBI, sequenze senza accessione a NCBI); i motori locali non inviano nulla.
 */
export function remoteSearchNotice(engine: SearchEngine): string | null {
  const service = engine === "rcsb" ? "RCSB PDB" : engine === "ncbi" ? "NCBI" : null;
  if (!service) return null;
  return `La sequenza viene inviata a ${service}, un servizio esterno. Per sequenze riservate usa BLAST+ o MMseqs2 locale (Impostazioni → Database), che non escono dal computer.`;
}

export const REFINEMENT_LABEL: Record<Refinement, string> = { very_fast: "Rapidissimo", fast: "Rapido", normal: "Standard", thorough: "Accurato" };

const QUALITY_TEXT: Record<WizardOptions["quality"], string> = { best: "qualità sul migliore", all: "qualità su tutti", none: "senza analisi di qualità" };

/** Opzioni in una riga: "5 modelli · Rapido · qualità sul migliore con PROCHECK e ProSA · ligandi". */
export function optionsSummary(o: WizardOptions, predicted = false): string {
  const parts = [`${o.models} ${o.models === 1 ? "modello" : "modelli"}`, REFINEMENT_LABEL[o.refinement]];
  parts.push(`${QUALITY_TEXT[o.quality]}${o.quality !== "none" && o.extended ? " con PROCHECK e ProSA" : ""}`);
  if (o.ligands) parts.push("ligandi trasferiti");
  if (predicted && o.plddtTrim) parts.push(`ritaglio pLDDT < ${o.plddtMin}`);
  return parts.join(" · ");
}

/** Riepilogo breve di un passo (indicatori della procedura); null finché il passo non ha una scelta. */
export function stepSummary(state: WizardState, step: WizardStep): string | null {
  switch (step) {
    case 1: {
      const n = cleanSequence(state.target.sequence).length;
      return n ? `${state.target.name || "bersaglio"} · ${n} aa` : null;
    }
    case 2:
      return state.templates.length ? state.templates.map(templateLabel).join(", ") : null;
    case 3:
      return state.pirDoc ? "documento PIR del progetto" : state.alignJob ? "MODELLER align2d" : null;
    case 4:
      return state.alignJob || state.pirDoc ? `${state.options.models} ${state.options.models === 1 ? "modello" : "modelli"} · ${REFINEMENT_LABEL[state.options.refinement]}` : null;
    case 5:
      return state.modelJob ? "modellazione avviata" : null;
    default:
      return null;
  }
}
