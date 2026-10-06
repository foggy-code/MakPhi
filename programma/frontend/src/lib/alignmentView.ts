// Allineamento leggibile (components/AlignmentView): righello numerato, numero del residuo in una colonna, simboli di
// conservazione. Logica pura e testata.

/**
 * Opacità (esadecimale, da aggiungere a un colore #rrggbb) dei fondi tenui: classe chimica nelle colonne non identiche
 * e identità locale nella modalità Confidenza. Il testo resta quello del tema: alignmentView.test.ts ne verifica il contrasto.
 */
export const CLASS_TINT = "38";
export const CONFIDENCE_TINT = "55";

/** Gap nelle sequenze allineate ("-", o "." in alcuni formati). */
export const isGap = (c: string | undefined) => c === "-" || c === ".";

/**
 * Righello del blocco di colonne [from, to) (indici da 0, colonne numerate da 1): il numero di ogni decima colonna
 * finisce sulla sua colonna, un "·" segna le quinte; un numero che non ci sta diventa "|". Un carattere per colonna.
 */
export function rulerText(from: number, to: number): string {
  const out = Array.from({ length: Math.max(0, to - from) }, () => " ");
  let free = 0; // primo carattere dopo l'ultimo segno scritto
  for (let k = 0; k < out.length; k++) {
    const col = from + k + 1;
    if (col % 5 !== 0) continue;
    const label = col % 10 === 0 ? String(col) : "·";
    const start = k - label.length + 1;
    if (start >= free) for (let j = 0; j < label.length; j++) out[start + j] = label[j];
    else out[k] = "|";
    free = k + 1;
  }
  return out.join("");
}

/** Numero del residuo (da 1, gap esclusi) nella colonna `col` della sequenza; null su un gap o fuori sequenza. */
export function residueNumberAt(seq: string, col: number): number | null {
  if (col < 0 || col >= seq.length || isGap(seq[col])) return null;
  let n = 0;
  for (let c = 0; c <= col; c++) if (!isGap(seq[c])) n++;
  return n;
}

/** Simboli della riga di consenso (Clustal). */
export const CONSERVATION_TEXT: Record<string, string> = {
  "*": "colonna identica in tutte le sequenze",
  ":": "sostituzioni molto simili",
  ".": "sostituzioni poco simili",
};

/** La colonna ha lo stesso residuo in tutte le sequenze (nessun gap). */
export function identicalColumns(aligned: string[]): boolean[] {
  const length = aligned[0]?.length ?? 0;
  const out: boolean[] = [];
  for (let c = 0; c < length; c++) {
    const first = aligned[0][c];
    out.push(!isGap(first) && aligned.every((s) => s[c] === first));
  }
  return out;
}
