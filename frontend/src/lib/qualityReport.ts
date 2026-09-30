// Regole della pagina Qualità condivise da tabella, viewer 3D e rapporto mostrato (logica pura, testata).
import type { ConsensusResult, QualityReport, ResidueRow, StructureSource } from "../api/types";

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
