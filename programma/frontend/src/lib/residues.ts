import type { ResidueRef } from "./residueColoring";

/**
 * Nomi e confronti dei residui comuni a grafici, tabelle e dettagli (un solo posto: prima c'erano copie con lo stesso
 * nome e argomenti in ordine diverso). Etichette della struttura secondaria a 3 stati: lo stato DSSP E è "filamento β"
 * come in DSSP_STATES (qualityView) e nella predizione (disorder).
 */

/** Nome del residuo come nei suggerimenti e nelle tabelle: "ALA A12". */
export function residueName(r: ResidueRef & { resname: string }): string {
  return `${r.resname} ${r.chain}${r.resnum}`;
}

/** Stesso residuo (catena e numero); `a` può mancare (nessuna selezione). */
export function sameResidue(a: ResidueRef | null | undefined, b: ResidueRef): boolean {
  return !!a && a.chain === b.chain && a.resnum === b.resnum;
}

/** Struttura secondaria a 3 stati (ss3): elica, filamento β, coil. */
export const SS3_LABEL: Record<string, string> = { H: "elica", E: "filamento β", C: "coil" };
