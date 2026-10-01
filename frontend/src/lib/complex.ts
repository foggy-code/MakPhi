// Complessi e omo-oligomeri (F5.3): righe della modalità Complesso di Modellazione, richiesta al backend, anteprima
// dell'allineamento per catena, colorazione del 3D per catena e interfaccia, testi degli assemblaggi. Logica pura.
//
// Convenzioni del backend (domain/complexes.py): un complesso ha da 2 a 12 catene e al più 3000 residui in tutto; ogni
// catena del template scelta riceve una sequenza del bersaglio, oppure "stessa della catena X" (`same_as`) negli
// omo-oligomeri; le restrizioni di simmetria valgono per le catene con la stessa sequenza. I residui del confronto delle
// interfacce sono etichette "A57" (catena di un carattere + numero, anche con codice di inserzione: "A52A").

import type {
  AssemblyChain, AssemblySummary, ChainSummary, ComplexAssessment, ComplexRequest, InterfaceComparison, InterfaceStatus, TemplateRef,
} from "../api/types";
import type { CategoricalScale } from "./colorscale";
import type { PirEntry } from "./pir";
import { resiNumber, type ResidueColoring, type ResidueValue } from "./residueColoring";

/** Stessi limiti del backend (domain/complexes.MAX_CHAINS, MAX_RESIDUES). */
export const MAX_COMPLEX_CHAINS = 12;
export const MAX_COMPLEX_RESIDUES = 3000;
/** Peso predefinito delle restrizioni di simmetria (esempio del manuale di MODELLER). */
export const SYMMETRY_WEIGHT = 1;

export type ChainSource = "project" | "paste" | "same";

export interface ComplexRow {
  /** catena del template */
  chain: string;
  included: boolean;
  source: ChainSource;
  name: string;
  /** sequenza (fonti "project" e "paste") */
  sequence: string;
  /** id della sequenza del progetto (fonte "project") */
  sequenceId?: string;
  /** catena del template di cui ripetere la sequenza (fonte "same") */
  sameAs: string;
  /** residui della catena del template (informativo) */
  residues?: number;
}

export interface ComplexState {
  templateId: string;
  name: string;
  rows: ComplexRow[];
  symmetry: boolean;
  weight: number;
}

export function emptyComplex(templateId = ""): ComplexState {
  return { templateId, name: "complesso", rows: [], symmetry: true, weight: SYMMETRY_WEIGHT };
}

/** Lettere della sequenza (intestazione FASTA, numeri, spazi e simboli tolti), in maiuscolo. */
export function cleanSequence(text: string): string {
  const body = text.trimStart().startsWith(">") ? text.trim().split(/\r?\n/).slice(1).join("") : text;
  return body.replace(/[^A-Za-z]/g, "").toUpperCase();
}

/**
 * Righe per le catene del template. Le righe già presenti per la stessa catena restano come sono; per le nuove, una
 * catena con la stessa sequenza (nel template) di una precedente è proposta come "stessa della catena …": in un
 * omo-oligomero basta scegliere la sequenza della prima.
 */
export function rowsForChains(chains: Pick<ChainSummary, "chain" | "sequence" | "residues">[], previous: ComplexRow[] = []): ComplexRow[] {
  const byChain = new Map(previous.map((r) => [r.chain, r]));
  const firstWith = new Map<string, string>();
  return chains.map((c) => {
    const kept = byChain.get(c.chain);
    const twin = firstWith.get(c.sequence);
    if (!firstWith.has(c.sequence)) firstWith.set(c.sequence, c.chain);
    if (kept) return { ...kept, residues: c.residues };
    return twin
      ? { chain: c.chain, included: true, source: "same", name: "", sequence: "", sameAs: twin, residues: c.residues }
      : { chain: c.chain, included: true, source: "paste", name: "", sequence: "", sameAs: "", residues: c.residues };
  });
}

/** Sequenza effettiva di una riga ("stessa della catena X": quella della riga X, se ha una sequenza propria). */
export function resolvedSequence(row: ComplexRow, rows: ComplexRow[]): string {
  if (row.source !== "same") return cleanSequence(row.sequence);
  const ref = rows.find((r) => r.chain === row.sameAs);
  return ref && ref.source !== "same" ? cleanSequence(ref.sequence) : "";
}

export interface ComplexCheck {
  request: ComplexRequest | null;
  errors: string[];
  chains: number;
  residues: number;
  /** gruppi di catene con la stessa sequenza (le restrizioni di simmetria valgono per questi) */
  symmetric: string[][];
}

/** Richiesta del complesso per il backend, con gli errori che il backend darebbe (corrispondenza incompleta, limiti). */
export function complexRequest(state: ComplexState): ComplexCheck {
  const rows = state.rows.filter((r) => r.included);
  const errors: string[] = [];
  if (!state.templateId) errors.push("Scegli il template (o l'assemblaggio) del complesso.");
  if (rows.length < 2) errors.push("Scegli almeno due catene del template: per una sola catena usa l'allineamento automatico.");
  if (rows.length > MAX_COMPLEX_CHAINS) errors.push(`Troppe catene (${rows.length}, massimo ${MAX_COMPLEX_CHAINS}).`);
  const included = new Set(rows.map((r) => r.chain));
  let residues = 0;
  const bySequence = new Map<string, string[]>();
  for (const row of rows) {
    if (row.source === "same") {
      const ref = rows.find((r) => r.chain === row.sameAs);
      if (!row.sameAs || !included.has(row.sameAs)) {
        errors.push(`Catena ${row.chain}: la catena di cui ripetere la sequenza non è fra quelle scelte.`);
        continue;
      }
      if (!ref || ref.source === "same") {
        errors.push(`Catena ${row.chain}: la catena ${row.sameAs} non ha una sequenza propria.`);
        continue;
      }
    }
    const seq = resolvedSequence(row, rows);
    if (!seq) {
      if (row.source !== "same") errors.push(`Manca la sequenza del bersaglio per la catena ${row.chain}.`);
      continue;
    }
    residues += seq.length;
    bySequence.set(seq, [...(bySequence.get(seq) ?? []), row.chain]);
  }
  if (residues > MAX_COMPLEX_RESIDUES) {
    errors.push(`Il complesso ha ${residues} residui in tutto (massimo ${MAX_COMPLEX_RESIDUES}: il tempo di MODELLER cresce con i residui del modello).`);
  }
  const symmetric = [...bySequence.values()].filter((g) => g.length > 1);
  const request: ComplexRequest | null = errors.length ? null : {
    name: state.name.trim() || "complesso",
    chains: rows.map((r) => r.source === "same"
      ? { template_chain: r.chain, same_as: r.sameAs, ...(r.name.trim() ? { name: r.name.trim() } : {}) }
      : { template_chain: r.chain, sequence: cleanSequence(r.sequence), ...(r.name.trim() ? { name: r.name.trim() } : {}) }),
  };
  return { request, errors, chains: rows.length, residues, symmetric };
}

/** Template del complesso come riferimento per le API (catene scelte, nell'ordine). */
export function complexTemplateRefs(state: ComplexState): TemplateRef[] {
  const chains = state.rows.filter((r) => r.included).map((r) => r.chain);
  return state.templateId ? [{ id: state.templateId, chains }] : [];
}

/** Opzioni di simmetria da inviare: solo se richiesta e con almeno due catene uguali. */
export function symmetryOptions(state: ComplexState, check: ComplexCheck): { symmetry: boolean; symmetry_weight?: number } {
  const on = state.symmetry && check.symmetric.length > 0;
  return on ? { symmetry: true, symmetry_weight: state.weight > 0 ? state.weight : SYMMETRY_WEIGHT } : { symmetry: false };
}

// ---------------------------------------------------------------------------------- anteprima per catena
export interface ChainAlignment {
  label: string;
  ids: string[];
  aligned: string[];
  /** riga del bersaglio (modalità Confidenza) */
  reference: number;
}

/** Allineamento di ogni catena (i tratti fra i '/'), con il bersaglio come riferimento; null se le voci non hanno lo
 *  stesso numero di catene (PIR non coerente). */
export function splitPirChains(entries: PirEntry[], labels: string[] = []): ChainAlignment[] | null {
  const target = entries.findIndex((e) => e.kind === "sequence");
  if (target < 0 || entries.length < 2) return null;
  const pieces = entries.map((e) => e.sequence.split("/"));
  const n = pieces[target].length;
  if (pieces.some((p) => p.length !== n)) return null;
  return Array.from({ length: n }, (_, k) => ({
    label: labels[k] ?? `Catena ${k + 1}`,
    ids: entries.map((e) => e.code),
    aligned: pieces.map((p) => p[k]),
    reference: target,
  }));
}

// ---------------------------------------------------------------------------------- 3D e confronto
/** Colori delle catene (chiari, per non confondersi con i colori dell'interfaccia). */
export const CHAIN_COLORS = ["#93c5fd", "#c4b5fd", "#99f6e4", "#fbcfe8", "#fde68a", "#bef264", "#fdba74", "#a5b4fc", "#d9f99d", "#f5d0fe", "#bae6fd", "#e7e5e4"];

export const INTERFACE_CATEGORIES: CategoricalScale = {
  kind: "categorical",
  categories: [
    { key: "conserved", color: "#15803d", label: "Interfaccia conservata dal template" },
    { key: "new", color: "#f59e0b", label: "Interfaccia solo nel modello" },
    { key: "lost", color: "#dc2626", label: "Interfaccia del template persa" },
  ],
};

export const STATUS_TONE: Record<InterfaceStatus, "ok" | "warn" | "err"> = {
  alta: "ok", media: "ok", accettabile: "warn", "non riprodotta": "err", nuova: "warn", "non valutabile": "warn",
};

/** "A57" → catena A, residuo 57 (anche "A52A": residuo 52A). */
export function parseResidueLabel(label: string): { chain: string; resnum: string } {
  return { chain: label.slice(0, 1), resnum: label.slice(1) };
}

function pairKey(chains: (string | null | undefined)[]): string {
  return chains.filter(Boolean).slice().sort().join("|");
}

/** Colori delle catene del modello, nell'ordine. */
export function chainColors(chains: string[]): Record<string, string> {
  return Object.fromEntries(chains.map((c, i) => [c, CHAIN_COLORS[i % CHAIN_COLORS.length]]));
}

/**
 * Colorazione del modello: catene in colori chiari e, sopra, i residui d'interfaccia (ΔSASA > 1 Å²) delle coppie di
 * catene — conservati dal template, solo nel modello — e quelli del template persi nel modello. `pair` = indice nel
 * confronto (una sola interfaccia), null = tutte.
 */
export function complexColoring(assessment: ComplexAssessment | null | undefined, pair: number | null = null): ResidueColoring | null {
  if (!assessment) return null;
  const chains = assessment.interfaces.chains ?? [];
  const colors = chainColors(chains);
  const comparisons = pair === null ? assessment.comparison : assessment.comparison.filter((_, i) => i === pair);
  const wanted = new Set(comparisons.map((c) => pairKey(c.model_chains)));
  const conserved = new Set(comparisons.flatMap((c) => c.conserved_residues));
  const values = new Map<string, ResidueValue>();
  for (const p of assessment.interfaces.pairs) {
    if (pair !== null && !wanted.has(pairKey(p.chains))) continue;
    for (const c of p.chains) {
      for (const r of p.residues[c] ?? []) {
        const label = `${c}${r.resnum}`;
        const kind = conserved.has(label) ? "conserved" : "new";
        const prev = values.get(label);
        if (!prev || (prev.value === "new" && kind === "conserved")) {
          values.set(label, { chain: c, resnum: r.resnum, value: kind, note: `${r.resname} · ΔSASA ${r.delta.toFixed(1).replace(".", ",")} Å²` });
        }
      }
    }
  }
  for (const c of comparisons) {
    for (const label of c.lost_residues) {
      if (!values.has(label)) values.set(label, { ...parseResidueLabel(label), value: "lost", note: "d'interfaccia nel template, non nel modello" });
    }
  }
  const labels = Object.fromEntries(INTERFACE_CATEGORIES.categories.map((k) => [k.key, k.label]));
  return {
    key: "interface",
    title: pair === null ? "Catene e interfacce del complesso" : `Interfaccia ${comparisons[0]?.model_chains.join("–") ?? ""}`,
    values: [...values.values()],
    scale: INTERFACE_CATEGORIES,
    describe: (v) => (typeof v === "string" ? labels[v] ?? v : "fuori dall'interfaccia"),
    chainColors: colors,
    legendExtra: chains.map((c) => ({ color: colors[c], label: `catena ${c}` })),
  };
}

/** Residui d'interfaccia del modello di un confronto, da evidenziare e centrare nel 3D. */
export function comparisonHighlight(assessment: ComplexAssessment | null | undefined, comparison: InterfaceComparison | null | undefined): { chain: string; resi: number }[] {
  if (!assessment || !comparison) return [];
  const key = pairKey(comparison.model_chains);
  const pair = assessment.interfaces.pairs.find((p) => pairKey(p.chains) === key);
  const labels = pair ? pair.chains.flatMap((c) => (pair.residues[c] ?? []).map((r) => ({ chain: c, resi: resiNumber(r.resnum) }))) : [];
  const lost = comparison.lost_residues.map(parseResidueLabel).map((r) => ({ chain: r.chain, resi: resiNumber(r.resnum) }));
  return [...labels, ...lost].filter((h) => Number.isFinite(h.resi));
}

const OLIGOMER: Record<string, string> = {
  monomeric: "monomero", dimeric: "dimero", trimeric: "trimero", tetrameric: "tetramero", pentameric: "pentamero",
  hexameric: "esamero", heptameric: "eptamero", octameric: "ottamero", nonameric: "nonamero", decameric: "decamero",
  dodecameric: "dodecamero",
};

/** Stato oligomerico in italiano ("dimeric" → "dimero"); altrimenti dal numero di catene proteiche. */
export function oligomerLabel(details: string, proteinChains: number): string {
  const key = details.trim().toLowerCase();
  if (OLIGOMER[key]) return OLIGOMER[key];
  if (key) return key;
  return proteinChains === 1 ? "monomero" : `${proteinChains} catene proteiche`;
}

/** Catena di un assemblaggio: "B = A con l'operatore 2" (`short`: "B=A·2"); solo il nome se è la catena del file così
 *  com'è (stesso nome, operatore identità). */
export function assemblyChainLabel(c: AssemblyChain, short = false): string {
  if (c.chain === c.source && c.identity) return c.chain;
  return short ? `${c.chain}=${c.source}·${c.operator}` : `${c.chain} = ${c.source} con l'operatore ${c.operator}`;
}

/** Riga di un assemblaggio: "Assemblaggio 1 · dimero · catene A, B (B = A con l'operatore 2) · dall'autore". */
export function assemblyLabel(a: AssemblySummary): string {
  const copies = a.chains.filter((c) => c.chain !== c.source || !c.identity).map((c) => assemblyChainLabel(c));
  const origin = a.details === "author_defined_assembly" ? "dall'autore" : a.details === "software_defined_assembly" ? `dal programma${a.method ? ` (${a.method})` : ""}` : "";
  return [
    `Assemblaggio ${a.id}`,
    oligomerLabel(a.oligomeric_details, a.protein_chains),
    a.chains.length ? `catene ${a.chains.map((c) => c.chain).join(", ")}${copies.length ? ` (${copies.join("; ")})` : ""}` : "",
    origin,
  ].filter(Boolean).join(" · ");
}

/** Un assemblaggio vale la pena di essere importato: si costruisce, ha almeno due catene e non è la sola identità
 *  (con la sola identità le catene sono già quelle del file). */
export function assemblyImportable(a: AssemblySummary): boolean {
  return a.buildable && a.protein_chains >= 2 && !a.identity_only;
}
