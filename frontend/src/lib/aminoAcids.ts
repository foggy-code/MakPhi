// Amminoacidi: classi chimiche e colori di Clustal X, condivisi dall'allineamento (components/AlignmentView) e dalla
// composizione della sequenza (lib/workflowView), così gli stessi residui hanno lo stesso colore in tutte le pagine.
// Logica pura e testata (aminoAcids.test.ts).

export { AMINO } from "./mutations";

/** Classi chimiche dei residui e loro colori: gruppi e colori di Clustal X (come lo schema "Clustal" di Jalview). */
export const RESIDUE_CLASSES: { letters: string; label: string; one: string; color: string }[] = [
  { letters: "AILMFWV", label: "Idrofobici", one: "idrofobico", color: "#80a0f0" },
  { letters: "KR", label: "Positivi", one: "positivo", color: "#f01505" },
  { letters: "ED", label: "Negativi", one: "negativo", color: "#c048c0" },
  { letters: "NQST", label: "Polari", one: "polare", color: "#15c015" },
  { letters: "C", label: "Cys", one: "cisteina", color: "#f08080" },
  { letters: "G", label: "Gly", one: "glicina", color: "#f09048" },
  { letters: "P", label: "Pro", one: "prolina", color: "#c0c000" },
  { letters: "HY", label: "His/Tyr", one: "aromatico polare", color: "#15a4a4" },
];

/** Classe Clustal X di un residuo (codice a una lettera, maiuscole o minuscole); null per gap e non standard. */
export function residueClass(aa: string): (typeof RESIDUE_CLASSES)[number] | null {
  const c = aa.toUpperCase();
  return c.length === 1 ? RESIDUE_CLASSES.find((k) => k.letters.includes(c)) ?? null : null;
}

/** Colore Clustal X di un residuo (per le barre a gruppi più larghi: il colore del residuo rappresentativo). */
export function clustalColor(aa: string): string | undefined {
  return residueClass(aa)?.color;
}
