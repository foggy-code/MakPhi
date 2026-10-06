// Controlli in tempo reale dell'Editor PIR: ciò che va guardato prima di modellare, per colonna, così che F8 / Maiusc+F8
// porti il cursore da un punto all'altro. Logica pura e testata (pirIssues.test.ts).
//
// - errori: separatori di catena "/" non nella stessa colonna in tutte le voci (MODELLER non legge il PIR), lettere
//   del template diverse dal residuo del file della struttura (MODELLER si ferma);
// - avvisi: gap che spezzano un'elica o un filamento di un template (lib/pirSecondary);
// - informazioni: residui del bersaglio senza nessun template di fronte (MODELLER li costruisce senza stampo).

import type { PirSecondaryEntry } from "../api/types";
import { columnOfResidue } from "./pirMutations";
import { conflictText, type GapConflict } from "./pirSecondary";

export type IssueSeverity = "err" | "warn" | "info";

export interface PirIssue {
  /** riga della voce interessata; null = tutta la colonna */
  row: number | null;
  /** colonne (da 0, comprese) */
  from: number;
  to: number;
  severity: IssueSeverity;
  text: string;
}

const isLetter = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

/** Unisce le colonne consecutive marcate da `hit` in tratti. */
function spans(width: number, hit: (c: number) => boolean): { from: number; to: number }[] {
  const out: { from: number; to: number }[] = [];
  for (let c = 0; c < width; c++) {
    if (!hit(c)) continue;
    const last = out[out.length - 1];
    if (last && last.to === c - 1) last.to = c;
    else out.push({ from: c, to: c });
  }
  return out;
}

/** Colonne in cui solo alcune voci hanno il separatore di catena. */
export function chainBreakIssues(rows: string[]): PirIssue[] {
  if (rows.length < 2) return [];
  const width = Math.max(0, ...rows.map((s) => s.length));
  return spans(width, (c) => {
    const n = rows.filter((s) => s[c] === "/").length;
    return n > 0 && n < rows.length;
  }).map((s) => ({ row: null, ...s, severity: "err", text: "Separatore di catena «/» non nella stessa colonna in tutte le voci" }));
}

/** Colonne fatte solo di gap (si tolgono con "Togli colonne di soli gap"). */
export function emptyColumnIssues(rows: string[]): PirIssue[] {
  const width = Math.max(0, ...rows.map((s) => s.length));
  return spans(width, (c) => rows.every((s) => !isLetter(s[c]) && s[c] !== "/" && s[c] !== "."))
    .map((s) => ({ row: null, ...s, severity: "info", text: "Colonna fatta solo di gap: si può togliere" }));
}

/** Residui del bersaglio con soli gap (o niente) di fronte in tutti i template. */
export function uncoveredIssues(rows: string[], kinds: string[]): PirIssue[] {
  const target = kinds.indexOf("sequence");
  const templates = kinds.map((k, i) => (k.startsWith("structure") ? i : -1)).filter((i) => i >= 0);
  if (target < 0 || !templates.length) return [];
  const t = rows[target];
  return spans(t.length, (c) => isLetter(t[c]) && templates.every((r) => !isLetter(rows[r][c])))
    .map((s) => ({ row: target, ...s, severity: "info", text: `Residui del bersaglio senza template di fronte (${s.to - s.from + 1}): MODELLER li costruisce senza stampo` }));
}

/** Lettere dei template diverse dal residuo del file della struttura (dalla risposta della struttura secondaria). */
export function mismatchIssues(rows: string[], entries: (PirSecondaryEntry | null)[]): PirIssue[] {
  const out: PirIssue[] = [];
  entries.forEach((e, row) => {
    for (const m of e?.mismatches ?? []) {
      const c = columnOfResidue(rows[row] ?? "", m.index);
      if (c !== null) out.push({ row, from: c, to: c, severity: "err", text: `${m.letter} nel PIR, ${m.residue} nel file: MODELLER si fermerà` });
    }
  });
  return out;
}

export function conflictIssues(conflicts: GapConflict[]): PirIssue[] {
  return conflicts.map((c) => ({ row: c.row, from: c.from, to: c.to, severity: "warn", text: conflictText(c) }));
}

/** Tutti i controlli, in ordine di colonna e di riga. */
export function sortIssues(list: PirIssue[]): PirIssue[] {
  return list.slice().sort((a, b) => a.from - b.from || (a.row ?? -1) - (b.row ?? -1));
}

/** Problemi che toccano la cella (quelli di colonna valgono per ogni riga). */
export function issuesAt(list: PirIssue[], r: number, c: number): PirIssue[] {
  return list.filter((i) => i.from <= c && c <= i.to && (i.row === null || i.row === r));
}

const sameIssue = (a: PirIssue, b: PirIssue) => a.row === b.row && a.from === b.from && a.to === b.to && a.text === b.text;

/**
 * Prossimo problema dopo la cella (dir 1) o prima (dir −1), in ordine di colonna e riga, ricominciando dall'altro capo.
 * `last`: il problema raggiunto con l'ultimo F8, se il cursore non si è mosso da allora: si passa al suo vicino
 * nell'elenco (così un problema di riga nella stessa colonna di uno di colonna non viene saltato). Null se non ce ne sono.
 */
export function nextIssue(list: PirIssue[], r: number, c: number, dir: 1 | -1, last?: PirIssue | null): PirIssue | null {
  if (!list.length) return null;
  const sorted = sortIssues(list);
  const at = last ? sorted.findIndex((i) => sameIssue(i, last)) : -1;
  if (at >= 0) return sorted[(at + dir + sorted.length) % sorted.length];
  const key = (i: PirIssue) => [i.from, i.row ?? r] as const;
  const after = (i: PirIssue) => {
    const [ic, ir] = key(i);
    return ic > c || (ic === c && ir > r);
  };
  const before = (i: PirIssue) => {
    const [ic, ir] = key(i);
    return ic < c || (ic === c && ir < r);
  };
  if (dir > 0) return sorted.find(after) ?? sorted[0];
  return [...sorted].reverse().find(before) ?? sorted[sorted.length - 1];
}

/** "2 errori · 1 avviso · 3 note". */
export function issueSummary(list: PirIssue[]): string {
  const n = (s: IssueSeverity) => list.filter((i) => i.severity === s).length;
  const parts: string[] = [];
  const err = n("err");
  const warn = n("warn");
  const info = n("info");
  if (err) parts.push(err === 1 ? "1 errore" : `${err} errori`);
  if (warn) parts.push(warn === 1 ? "1 avviso" : `${warn} avvisi`);
  if (info) parts.push(info === 1 ? "1 nota" : `${info} note`);
  return parts.join(" · ");
}
