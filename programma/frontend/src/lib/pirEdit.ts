// Modifica di un allineamento PIR nella griglia dell'Editor PIR: operazioni pure sulle righe allineate.
//
// Regole comuni:
// - si toccano solo i gap "-": residui, separatori di catena "/" e blocchi di ligandi "." non cambiano mai ordine;
// - dopo ogni operazione le righe vengono pareggiate con gap in coda e le colonne finali di soli gap tolte, così
//   l'allineamento resta rettangolare (requisito di MODELLER) senza che la larghezza cresca a ogni inserimento;
// - un'operazione che non cambia nulla restituisce null (nessun passo vuoto nella cronologia);
// - il testo PIR si riscrive sostituendo solo le righe delle sequenze (rewritePir): commenti, seconde righe, testo dopo
//   l'`*` e minuscole dei residui non toccati restano come sono.

import { pirLayout, rawSequence } from "./pir";

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

/** Le due liste di righe sono identiche. */
export const sameRows = (a: string[], b: string[]) => a.length === b.length && a.every((s, i) => s === b[i]);

/** Pareggia le righe con gap in coda e toglie le colonne finali fatte solo di gap. */
export function squareUp(rows: string[]): string[] {
  let width = Math.max(0, ...rows.map((s) => s.length));
  while (width > 0 && rows.every((s) => width > s.length || s[width - 1] === GAP)) width--;
  return rows.map((s) => (s.length >= width ? s.slice(0, width) : s + GAP.repeat(width - s.length)));
}

const changed = (before: string[], after: string[]) => (sameRows(before, after) ? null : after);

/**
 * Inserisce `n` gap nella colonna `col` delle righe selezionate: il resto della riga scorre a destra. Null se non
 * cambia nulla (un gap in coda viene tolto subito insieme alla colonna finale di soli gap).
 */
export function insertGaps(rows: string[], sel: Selection, col: number, n = 1): string[] | null {
  const targets = new Set(rowsOf(sel));
  return changed(rows, squareUp(rows.map((s, r) => (targets.has(r) ? s.slice(0, col) + GAP.repeat(n) + s.slice(col) : s))));
}

/**
 * Toglie fino a `n` gap a partire dalla colonna `col` delle righe selezionate (il resto della riga scorre a
 * sinistra). Una riga senza gap in quella colonna resta com'è. Null se nessuna riga cambia.
 */
export function deleteGaps(rows: string[], sel: Selection, col: number, n = 1): string[] | null {
  const targets = new Set(rowsOf(sel));
  const out = rows.map((s, r) => {
    if (!targets.has(r)) return s;
    let k = 0;
    while (k < n && s[col + k] === GAP) k++;
    return k ? s.slice(0, col) + s.slice(col + k) : s;
  });
  return changed(rows, squareUp(out));
}

/**
 * Sposta di una colonna il blocco selezionato: a sinistra se la colonna subito prima del blocco è un gap in tutte
 * le righe selezionate (il gap passa dopo il blocco), a destra nel caso opposto. Null se il blocco non può muoversi
 * (un residuo di fronte o il bordo dell'allineamento) o se lo spostamento non cambia nulla (blocco di soli gap).
 */
export function slideBlock(rows: string[], sel: Selection, dir: -1 | 1): string[] | null {
  const s = normSel(sel);
  const targets = rowsOf(s);
  if (isGapBlock(rows, s)) return slideGaps(rows, s, dir);
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
  return changed(rows, squareUp(out));
}

/** Il blocco selezionato è fatto solo di gap in tutte le righe selezionate. */
export function isGapBlock(rows: string[], sel: Selection): boolean {
  const s = normSel(sel);
  return rowsOf(s).every((r) => rows[r].slice(s.c0, s.c1 + 1).padEnd(s.c1 - s.c0 + 1, GAP).split("").every((c) => c === GAP));
}

/** Caratteri che i gap non scavalcano: devono restare nella stessa colonna in tutte le voci. */
const FIXED = new Set(["/", "."]);

/**
 * Sposta di una colonna un blocco di soli gap (una zona di inserzione o delezione): il carattere subito oltre il blocco
 * passa dall'altra parte, quindi i residui restano in ordine e cambia solo dove cade il gap. Null al bordo, se oltre il
 * blocco c'è un separatore di catena o un ligando ("/", ".") o se non cambia nulla.
 */
function slideGaps(rows: string[], s: Selection, dir: -1 | 1): string[] | null {
  const width = s.c1 - s.c0 + 1;
  const near = dir < 0 ? s.c0 - 1 : s.c1 + 1;
  if (near < 0) return null;
  const out = rows.slice();
  for (const r of rowsOf(s)) {
    const row = out[r].padEnd(s.c1 + 1, GAP);
    const c = row[near];
    if (c === undefined) continue; // oltre la fine della riga: niente da scambiare
    if (FIXED.has(c)) return null;
    const gaps = GAP.repeat(width);
    out[r] = dir < 0
      ? row.slice(0, near) + gaps + c + row.slice(s.c1 + 1)
      : row.slice(0, s.c0) + c + gaps + row.slice(near + 1);
  }
  return changed(rows, squareUp(out));
}

/**
 * Inserisce nella colonna `col` una colonna di marcatori: "/" (separatore di catena) in tutte le voci, perché MODELLER
 * lo vuole nella stessa colonna ovunque; "." (blocco di un ligando) nelle righe `marked`, gap nelle altre.
 */
export function insertMarkerColumn(rows: string[], col: number, mark: "/" | ".", marked: Set<number>): string[] {
  return squareUp(rows.map((s, r) => {
    const row = s.padEnd(col, GAP);
    const ch = mark === "/" || marked.has(r) ? mark : GAP;
    return row.slice(0, col) + ch + row.slice(col);
  }));
}

export interface TrimResult {
  rows: string[];
  /** residui tolti al bersaglio all'inizio e alla fine */
  start: number;
  end: number;
}

/**
 * Toglie le colonne iniziali e finali in cui nessun template ha un residuo (code del bersaglio senza stampo, che
 * MODELLER costruirebbe come anse libere), senza scavalcare un separatore di catena. Null se non c'è nulla da togliere
 * o se mancano bersaglio o template.
 */
export function trimUncoveredEnds(rows: string[], kinds: string[]): TrimResult | null {
  const target = kinds.indexOf("sequence");
  const templates = kinds.map((k, i) => (k.startsWith("structure") ? i : -1)).filter((i) => i >= 0);
  if (target < 0 || !templates.length) return null;
  const width = Math.max(0, ...rows.map((s) => s.length));
  // coperta: un template, o un'altra voce che non è il bersaglio (non si tolgono residui ad altre sequenze)
  const covered = (c: number) => rows.some((s, r) => r !== target && isLetter(s[c]));
  const marker = (c: number) => rows.some((s) => s[c] === "/" || s[c] === ".");
  let from = 0;
  while (from < width && !covered(from) && !marker(from)) from++;
  let to = width;
  while (to > from && !covered(to - 1) && !marker(to - 1)) to--;
  // una catena intera senza template resterebbe vuota davanti al suo separatore: da quel lato non si ritaglia
  if (from > 0 && marker(from)) from = 0;
  if (to < width && to > 0 && marker(to - 1)) to = width;
  if (from === 0 && to === width) return null;
  const t = rows[target];
  const count = (a: number, b: number) => t.slice(a, b).split("").filter(isLetter).length;
  const start = count(0, from);
  const end = count(to, width);
  if (!start && !end) return null;
  return { rows: rows.map((s) => s.slice(from, to)), start, end };
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

/** Larghezza delle righe di sequenza riscritte, se il testo non ne suggerisce un'altra. */
export const LINE_WIDTH = 75;

const isLetter = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

/**
 * Testo PIR con le righe allineate date al posto delle sequenze delle voci (nello stesso ordine di parsePir). Cambiano
 * solo le righe della sequenza fino all'`*`: il resto del testo (righe prima della prima voce, seconde righe, testo dopo
 * l'`*`, righe vuote) resta com'è. Un residuo uguale all'originale a meno delle maiuscole tiene la lettera originale
 * (es. "w", l'acqua di MODELLER). Le righe vanno a capo come nel testo originale (75 caratteri se la sequenza era su
 * una riga sola). Una voce senza seconda riga ne riceve una minima (`sequence:<codice>::::::::`).
 */
export function rewritePir(text: string, rows: string[]): string {
  const { lines, layout } = pirLayout(text);
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  // dall'ultima voce alla prima: le sostituzioni non spostano le righe delle voci precedenti
  for (let i = layout.length - 1; i >= 0; i--) {
    const e = layout[i];
    const row = rows[i];
    if (row === undefined) continue;
    const raw = rawSequence(lines, e);
    const letters = raw.split("").filter(isLetter);
    let k = 0;
    const seq = row
      .split("")
      .map((c) => {
        if (!isLetter(c)) return c;
        const before = letters[k++];
        return before !== undefined && before.toUpperCase() === c.toUpperCase() ? before : c;
      })
      .join("");
    const end = e.starLine >= 0 ? e.starLine + 1 : e.seqEnd;
    const block = lines.slice(e.seqStart, end).filter((l) => l.trim());
    const width = block.length > 1 ? Math.max(10, block[0].trim().length) : LINE_WIDTH;
    const tail = e.starLine >= 0 ? lines[e.starLine].slice(lines[e.starLine].indexOf("*") + 1) : "";
    const out = `${seq}*`.match(new RegExp(`.{1,${width}}`, "g")) ?? ["*"];
    out[out.length - 1] += tail;
    if (e.headerLine < 0) out.unshift(`sequence:${lines[e.codeLine].trim().slice(4).trim()}::::::::`);
    lines.splice(e.seqStart, end - e.seqStart, ...out);
  }
  return lines.join(eol);
}

/**
 * Cronologia delle modifiche (annulla / ripristina) su istantanee: di testo, o di tutto ciò che una modifica cambia
 * insieme (l'Editor PIR registra testo, riferimento delle sostituzioni e annotazioni).
 */
export interface History<T = string> {
  past: T[];
  future: T[];
}

export const HISTORY_LIMIT = 200;

export const emptyHistory = <T = string>(): History<T> => ({ past: [], future: [] });

/** Registra lo stato `prev` prima di una modifica (il ramo "ripristina" si perde); `same` evita istantanee doppie. */
export function record<T>(h: History<T>, prev: T, same: (a: T, b: T) => boolean = (a, b) => a === b): History<T> {
  const last = h.past[h.past.length - 1];
  if (last !== undefined && same(last, prev)) return { past: h.past, future: [] };
  return { past: [...h.past, prev].slice(-HISTORY_LIMIT), future: [] };
}

export function undo<T>(h: History<T>, current: T): { history: History<T>; value: T } | null {
  if (!h.past.length) return null;
  return { history: { past: h.past.slice(0, -1), future: [current, ...h.future] }, value: h.past[h.past.length - 1] };
}

export function redo<T>(h: History<T>, current: T): { history: History<T>; value: T } | null {
  if (!h.future.length) return null;
  return { history: { past: [...h.past, current], future: h.future.slice(1) }, value: h.future[0] };
}
