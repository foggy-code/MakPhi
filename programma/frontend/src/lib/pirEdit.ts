// Modifica di un allineamento PIR nella griglia dell'Editor PIR: operazioni pure sulle righe allineate.
//
// Regole comuni:
// - si toccano solo i gap "-": residui, separatori di catena "/" e blocchi di ligandi "." non cambiano mai ordine;
// - dopo ogni operazione le righe vengono pareggiate con gap in coda e le colonne finali di soli gap tolte, così
//   l'allineamento resta rettangolare (requisito di MODELLER) senza che la larghezza cresca a ogni inserimento.

import type { PirEntry } from "./pir";

export const GAP = "-";

/** Selezione rettangolare: righe e colonne da 0, estremi compresi. */
export interface Selection {
  r0: number;
  r1: number;
  c0: number;
  c1: number;
}

export const normSel = (s: Selection): Selection => ({
  r0: Math.min(s.r0, s.r1),
  r1: Math.max(s.r0, s.r1),
  c0: Math.min(s.c0, s.c1),
  c1: Math.max(s.c0, s.c1),
});

const rowsOf = (s: Selection) => {
  const n = normSel(s);
  return Array.from({ length: n.r1 - n.r0 + 1 }, (_, i) => n.r0 + i);
};

/** Pareggia le righe con gap in coda e toglie le colonne finali fatte solo di gap. */
export function squareUp(rows: string[]): string[] {
  let width = Math.max(0, ...rows.map((s) => s.length));
  while (width > 0 && rows.every((s) => width > s.length || s[width - 1] === GAP)) width--;
  return rows.map((s) => (s.length >= width ? s.slice(0, width) : s + GAP.repeat(width - s.length)));
}

/** Inserisce `n` gap nella colonna `col` delle righe selezionate: il resto della riga scorre a destra. */
export function insertGaps(rows: string[], sel: Selection, col: number, n = 1): string[] {
  const targets = new Set(rowsOf(sel));
  return squareUp(rows.map((s, r) => (targets.has(r) ? s.slice(0, col) + GAP.repeat(n) + s.slice(col) : s)));
}

/**
 * Toglie fino a `n` gap a partire dalla colonna `col` delle righe selezionate (il resto della riga scorre a
 * sinistra). Una riga senza gap in quella colonna resta com'è. Restituisce null se nessuna riga cambia.
 */
export function deleteGaps(rows: string[], sel: Selection, col: number, n = 1): string[] | null {
  const targets = new Set(rowsOf(sel));
  let changed = false;
  const out = rows.map((s, r) => {
    if (!targets.has(r)) return s;
    let k = 0;
    while (k < n && s[col + k] === GAP) k++;
    if (!k) return s;
    changed = true;
    return s.slice(0, col) + s.slice(col + k);
  });
  return changed ? squareUp(out) : null;
}

/**
 * Sposta di una colonna il blocco selezionato: a sinistra se la colonna subito prima del blocco è un gap in tutte
 * le righe selezionate (il gap passa dopo il blocco), a destra nel caso opposto. Restituisce null se il blocco non
 * può muoversi (un residuo di fronte o il bordo dell'allineamento).
 */
export function slideBlock(rows: string[], sel: Selection, dir: -1 | 1): string[] | null {
  const s = normSel(sel);
  const targets = rowsOf(s);
  const free = dir < 0 ? s.c0 - 1 : s.c1 + 1;
  if (free < 0) return null;
  if (!targets.every((r) => rows[r][free] === GAP || (dir > 0 && free >= rows[r].length))) return null;
  const out = rows.slice();
  for (const r of targets) {
    const row = out[r].padEnd(s.c1 + 2, GAP);
    const block = row.slice(s.c0, s.c1 + 1);
    out[r] = dir < 0
      ? row.slice(0, free) + block + GAP + row.slice(s.c1 + 1)
      : row.slice(0, s.c0) + GAP + block + row.slice(free + 1);
  }
  return squareUp(out);
}

/** Toglie le colonne fatte solo di gap; null se non ce ne sono. */
export function removeGapColumns(rows: string[]): string[] | null {
  const width = Math.max(0, ...rows.map((s) => s.length));
  const keep: number[] = [];
  for (let c = 0; c < width; c++) if (!rows.every((s) => (s[c] ?? GAP) === GAP)) keep.push(c);
  if (keep.length === width) return null;
  return rows.map((s) => keep.map((c) => s[c] ?? GAP).join(""));
}

/** Righe di lunghezza diversa (l'allineamento non è rettangolare). */
export const isRagged = (rows: string[]) => rows.some((s) => s.length !== rows[0].length);

/** Testo PIR con le sequenze date al posto di quelle delle voci (intestazioni invariate, righe da 75 caratteri). */
export function serializePir(entries: PirEntry[], rows: string[] = entries.map((e) => e.sequence)): string {
  return entries
    .map((e, i) => {
      const seq = `${rows[i] ?? e.sequence}*`;
      const lines = seq.match(/.{1,75}/g) ?? ["*"];
      return [`>P1;${e.code}`, e.header, ...lines].join("\n");
    })
    .join("\n\n")
    .concat("\n");
}

/** Numero del residuo (da 1) nella colonna di una riga PIR: contano le lettere, non gap, "/" o "."; null fuori dai residui. */
export function pirResidueNumber(seq: string, col: number): number | null {
  if (!/[A-Za-z]/.test(seq[col] ?? "")) return null;
  let n = 0;
  for (let c = 0; c <= col; c++) if (/[A-Za-z]/.test(seq[c])) n++;
  return n;
}

/** Cronologia delle modifiche (annulla / ripristina) su istantanee di testo. */
export interface History {
  past: string[];
  future: string[];
}

export const HISTORY_LIMIT = 200;

export const emptyHistory = (): History => ({ past: [], future: [] });

/** Registra lo stato `prev` prima di una modifica (il ramo "ripristina" si perde). */
export function record(h: History, prev: string): History {
  if (h.past[h.past.length - 1] === prev) return { past: h.past, future: [] };
  return { past: [...h.past, prev].slice(-HISTORY_LIMIT), future: [] };
}

export function undo(h: History, current: string): { history: History; value: string } | null {
  if (!h.past.length) return null;
  return { history: { past: h.past.slice(0, -1), future: [current, ...h.future] }, value: h.past[h.past.length - 1] };
}

export function redo(h: History, current: string): { history: History; value: string } | null {
  if (!h.future.length) return null;
  return { history: { past: [...h.past, current], future: h.future.slice(1) }, value: h.future[0] };
}
