import { useMemo } from "react";
import type { ConsensusResult, QualityReport } from "../../api/types";
import { fmtNum } from "../../lib/format";
import { favoredTone, outlierTone } from "../../lib/qualityView";
import { classLabel, fmtAngle, type RamaPoint } from "../../lib/ramachandran";
import type { ResidueRef } from "../../lib/residueColoring";
import { sameResidue } from "../../lib/residues";
import { RamachandranPlot } from "../charts";
import { Alert, Disclosure, Stat } from "../ui";
import { ResidueDetail } from "./ResidueTable";

/** Residuo come chip: nome, angoli e classe; il clic lo seleziona nel grafico. */
function PointChip({ p, selected, onSelect }: { p: RamaPoint; selected: ResidueRef | null; onSelect: (r: ResidueRef) => void }) {
  const on = sameResidue(selected, p);
  return (
    <button type="button" className="chip" aria-pressed={on} title={`φ ${fmtAngle(p.phi, 1)}°, ψ ${fmtAngle(p.psi, 1)}° · ${classLabel(p.class)} · seleziona nel grafico`}
      onClick={() => onSelect(p)}>
      <b>{p.resname} {p.chain}{p.resnum}</b>
      <span className="text-2">φ {fmtAngle(p.phi)}°, ψ {fmtAngle(p.psi)}°</span>
      <span className="text-2">{classLabel(p.class)}</span>
    </button>
  );
}

/**
 * Scheda Ramachandran: il grafico (colori standard di PROCHECK) e, accanto, conteggi per regione, outlier e residui in
 * regioni consentite come chip, il residuo selezionato e la nota sulle regioni.
 */
export function RamaTab({ report, consensus, selected, onSelect, onShow3d, onTable }: {
  report: QualityReport;
  consensus: ConsensusResult | null;
  selected: ResidueRef | null;
  onSelect: (r: ResidueRef) => void;
  onShow3d: () => void;
  onTable: () => void;
}) {
  const rs = report.ramachandran.summary;
  const outliers = useMemo(() => report.ramachandran.points.filter((p) => p.region === "outlier"), [report.ramachandran.points]);
  const allowed = useMemo(() => report.ramachandran.points.filter((p) => p.region === "allowed"), [report.ramachandran.points]);
  // senza residui valutabili le percentuali valgono 0 (domain/ramachandran): nessun esito
  const rated = rs.total > 0;
  return (
    <div className="quality-rama">
      <RamachandranPlot report={report.ramachandran} onSelect={onSelect} selected={selected} />
      <div className="stack">
        {!rated && <Alert tone="info">Nessun residuo con angoli φ/ψ valutabili.</Alert>}
        <div className="grid grid-2">
          <Stat label="Favorite" value={<>{fmtNum(rs.favored_pct)}<small>%</small></>} note={`${rs.favored} su ${rs.total} residui`} tone={rated ? favoredTone(rs.favored_pct) : undefined} />
          <Stat label="Consentite" value={<>{fmtNum(rs.allowed_pct)}<small>%</small></>} note={`${rs.allowed} residui`} tone="info" />
          <Stat label="Outlier" value={<>{fmtNum(rs.outlier_pct)}<small>%</small></>} note={`${rs.outlier} residui · penalità oltre lo 0,5%`} tone={rated ? outlierTone(rs.outlier_pct) : undefined} />
          <Stat label="Cis non prolinici" value={rs.cis_non_proline} note="legami peptidici cis rari" tone={rs.cis_non_proline > 0 ? "warn" : "ok"} />
        </div>
        {rated && (
          <section className="stack-sm">
            <h4>Outlier ({outliers.length})</h4>
            {outliers.length === 0 ? <Alert tone="ok">Nessun residuo in regione non consentita.</Alert> : (
              <div className="row" style={{ gap: 6 }}>
                {outliers.map((p) => <PointChip key={`${p.chain}${p.resnum}`} p={p} selected={selected} onSelect={onSelect} />)}
              </div>
            )}
          </section>
        )}
        {allowed.length > 0 && (
          <Disclosure title="Residui in regioni consentite" extra={allowed.length}>
            <div className="row" style={{ gap: 6 }}>
              {allowed.map((p) => <PointChip key={`${p.chain}${p.resnum}`} p={p} selected={selected} onSelect={onSelect} />)}
            </div>
          </Disclosure>
        )}
        {selected && <ResidueDetail report={report} selected={selected} consensus={consensus} onShow3d={onShow3d} onTable={onTable} />}
        <Disclosure title="Regioni, classi e colori" extra="regioni semplificate: per una validazione formale MolProbity">
          <div className="stack-sm">
            <p>
              Regioni semplificate calibrate sulle aree favorite di MolProbity (Lovell et al. 2003), distinte per glicina, prolina e
              caso generale (la pre-prolina si valuta con le regioni del caso generale). Per una validazione formale usa MolProbity.
            </p>
            <p>
              Colori standard di PROCHECK: regioni favorite in rosso, consentite in giallo, non consentite in bianco; le glicine sono
              triangoli, gli outlier hanno l'etichetta rossa. La vista «Tutti» mostra tutti i residui sulle regioni del caso generale.
            </p>
            {rs.cis_non_proline > 0 && <p>Trovati {rs.cis_non_proline} legami peptidici cis non prolinici: rari nelle proteine, vanno controllati.</p>}
          </div>
        </Disclosure>
      </div>
    </div>
  );
}
