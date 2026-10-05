// FASTA dell'allineamento mostrato (components/AlignmentView). Logica pura e testata.
import { recordsToFasta } from "./files";

/**
 * Allineamento in FASTA con i gap. Le voci PIR possono contenere "/" (interruzione di catena) e "." (residuo BLK), che il
 * FASTA non prevede: le colonne di interruzione comuni a tutte le sequenze si tolgono (catene concatenate), gli altri
 * "/" e i "." diventano gap, così le colonne restano allineate.
 */
export function alignmentFasta(ids: string[], aligned: string[]): string {
  const length = aligned.reduce((m, s) => Math.max(m, s.length), 0);
  const breaks = new Set<number>();
  for (let c = 0; c < length; c++) if (aligned.every((s) => s[c] === "/")) breaks.add(c);
  const clean = (s: string) => {
    let out = "";
    for (let c = 0; c < s.length; c++) if (!breaks.has(c)) out += s[c] === "/" || s[c] === "." ? "-" : s[c];
    return out;
  };
  return recordsToFasta(ids.map((name, i) => ({ name, sequence: clean(aligned[i] ?? "") })));
}
