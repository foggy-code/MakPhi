// Annotazioni sulle colonne dell'Editor PIR (sito attivo, cisteine di un ponte disolfuro, motivi…): legate ai residui del
// bersaglio, non alle colonne, così seguono lo spostamento dei gap. Salvate nei metadati del documento. Logica pura e
// testata (pirAnnotations.test.ts).

import { columnOfResidue } from "./pirMutations";

export interface PirAnnotation {
  id: string;
  label: string;
  /** residui del bersaglio (da 1, compresi) */
  from: number;
  to: number;
  color: string;
}

export const ANNOTATION_COLORS: { value: string; label: string }[] = [
  { value: "#a855f7", label: "viola" },
  { value: "#22c55e", label: "verde" },
  { value: "#ef4444", label: "rosso" },
  { value: "#0ea5e9", label: "azzurro" },
];

const isLetter = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

/** Residui del bersaglio (da 1) che cadono nelle colonne [c0, c1]; null se nessuno. */
export function residuesInColumns(target: string, c0: number, c1: number): { from: number; to: number } | null {
  let n = 0;
  let from = 0;
  let to = 0;
  for (let c = 0; c < target.length && c <= c1; c++) {
    if (!isLetter(target[c])) continue;
    n++;
    if (c >= c0) {
      if (!from) from = n;
      to = n;
    }
  }
  return from ? { from, to } : null;
}

/** Colonne attuali di un'annotazione nella riga del bersaglio; null se i residui non ci sono più. */
export function annotationColumns(target: string, a: Pick<PirAnnotation, "from" | "to">): { from: number; to: number } | null {
  const from = columnOfResidue(target, a.from);
  const to = columnOfResidue(target, a.to);
  return from === null || to === null ? null : { from, to };
}

/** Annotazioni lette dai metadati del documento (quelle malformate restano fuori). */
export function readAnnotations(meta: Record<string, unknown> | undefined): PirAnnotation[] {
  const list = meta?.annotations;
  if (!Array.isArray(list)) return [];
  return list.filter((a): a is PirAnnotation =>
    !!a && typeof a === "object" && typeof a.id === "string" && typeof a.label === "string"
    && Number.isInteger(a.from) && Number.isInteger(a.to) && a.from >= 1 && a.to >= a.from && typeof a.color === "string");
}
