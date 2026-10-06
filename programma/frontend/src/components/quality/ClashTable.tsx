import { Fragment, useMemo } from "react";
import type { QualityReport, ResidueRow } from "../../api/types";
import { fmtNum } from "../../lib/format";
import { dsspLabel, parseAtom, type AtomRef, type Clash } from "../../lib/qualityView";
import { RAMA_SHORT_LABEL } from "../../lib/ramachandran";
import { Icon } from "../Icon";
import { Alert, BarCell, DetailGrid, ExpandAll, ExpandButton, useExpanded } from "../ui";

const COLUMNS = 6;

/** Un atomo dello scontro con le metriche del suo residuo. */
function AtomDetail({ title, label, residue }: { title: string; label: string; residue: ResidueRow | undefined }) {
  const atom = parseAtom(label);
  return (
    <div className="stack-sm">
      <h4>{title}</h4>
      <DetailGrid items={[
        { label: "Atomo", value: <span className="mono">{atom?.atom ?? label}</span> },
        { label: "Residuo", value: atom ? `${atom.resname} ${atom.chain}${atom.resnum}` : "—" },
        { label: "DSSP", value: residue?.ss ? `${residue.ss} · ${dsspLabel(residue.ss)}` : "—" },
        { label: "Ramachandran", value: residue?.rama ? RAMA_SHORT_LABEL[residue.rama] : "—" },
        { label: "RSA", value: residue?.rsa === null || residue?.rsa === undefined ? "—" : `${fmtNum(residue.rsa, 2)} · ${residue.rsa < 0.2 ? "sepolto" : "esposto"}` },
        { label: "Contatti del residuo", value: residue ? residue.clashes : "—" },
      ]} />
    </div>
  );
}

/**
 * Contatti sterici gravi (al massimo 100, per sovrapposizione decrescente): righe espandibili con i due atomi e i loro
 * residui; "Mostra nel 3D" evidenzia la coppia.
 */
export function ClashTable({ report, onShow }: { report: QualityReport; onShow: (c: Clash) => void }) {
  const open = useExpanded<number>();
  const list = report.clashes.clashes;
  const byResidue = useMemo(() => new Map(report.residues.map((r) => [`${r.chain}:${r.resnum}`, r])), [report.residues]);
  const maxOverlap = useMemo(() => list.reduce((m, c) => Math.max(m, c.overlap), 0), [list]);
  if (!list.length) return <Alert tone="ok">Nessun contatto sterico grave (sovrapposizione &gt; 0,4 Å).</Alert>;
  const residueOf = (a: AtomRef | null) => (a ? byResidue.get(`${a.chain}:${a.resnum}`) : undefined);
  const allOpen = open.size === list.length;
  return (
    <div className="stack-sm">
      <div className="row small">
        <span className="text-2">
          {report.clashes.count} contatti gravi su {report.clashes.atoms} atomi pesanti · clashscore {fmtNum(report.clashes.clashscore, 2)}
          {report.clashes.count > list.length && ` · mostrati i ${list.length} con la sovrapposizione maggiore`}
        </span>
        <ExpandAll masculine noun="contatti" className="right" allOpen={allOpen} onExpand={() => open.expandAll(list.map((_, i) => i))} onCollapse={open.collapseAll} />
      </div>
      <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 34 }} aria-label="Dettagli" />
              <th>Atomo 1</th><th>Atomo 2</th><th className="num">Distanza (Å)</th><th>Sovrapposizione (Å)</th><th aria-label="Azioni" />
            </tr>
          </thead>
          <tbody>
            {list.map((c, i) => {
              const isOpen = open.has(i);
              const a = parseAtom(c.atom1);
              const b = parseAtom(c.atom2);
              return (
                <Fragment key={i}>
                  <tr className={`clickable${isOpen ? " row-open" : ""}`} onClick={() => open.toggle(i)}>
                    <td><ExpandButton open={isOpen} onClick={() => open.toggle(i)} label="dettagli dello scontro" /></td>
                    <td className="mono">{c.atom1}</td>
                    <td className="mono">{c.atom2}</td>
                    <td className="num">{fmtNum(c.distance, 2)}</td>
                    <td><BarCell value={c.overlap} max={maxOverlap || 1} tone="var(--err)" label={fmtNum(c.overlap, 2)} /></td>
                    <td>
                      <button type="button" className="btn btn-sm btn-ghost" title="Evidenzia i due residui nella vista 3D"
                        onClick={(e) => {
                          e.stopPropagation();
                          onShow(c);
                        }}>
                        <Icon name="cube" size={13} /> 3D
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="row-detail">
                      <td colSpan={COLUMNS}>
                        <div className="stack-sm">
                          <DetailGrid items={[
                            { label: "Distanza", value: `${fmtNum(c.distance, 2)} Å` },
                            { label: "Sovrapposizione", value: `${fmtNum(c.overlap, 2)} Å (soglia 0,4 Å; 0,8 Å tra N e O)` },
                            { label: "Somma dei raggi di van der Waals", value: `${fmtNum(c.distance + c.overlap, 2)} Å` },
                            { label: "Stessa catena", value: a && b ? (a.chain === b.chain ? "sì" : "no, contatto tra catene") : "—" },
                          ]} />
                          <div className="grid grid-2">
                            <AtomDetail title="Atomo 1" label={c.atom1} residue={residueOf(a)} />
                            <AtomDetail title="Atomo 2" label={c.atom2} residue={residueOf(b)} />
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="small text-2">
        Sovrapposizione = somma dei raggi di van der Waals − distanza, sugli atomi pesanti; esclusi gli atomi a 3 legami o meno e le
        acque, con la tolleranza di un legame idrogeno per le coppie N/O. Senza idrogeni il clashscore è confrontabile con MolProbity
        solo come ordine di grandezza.
      </p>
    </div>
  );
}
