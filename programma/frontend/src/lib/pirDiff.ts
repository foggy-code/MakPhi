// Confronto dell'Editor PIR con la versione salvata: quali residui sono allineati diversamente. Non si confrontano le
// colonne (un gap all'inizio le sposterebbe tutte) ma le coppie bersaglio–template: un residuo del bersaglio è cambiato
// se ora sta di fronte a un residuo diverso del template (o a un gap, o viceversa). Logica pura e testata.

import type { PirEntry } from "./pir";

const isLetter = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

/** Per ogni residuo del bersaglio (da 0) il residuo del template di fronte (da 0), o −1 se di fronte c'è un gap. */
export function partners(target: string, template: string): number[] {
  const out: number[] = [];
  let p = -1;
  for (let c = 0; c < target.length; c++) {
    const tl = isLetter(target[c]);
    if (isLetter(template[c])) p++;
    if (tl) out.push(isLetter(template[c]) ? p : -1);
  }
  return out;
}

export interface TemplateDiff {
  row: number;
  code: string;
  /** residui del bersaglio (da 0) allineati diversamente */
  changed: number[];
}

export interface AlignmentDiff {
  templates: TemplateDiff[];
  /** voci che non si possono confrontare (nuove, rinominate o con residui aggiunti o tolti) */
  notComparable: string[];
}

/**
 * Residui del bersaglio allineati diversamente a ciascun template rispetto alla versione salvata. Si confrontano le voci
 * con lo stesso codice e lo stesso numero di residui.
 */
export function alignmentDiff(saved: PirEntry[], current: PirEntry[]): AlignmentDiff {
  const out: AlignmentDiff = { templates: [], notComparable: [] };
  const residues = (s: string) => s.split("").filter(isLetter).length;
  const t0 = saved.find((e) => e.kind === "sequence");
  const t1 = current.find((e) => e.kind === "sequence");
  if (!t0 || !t1 || t0.code !== t1.code || residues(t0.sequence) !== residues(t1.sequence)) {
    if (t1) out.notComparable.push(t1.code);
    return out;
  }
  current.forEach((e, row) => {
    if (!e.kind.startsWith("structure")) return;
    const before = saved.find((s) => s.code === e.code);
    if (!before || residues(before.sequence) !== residues(e.sequence)) {
      out.notComparable.push(e.code);
      return;
    }
    const a = partners(t0.sequence, before.sequence);
    const b = partners(t1.sequence, e.sequence);
    const changed = a.map((x, i) => (x !== b[i] ? i : -1)).filter((i) => i >= 0);
    if (changed.length) out.templates.push({ row, code: e.code, changed });
  });
  return out;
}

/** Celle da evidenziare ("riga:colonna"): i residui cambiati del bersaglio e i residui dei template di fronte a loro. */
export function diffCells(diff: AlignmentDiff, rows: string[], targetRow: number): Set<string> {
  const out = new Set<string>();
  const t = rows[targetRow] ?? "";
  const columns: number[] = [];
  for (let c = 0; c < t.length; c++) if (isLetter(t[c])) columns.push(c);
  for (const d of diff.templates) {
    for (const i of d.changed) {
      const c = columns[i];
      if (c === undefined) continue;
      out.add(`${targetRow}:${c}`);
      if (isLetter(rows[d.row]?.[c])) out.add(`${d.row}:${c}`);
    }
  }
  return out;
}

/** "24–28, 40" (numeri da 1) da indici da 0 ordinati. */
export function ranges(indices: number[]): string {
  const parts: string[] = [];
  let start = -1;
  let prev = -2;
  for (const i of [...indices].sort((x, y) => x - y)) {
    if (i !== prev + 1) {
      if (start >= 0) parts.push(start === prev ? `${start + 1}` : `${start + 1}–${prev + 1}`);
      start = i;
    }
    prev = i;
  }
  if (start >= 0) parts.push(start === prev ? `${start + 1}` : `${start + 1}–${prev + 1}`);
  return parts.join(", ");
}
