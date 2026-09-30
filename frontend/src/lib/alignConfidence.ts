// Confidenza di un allineamento bersaglio-template, colonna per colonna.
//
// Identità locale: per ogni riga di template e ogni colonna, percentuale di residui identici al bersaglio su
// una finestra scorrevole di 9 colonne centrata sulla colonna (troncata alle estremità), contando solo le
// colonne in cui il bersaglio ha un residuo: un gap del template di fronte a un residuo del bersaglio conta come
// non identico, perché lì il template non dà coordinate. La colonna prende il valore del miglior template che
// la copre. Zona crepuscolare: identità locale sotto il 25% (soglia di identità già usata dalla pipeline;
// Rost 1999, "Twilight zone of protein sequence alignments"). Non coperte: residui del bersaglio di fronte a
// soli gap.

import type { ContinuousScale } from "./colorscale";

export const CONFIDENCE_WINDOW = 9;
/** Stessa soglia predefinita del filtro "zona crepuscolare" della pipeline (twilight.identity). */
export const TWILIGHT_IDENTITY = 25;

const isResidue = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

export interface ColumnConfidence {
  /** il bersaglio ha un residuo in questa colonna */
  residue: boolean;
  /** numero del residuo del bersaglio (1-based), null sui gap del bersaglio */
  targetIndex: number | null;
  /** miglior identità locale (%) tra i template che coprono la colonna; null se nessuno la copre */
  best: number | null;
  covered: boolean;
  /** residuo del bersaglio coperto ma con identità locale sotto la soglia */
  twilight: boolean;
  /** residuo del bersaglio senza template di fronte */
  uncovered: boolean;
}

export interface ConfidenceRegion {
  kind: "twilight" | "uncovered";
  /** colonne (0-based, incluse) */
  fromColumn: number;
  toColumn: number;
  /** residui del bersaglio (1-based, inclusi) */
  fromResidue: number;
  toResidue: number;
}

export interface AlignmentConfidence {
  reference: number;
  window: number;
  threshold: number;
  /** identità locale (%) per riga e colonna; null per la riga del bersaglio */
  rows: ((number | null)[] | null)[];
  columns: ColumnConfidence[];
  regions: ConfidenceRegion[];
  summary: { residues: number; covered: number; twilight: number; uncovered: number; meanIdentity: number | null };
}

/** Identità locale (%) di `other` rispetto a `ref` per ogni colonna (finestra centrata di `window` colonne). */
export function localIdentity(ref: string, other: string, window = CONFIDENCE_WINDOW): (number | null)[] {
  const n = ref.length;
  const half = Math.floor(Math.max(1, window) / 2);
  // somme prefisse: residui del bersaglio e identità nella finestra in O(n)
  const res = new Array<number>(n + 1).fill(0);
  const same = new Array<number>(n + 1).fill(0);
  for (let c = 0; c < n; c++) {
    const r = isResidue(ref[c]);
    res[c + 1] = res[c] + (r ? 1 : 0);
    same[c + 1] = same[c] + (r && isResidue(other[c]) && ref[c].toUpperCase() === other[c].toUpperCase() ? 1 : 0);
  }
  const out: (number | null)[] = [];
  for (let c = 0; c < n; c++) {
    const lo = Math.max(0, c - half);
    const hi = Math.min(n, c + half + 1);
    const total = res[hi] - res[lo];
    out.push(total ? (100 * (same[hi] - same[lo])) / total : null);
  }
  return out;
}

/** Confidenza di tutte le colonne rispetto alla riga `reference` (il bersaglio). */
export function alignmentConfidence(aligned: string[], reference = 0, window = CONFIDENCE_WINDOW, threshold = TWILIGHT_IDENTITY): AlignmentConfidence {
  const ref = aligned[reference] ?? "";
  const n = ref.length;
  const rows = aligned.map((seq, r) => (r === reference ? null : localIdentity(ref, seq, window)));
  const columns: ColumnConfidence[] = [];
  let targetIndex = 0;
  for (let c = 0; c < n; c++) {
    const residue = isResidue(ref[c]);
    if (residue) targetIndex++;
    let best: number | null = null;
    let covered = false;
    aligned.forEach((seq, r) => {
      if (r === reference || !isResidue(seq[c])) return;
      covered = true;
      const v = rows[r]?.[c];
      if (v !== null && v !== undefined && (best === null || v > best)) best = v;
    });
    columns.push({
      residue, targetIndex: residue ? targetIndex : null, best: covered ? best : null, covered,
      twilight: residue && covered && best !== null && best < threshold,
      uncovered: residue && !covered && aligned.length > 1,
    });
  }
  const regions: ConfidenceRegion[] = [];
  for (const kind of ["twilight", "uncovered"] as const) {
    let open: ConfidenceRegion | null = null;
    columns.forEach((col, c) => {
      if (!col.residue) return; // i gap del bersaglio non interrompono né allungano una regione
      if (col[kind]) {
        if (open) {
          open.toColumn = c;
          open.toResidue = col.targetIndex!;
        } else {
          open = { kind, fromColumn: c, toColumn: c, fromResidue: col.targetIndex!, toResidue: col.targetIndex! };
          regions.push(open);
        }
      } else open = null;
    });
  }
  regions.sort((a, b) => a.fromColumn - b.fromColumn || a.kind.localeCompare(b.kind));
  const targetCols = columns.filter((c) => c.residue);
  const bests = targetCols.map((c) => c.best).filter((v): v is number => v !== null);
  return {
    reference, window, threshold, rows, columns, regions,
    summary: {
      residues: targetCols.length,
      covered: targetCols.filter((c) => c.covered).length,
      twilight: targetCols.filter((c) => c.twilight).length,
      uncovered: targetCols.filter((c) => c.uncovered).length,
      meanIdentity: bests.length ? bests.reduce((s, v) => s + v, 0) / bests.length : null,
    },
  };
}

/** Colori dell'identità locale: rosso sotto la soglia della zona crepuscolare (salto netto), poi verso il verde. */
export const IDENTITY_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: [
    { value: 0, color: "#b2182b" },
    { value: TWILIGHT_IDENTITY, color: "#ef8a62" },
    { value: TWILIGHT_IDENTITY, color: "#fddc5c" },
    { value: 50, color: "#a6d96a" },
    { value: 100, color: "#1a9850" },
  ],
  ticks: [{ value: 0, label: "0%" }, { value: TWILIGHT_IDENTITY, label: "25%" }, { value: 50, label: "50%" }, { value: 100, label: "100%" }],
  digits: 0,
  unit: "%",
};

/** Intervallo di residui del bersaglio come testo ("12–30" o "7"). */
export function regionLabel(r: ConfidenceRegion): string {
  return r.fromResidue === r.toResidue ? String(r.fromResidue) : `${r.fromResidue}–${r.toResidue}`;
}

/** Indice della riga del bersaglio in un PIR (voce "sequence"), altrimenti la prima. */
export function pirReference(kinds: string[]): number {
  const i = kinds.findIndex((k) => k === "sequence");
  return i >= 0 ? i : 0;
}
