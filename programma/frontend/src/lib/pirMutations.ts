// Sostituzioni di amminoacidi nell'Editor PIR: punteggio BLOSUM62, sostituzione di un residuo, numerazione per catena,
// elenco delle mutazioni rispetto alle sequenze originali (notazione K48R, B:K2R con più catene) e loro annullamento.
// Logica pura e testata (pirMutations.test.ts).
//
// Le sequenze originali sono quelle senza gap al caricamento del documento: lo spostamento dei gap non le cambia,
// quindi una mutazione è una posizione in cui il residuo n-esimo differisce dall'originale. Se la lunghezza cambia
// (residui aggiunti o tolti dal testo PIR) il confronto per posizione non ha più senso e la voce lo segnala. Le voci con
// un codice ripetuto non si confrontano: non si saprebbe a quale originale riferirle.

import type { PirEntry } from "./pir";

/** Ordine delle righe e colonne della matrice (come in NCBI / Biopython). */
const ORDER = "ARNDCQEGHILKMFPSTWYV";

// BLOSUM62 (Henikoff & Henikoff 1992), generata da Biopython (Bio.Align.substitution_matrices "BLOSUM62").
const MATRIX: number[][] = [
  [4, -1, -2, -2, 0, -1, -1, 0, -2, -1, -1, -1, -1, -2, -1, 1, 0, -3, -2, 0],
  [-1, 5, 0, -2, -3, 1, 0, -2, 0, -3, -2, 2, -1, -3, -2, -1, -1, -3, -2, -3],
  [-2, 0, 6, 1, -3, 0, 0, 0, 1, -3, -3, 0, -2, -3, -2, 1, 0, -4, -2, -3],
  [-2, -2, 1, 6, -3, 0, 2, -1, -1, -3, -4, -1, -3, -3, -1, 0, -1, -4, -3, -3],
  [0, -3, -3, -3, 9, -3, -4, -3, -3, -1, -1, -3, -1, -2, -3, -1, -1, -2, -2, -1],
  [-1, 1, 0, 0, -3, 5, 2, -2, 0, -3, -2, 1, 0, -3, -1, 0, -1, -2, -1, -2],
  [-1, 0, 0, 2, -4, 2, 5, -2, 0, -3, -3, 1, -2, -3, -1, 0, -1, -3, -2, -2],
  [0, -2, 0, -1, -3, -2, -2, 6, -2, -4, -4, -2, -3, -3, -2, 0, -2, -2, -3, -3],
  [-2, 0, 1, -1, -3, 0, 0, -2, 8, -3, -3, -1, -2, -1, -2, -1, -2, -2, 2, -3],
  [-1, -3, -3, -3, -1, -3, -3, -4, -3, 4, 2, -3, 1, 0, -3, -2, -1, -3, -1, 3],
  [-1, -2, -3, -4, -1, -2, -3, -4, -3, 2, 4, -2, 2, 0, -3, -2, -1, -2, -1, 1],
  [-1, 2, 0, -1, -3, 1, 1, -2, -1, -3, -2, 5, -1, -3, -1, 0, -1, -3, -2, -2],
  [-1, -1, -2, -3, -1, 0, -2, -3, -2, 1, 2, -1, 5, 0, -2, -1, -1, -1, -1, 1],
  [-2, -3, -3, -3, -2, -3, -3, -3, -1, 0, 0, -3, 0, 6, -4, -2, -2, 1, 3, -1],
  [-1, -2, -2, -1, -3, -1, -1, -2, -2, -3, -3, -1, -2, -4, 7, -1, -1, -4, -3, -2],
  [1, -1, 1, 0, -1, 0, 0, 0, -1, -2, -2, 0, -1, -2, -1, 4, 1, -3, -2, -2],
  [0, -1, 0, -1, -1, -1, -1, -2, -2, -1, -1, -1, -1, -2, -1, 1, 5, -2, -2, 0],
  [-3, -3, -4, -4, -2, -2, -3, -2, -2, -3, -2, -3, -1, 1, -4, -3, -2, 11, 2, -3],
  [-2, -2, -2, -3, -2, -1, -2, -3, 2, -1, -1, -2, -1, 3, -3, -2, -2, 2, 7, -1],
  [0, -3, -3, -3, -1, -2, -2, -3, -3, 3, 1, -2, 1, -1, -2, -2, 0, -3, -1, 4],
];

/** I 20 amminoacidi standard: le sole lettere accettate come sostituzione. */
export const STANDARD_AA = ORDER;

export const isStandardAa = (c: string) => c.length === 1 && ORDER.includes(c.toUpperCase());

/** Punteggio BLOSUM62 della sostituzione a → b; null se una delle due lettere non è un amminoacido standard. */
export function blosum62(a: string, b: string): number | null {
  const i = ORDER.indexOf(a.toUpperCase());
  const j = ORDER.indexOf(b.toUpperCase());
  return i < 0 || j < 0 ? null : MATRIX[i][j];
}

/** Lettura del punteggio: positivo = sostituzione frequente fra proteine omologhe (conservativa). */
export function blosumTone(score: number | null): "same" | "conservative" | "neutral" | "radical" {
  if (score === null) return "neutral";
  if (score > 0) return "conservative";
  if (score === 0) return "neutral";
  return "radical";
}

const isResidue = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

/** Sequenza senza gap né marcatori (solo i residui, in maiuscolo). */
export const residuesOf = (seq: string) => seq.replace(/[^A-Za-z]/g, "").toUpperCase();

/** Colonna dell'n-esimo residuo (da 1) di una riga allineata; null se la riga ha meno residui. */
export function columnOfResidue(seq: string, n: number): number | null {
  let k = 0;
  for (let c = 0; c < seq.length; c++) {
    if (isResidue(seq[c]) && ++k === n) return c;
  }
  return null;
}

/**
 * Sostituisce il residuo nella colonna `col` della riga `row` con `aa`. Null se lì c'è un gap o un marcatore, se la
 * lettera non è un amminoacido standard o se il residuo è già quello.
 */
export function substitute(rows: string[], row: number, col: number, aa: string): string[] | null {
  const s = rows[row];
  const up = aa.toUpperCase();
  if (s === undefined || !isResidue(s[col]) || !isStandardAa(up) || s[col].toUpperCase() === up) return null;
  const out = rows.slice();
  out[row] = s.slice(0, col) + up + s.slice(col + 1);
  return out;
}

/**
 * Primo residuo numerato della voce: il terzo campo della seconda riga PIR (es. "structureX:1ABC:5:A:..." → 5). Vuoto,
 * "FIRST" o non numerico → 1. Per i template è il numero del primo residuo nel file della struttura: se la numerazione
 * del file salta (residui mancanti) i numeri successivi sono solo progressivi.
 */
export function startNumber(header: string): number {
  const n = parseInt((header.split(":")[2] ?? "").trim(), 10);
  return Number.isFinite(n) ? n : 1;
}

const CHAIN_ID = /^[A-Za-z0-9]$/;

/**
 * Nomi delle `segments` catene di una voce (separate da "/"): la prima dal quarto campo della seconda riga PIR (catena
 * iniziale), l'ultima dal sesto (catena finale) se le catene sono due; le altre seguono in ordine alfabetico dalla prima
 * (A, B, C…). Senza indicazioni la prima è A, come nei modelli a più catene di BioLoom.
 */
export function chainIds(header: string, segments: number): string[] {
  const f = header.split(":");
  const first = (f[3] ?? "").trim();
  const last = (f[5] ?? "").trim();
  const start = CHAIN_ID.test(first) ? first : "A";
  const out = Array.from({ length: segments }, (_, i) => (i === 0 ? start : String.fromCharCode(start.charCodeAt(0) + i)));
  if (segments === 2 && CHAIN_ID.test(last) && last !== start) out[1] = last;
  return out;
}

export interface ResiduePosition {
  /** posizione nella sequenza senza gap della voce (da 1, tutte le catene) */
  index: number;
  /** catena (da 0) e suo nome */
  segment: number;
  chain: string;
  /** numero del residuo nella catena: la prima parte dal primo residuo della seconda riga PIR, le altre da 1 */
  number: number;
  /** la voce ha più catene (la notazione porta il nome della catena) */
  multichain: boolean;
}

/**
 * Numerazione di ogni colonna di una riga allineata (null su gap e marcatori): per catena, come i modelli di BioLoom
 * (ogni catena numerata da 1, la prima dal primo residuo indicato nella seconda riga PIR).
 */
export function residuePositions(seq: string, header: string): (ResiduePosition | null)[] {
  const segments = seq.split("/").length;
  const chains = chainIds(header, segments);
  const start = startNumber(header);
  const out: (ResiduePosition | null)[] = [];
  let index = 0;
  let segment = 0;
  let inChain = 0;
  for (let c = 0; c < seq.length; c++) {
    const ch = seq[c];
    if (ch === "/") {
      segment++;
      inChain = 0;
      out.push(null);
      continue;
    }
    if (!isResidue(ch)) {
      out.push(null);
      continue;
    }
    index++;
    inChain++;
    out.push({ index, segment, chain: chains[segment], number: segment === 0 ? inChain + start - 1 : inChain, multichain: segments > 1 });
  }
  return out;
}

/** "K48R", o "B:K2R" in una voce con più catene. */
export const mutationLabel = (p: ResiduePosition, wt: string, mut: string) => `${p.multichain ? `${p.chain}:` : ""}${wt}${p.number}${mut}`;

export interface PirMutation {
  /** riga della voce nell'allineamento */
  row: number;
  code: string;
  kind: string;
  /** posizione del residuo nella sequenza senza gap (da 1) */
  index: number;
  chain: string;
  /** numero del residuo nella sua catena */
  number: number;
  wt: string;
  mut: string;
  /** colonna attuale nell'allineamento */
  column: number;
  /** "K48R" o "B:K2R" */
  label: string;
  blosum: number | null;
}

export interface MutationReport {
  mutations: PirMutation[];
  /** voci la cui sequenza ha cambiato lunghezza: il confronto per posizione non è possibile */
  lengthChanged: string[];
  /** codici ripetuti: quelle voci non si confrontano */
  duplicated: string[];
}

/** Codici che compaiono più di una volta. */
export function duplicatedCodes(entries: Pick<PirEntry, "code">[]): string[] {
  const seen = new Set<string>();
  const dup = new Set<string>();
  for (const e of entries) (seen.has(e.code) ? dup : seen).add(e.code);
  return [...dup];
}

/** Mutazioni di ogni voce rispetto alla sequenza originale (per codice). Le voci nuove, rinominate o con codice ripetuto non si confrontano. */
export function pirMutations(original: Record<string, string>, entries: PirEntry[], rows: string[] = entries.map((e) => e.sequence)): MutationReport {
  const mutations: PirMutation[] = [];
  const lengthChanged: string[] = [];
  const duplicated = duplicatedCodes(entries);
  entries.forEach((e, row) => {
    const before = original[e.code];
    if (before === undefined || duplicated.includes(e.code)) return;
    const seq = rows[row] ?? e.sequence;
    const now = residuesOf(seq);
    if (now.length !== before.length) {
      lengthChanged.push(e.code);
      return;
    }
    if (now === before) return;
    residuePositions(seq, e.header).forEach((p, column) => {
      if (!p) return;
      const wt = before[p.index - 1];
      const mut = now[p.index - 1];
      if (wt === mut) return;
      mutations.push({ row, code: e.code, kind: e.kind, index: p.index, chain: p.chain, number: p.number, wt, mut, column, label: mutationLabel(p, wt, mut), blosum: blosum62(wt, mut) });
    });
  });
  return { mutations, lengthChanged, duplicated };
}

/** Sequenze originali (senza gap) delle voci, per codice: il riferimento delle mutazioni. I codici ripetuti restano fuori. */
export function originalSequences(entries: PirEntry[]): Record<string, string> {
  const duplicated = duplicatedCodes(entries);
  const out: Record<string, string> = {};
  for (const e of entries) if (!duplicated.includes(e.code)) out[e.code] = residuesOf(e.sequence);
  return out;
}

/** Riporta all'originale le mutazioni date (null se nessuna riga cambia). */
export function revertMutations(rows: string[], mutations: Pick<PirMutation, "row" | "index" | "wt">[]): string[] | null {
  const out = rows.slice();
  let changed = false;
  for (const m of mutations) {
    const col = columnOfResidue(out[m.row] ?? "", m.index);
    if (col === null || out[m.row][col].toUpperCase() === m.wt) continue;
    out[m.row] = out[m.row].slice(0, col) + m.wt + out[m.row].slice(col + 1);
    changed = true;
  }
  return changed ? out : null;
}
