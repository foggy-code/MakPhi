// Regole della pagina Qualità condivise da tabella, viewer 3D e rapporto mostrato (logica pura, testata).
import type { ConsensusResult, LigandGroupCheck, QualityReport, ResidueRow, StructureSource } from "../api/types";
import { coordinationLines, ligandFocus, type CoordinationLine } from "./ligands";
import { resiNumber, type ResidueRef } from "./residueColoring";

/** Evidenza del 3D: residui a bastoncini, residuo scelto e ligando con le linee di coordinazione (che portano lo
 *  zoom sul sito). Solo la scelta di un ligando lo tiene: un residuo, una regione, uno scontro o i residui
 *  problematici lo tolgono, con le sue linee (altrimenti linee e zoom del ligando resterebbero su un'altra evidenza). */
export interface Highlight {
  focus: { chain: string; resi: number }[];
  selected: ResidueRef | null;
  ligand: LigandGroupCheck | null;
  lines: CoordinationLine[];
}

/** Residui evidenziati (e quello scelto), senza ligando. */
export function residuesHighlight(focus: { chain: string; resi: number }[], selected: ResidueRef | null = null): Highlight {
  return { focus, selected, ligand: null, lines: [] };
}

/** Uno scontro della tabella "Contatti sterici": i due residui ("A 43 …" → catena e numero), il primo scelto. */
export function clashHighlight(c: { atom1: string; atom2: string }): Highlight {
  const [c1, r1] = c.atom1.split(" ");
  const [c2, r2] = c.atom2.split(" ");
  return residuesHighlight([{ chain: c1, resi: resiNumber(r1) }, { chain: c2, resi: resiNumber(r2) }], { chain: c1, resnum: r1 });
}

/** Un gruppo della sezione "Ligandi e metalli": il gruppo, i donatori e gli scontri, con le linee di coordinazione. */
export function ligandHighlight(g: LigandGroupCheck): Highlight {
  return { focus: ligandFocus(g), selected: null, ligand: g, lines: coordinationLines(g) };
}

/** Segnali per residuo del consenso, solo se allineati ai residui del rapporto (altrimenti null). */
export function alignedPerResidue(report: Pick<QualityReport, "residues">, consensus: ConsensusResult | null | undefined): ConsensusResult["per_residue"] | null {
  const per = consensus?.per_residue;
  return per && per.counts.length === report.residues.length ? per : null;
}

/**
 * Residuo problematico: outlier di Ramachandran, coinvolto in un clash, oppure segnalato da almeno 2 metodi del
 * consenso (se il consenso è allineato al rapporto). Stessa definizione per il filtro della tabella e per
 * "Evidenzia tutti i residui problematici" nel 3D.
 */
export function isProblemResidue(r: ResidueRow, i: number, per: ConsensusResult["per_residue"] | null): boolean {
  return r.rama === "outlier" || r.clashes > 0 || (per ? per.counts[i] >= 2 : false);
}

/** Residui problematici del rapporto, nell'ordine del file. */
export function problemResidues(report: Pick<QualityReport, "residues">, consensus: ConsensusResult | null | undefined): ResidueRow[] {
  const per = alignedPerResidue(report, consensus);
  return report.residues.filter((r, i) => isProblemResidue(r, i, per));
}

/**
 * Struttura del rapporto mostrato (3D, colorazione, QMEAN): quella analizzata dal job di qualità aperto oppure quella
 * del rapporto salvato di un modello, mai la scelta corrente del selettore "Struttura da analizzare", che vale solo
 * per la prossima analisi.
 */
export function shownReportSource(qualityJob: string | null, jobSource: unknown, stored: Pick<QualityReport, "source"> | null): StructureSource | undefined {
  if (qualityJob) return (jobSource as StructureSource | undefined) ?? undefined;
  return stored?.source;
}
