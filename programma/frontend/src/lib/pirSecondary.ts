// Struttura secondaria sull'allineamento dell'Editor PIR: stati per colonna dalla struttura secondaria per residuo
// (DSSP dei template, GOR del bersaglio: POST /api/pir/secondary) e gap che cadono dentro un'elica o un filamento di un
// template. Logica pura e testata (pirSecondary.test.ts).
//
// Il server risponde con un carattere per residuo della voce, non per colonna: lo spostamento dei gap non richiede di
// richiedere di nuovo nulla, le colonne si ricalcolano qui dalla riga attuale.

import type { PirEntry } from "./pir";

/** Stato a tre: H elica, E filamento, C coil; "?" residuo senza assegnazione; " " colonna senza residuo della voce. */
export type SS3 = "H" | "E" | "C" | "?" | " ";

const isLetter = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

/** DSSP a 8 stati (o predizione a 3) → 3 stati: H G I elica, E B filamento, "?" sconosciuto, il resto coil. */
export function toSS3(c: string | undefined): SS3 {
  if (c === undefined || c === "?") return "?";
  if (c === "H" || c === "G" || c === "I") return "H";
  if (c === "E" || c === "B") return "E";
  return "C";
}

/** Stato per colonna di una riga allineata: la k-esima lettera prende il k-esimo stato (mancante → "?"). */
export function columnStates(row: string, perResidue: string): SS3[] {
  const out: SS3[] = [];
  let k = 0;
  for (const c of row) out.push(isLetter(c) ? toSS3(perResidue[k++]) : " ");
  return out;
}

/** Stati DSSP a 8 per colonna (per il dettaglio nella riga di stato): " " senza residuo, "?" sconosciuto. */
export function columnCodes(row: string, perResidue: string): string[] {
  const out: string[] = [];
  let k = 0;
  for (const c of row) out.push(isLetter(c) ? perResidue[k++] ?? "?" : " ");
  return out;
}

/**
 * Chiave delle richieste: cambia solo con ciò che cambia la risposta (codici, tipi, seconde righe, residui, template del
 * documento), non con la posizione dei gap.
 */
export function secondaryKey(entries: PirEntry[], templates: unknown): string {
  return JSON.stringify([entries.map((e) => [e.code, e.kind, e.header, e.sequence.replace(/[^A-Za-z/]/g, "")]), templates ?? []]);
}

export interface GapConflict {
  /** riga del template */
  row: number;
  /** insertion: il bersaglio ha residui di fronte a un gap del template dentro un elemento; deletion: il bersaglio ha un gap di fronte a residui di un elemento */
  kind: "insertion" | "deletion";
  element: "H" | "E";
  /** colonne (da 0, comprese) */
  from: number;
  to: number;
}

/** Stato del residuo più vicino della riga nella direzione `dir`, senza scavalcare un separatore di catena. */
function neighbour(row: string, states: SS3[], c: number, dir: -1 | 1): SS3 | null {
  for (let k = c + dir; k >= 0 && k < row.length; k += dir) {
    if (row[k] === "/") return null;
    if (isLetter(row[k])) return states[k];
  }
  return null;
}

/** La riga ha un residuo nella direzione `dir` dalla colonna, nella stessa catena. */
function hasResidue(row: string, c: number, dir: -1 | 1): boolean {
  for (let k = c + dir; k >= 0 && k < row.length; k += dir) {
    if (row[k] === "/") return false;
    if (isLetter(row[k])) return true;
  }
  return false;
}

/**
 * Gap che spezzano un'elica o un filamento di un template (DSSP): una inserzione del bersaglio dove il template ha un gap
 * fra due residui dello stesso elemento, o una delezione del bersaglio di fronte a residui di un elemento. MODELLER
 * deve inventare o accorciare la struttura proprio lì: sono le colonne da controllare a mano. Colonne vicine dello
 * stesso tipo si uniscono.
 */
export function gapConflicts(rows: string[], kinds: string[], states: (SS3[] | null)[], sources: (string | null)[]): GapConflict[] {
  const target = kinds.indexOf("sequence");
  if (target < 0) return [];
  const t = rows[target] ?? "";
  const out: GapConflict[] = [];
  rows.forEach((row, r) => {
    const s = states[r];
    if (r === target || !s || sources[r] !== "dssp" || !kinds[r]?.startsWith("structure")) return;
    let open: GapConflict | null = null;
    for (let c = 0; c < row.length; c++) {
      let hit: Pick<GapConflict, "kind" | "element"> | null = null;
      const p = row[c];
      // delezione solo all'interno della catena del bersaglio: residui del template prima e dopo il bersaglio (code
      // N- e C-terminali) restano semplicemente fuori dal modello, non c'è nulla da accorciare
      if (isLetter(p) && t[c] === "-" && (s[c] === "H" || s[c] === "E") && hasResidue(t, c, -1) && hasResidue(t, c, 1)) {
        hit = { kind: "deletion", element: s[c] as "H" | "E" };
      }
      else if (p === "-" && isLetter(t[c])) {
        const left = neighbour(row, s, c, -1);
        const right = neighbour(row, s, c, 1);
        if (left && left === right && (left === "H" || left === "E")) hit = { kind: "insertion", element: left };
      }
      if (hit && open && open.kind === hit.kind && open.element === hit.element && open.to === c - 1) open.to = c;
      else if (hit) {
        open = { row: r, ...hit, from: c, to: c };
        out.push(open);
      } else open = null;
    }
  });
  return out;
}

const ELEMENT = { H: "un'elica", E: "un filamento β" } as const;

/** "Inserzione del bersaglio dentro un'elica" / "Delezione del bersaglio in un filamento β". */
export function conflictText(c: Pick<GapConflict, "kind" | "element">): string {
  return c.kind === "insertion" ? `Inserzione del bersaglio dentro ${ELEMENT[c.element]}` : `Delezione del bersaglio in ${ELEMENT[c.element]}`;
}

/** Colonne in conflitto come "riga:colonna" (per evidenziarle nella griglia). */
export function conflictCells(conflicts: GapConflict[]): Set<string> {
  const out = new Set<string>();
  for (const c of conflicts) for (let k = c.from; k <= c.to; k++) out.add(`${c.row}:${k}`);
  return out;
}
