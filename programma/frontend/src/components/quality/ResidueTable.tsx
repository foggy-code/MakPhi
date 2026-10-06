import { useEffect, useMemo, useState } from "react";
import type { ConsensusResult, QualityReport, ResidueRow } from "../../api/types";
import { fmtNum, plural } from "../../lib/format";
import { alignedPerResidue, isProblemResidue } from "../../lib/qualityReport";
import { dsspLabel, residueClashes } from "../../lib/qualityView";
import { classLabel, fmtAngle, RAMA_SHORT_LABEL, RAMA_TONE } from "../../lib/ramachandran";
import { isPlddt, QMEAN_LOCAL_THRESHOLD, SIGNAL_LABEL, type ResidueRef } from "../../lib/residueColoring";
import { sameResidue } from "../../lib/residues";
import { TABLE_ROW_HEIGHT, useVirtualRows } from "../../lib/virtual";
import { Icon } from "../Icon";
import { Alert, Badge, DetailGrid, DetailPanel, rowActivate } from "../ui";

/** Ricerca per nome, catena e numero ("A45", "gly", "ASP A4"), senza badare a maiuscole e spazi. */
function matches(r: ResidueRow, query: string): boolean {
  const compact = (s: string) => s.toLowerCase().replace(/[\s:]+/g, "");
  const q = compact(query);
  return !q || compact(`${r.resname} ${r.chain}${r.resnum}`).includes(q);
}

/**
 * Tabella dei residui (a finestra oltre 200 righe, altezza fissa): filtro dei problematici e ricerca. Il clic seleziona
 * il residuo; il dettaglio va nel pannello sotto la tabella, mai in righe inserite tra le altre.
 */
export function ResidueTable({ residues, onPick, selected, consensus, plddt }: { residues: ResidueRow[]; onPick: (r: ResidueRow) => void; selected: ResidueRef | null; consensus: ConsensusResult | null; plddt: boolean }) {
  const counts = alignedPerResidue({ residues }, consensus);
  const problem = (r: ResidueRow, i: number) => isProblemResidue(r, i, counts);
  const selectedIsProblem = selected ? residues.some((r, i) => sameResidue(selected, r) && problem(r, i)) : true;
  const [only, setOnly] = useState<"all" | "problems">(selectedIsProblem ? "problems" : "all");
  const [query, setQuery] = useState("");
  const problems = useMemo(() => residues.filter((r, i) => isProblemResidue(r, i, counts)).length, [residues, counts]);
  const rows = residues.map((r, i) => ({ r, i })).filter(({ r, i }) => (only === "all" || problem(r, i)) && matches(r, query));
  // oltre VIRTUAL_THRESHOLD righe si disegnano solo quelle visibili (F8b); il residuo scelto (3D, grafici) si porta in
  // vista scorrendo solo la tabella, anche se la sua riga non è disegnata
  const virtual = useVirtualRows<HTMLDivElement>({ count: rows.length, rowHeight: TABLE_ROW_HEIGHT, measure: true });
  const rowStyle = virtual.active ? { height: TABLE_ROW_HEIGHT } : undefined;
  const selIndex = selected ? rows.findIndex(({ r }) => sameResidue(selected, r)) : -1;
  const { reveal } = virtual;
  useEffect(() => {
    if (selIndex >= 0) reveal(selIndex);
  }, [selected, only, selIndex, reveal]);
  const columns = counts ? 10 : 9;
  return (
    <div className="stack-sm">
      <div className="row small">
        <label className="check"><input type="radio" checked={only === "problems"} onChange={() => setOnly("problems")} /> Solo residui problematici ({problems})</label>
        <label className="check"><input type="radio" checked={only === "all"} onChange={() => setOnly("all")} /> Tutti ({residues.length})</label>
        <input className="input input-sm quality-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Cerca un residuo (es. A45, GLY)" aria-label="Cerca un residuo" />
        {query && <span className="text-2">{plural(rows.length, "residuo trovato", "residui trovati")}</span>}
      </div>
      {counts && <p className="small text-2">Problematici: outlier di Ramachandran, coinvolti in clash o segnalati da almeno 2 metodi del consenso.</p>}
      {!rows.length ? (
        query ? <Alert tone="info">Nessun residuo corrisponde a «{query}».</Alert>
          : <Alert tone="ok">{counts ? "Nessun outlier di Ramachandran, residuo coinvolto in clash o segnalato da almeno 2 metodi del consenso." : "Nessun outlier di Ramachandran né residuo coinvolto in clash."}</Alert>
      ) : (
        <div ref={virtual.ref} onScroll={virtual.onScroll} className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
          <table className="table">
            <thead><tr><th>Residuo</th><th>DSSP</th><th className="num">φ</th><th className="num">ψ</th><th>Ramachandran</th><th className="num">Clash</th><th className="num">RSA</th><th className="num">DOPE</th><th className="num">{plddt ? "pLDDT" : "B-factor"}</th>{counts && <th>Consenso</th>}</tr></thead>
            <tbody>
              {virtual.range.padTop > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padTop }}><td colSpan={columns} /></tr>}
              {rows.slice(virtual.range.start, virtual.range.end).map(({ r, i }, n) => {
                const sel = sameResidue(selected, r);
                return (
                  <tr key={`${r.chain}${r.resnum}`} data-vrow={virtual.range.start + n} style={rowStyle} aria-selected={sel}
                    className={`clickable${sel ? " selected" : ""}${virtual.active ? " virtual-row" : ""}`} {...rowActivate(() => onPick(r))}>
                    <td className="nowrap"><b>{r.resname}</b> {r.chain}{r.resnum}</td>
                    <td title={dsspLabel(r.ss)}>{r.ss}</td>
                    <td className="num">{fmtNum(r.phi, 0)}</td>
                    <td className="num">{fmtNum(r.psi, 0)}</td>
                    <td>{r.rama ? <Badge tone={RAMA_TONE[r.rama]}>{RAMA_SHORT_LABEL[r.rama]}</Badge> : "—"}</td>
                    <td className="num">{r.clashes || ""}</td>
                    <td className="num">{fmtNum(r.rsa, 2)}</td>
                    <td className="num">{fmtNum(r.dope, 3)}</td>
                    <td className="num">{fmtNum(r.bfactor, 1)}</td>
                    {counts && <td className="small">{counts.flags[i].map((k) => SIGNAL_LABEL[k] ?? k).join(", ")}</td>}
                  </tr>
                );
              })}
              {virtual.range.padBottom > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padBottom }}><td colSpan={columns} /></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * Scheda del residuo selezionato (dal 3D, dai grafici o dalla tabella): tutte le metriche, gli scontri elencati, le
 * segnalazioni del consenso e il disordine predetto; "Mostra nel 3D" lo evidenzia e lo centra.
 */
export function ResidueDetail({ report, selected, consensus, onShow3d, onTable, onClose }: {
  report: QualityReport;
  selected: ResidueRef;
  consensus: ConsensusResult | null;
  onShow3d?: () => void;
  onTable?: () => void;
  onClose?: () => void;
}) {
  const i = report.residues.findIndex((r) => sameResidue(selected, r));
  if (i < 0) return null;
  const r = report.residues[i];
  const per = alignedPerResidue(report, consensus);
  const flags = per?.flags[i] ?? [];
  const qmean = per?.values.qmean?.[i];
  const prosa = per?.values.prosa?.[i];
  const disorder = consensus?.disorder && consensus.disorder.flags.length === report.residues.length ? consensus.disorder : null;
  const point = report.ramachandran.points.find((p) => sameResidue(selected, p));
  const clashes = residueClashes(report.clashes.clashes, r);
  const problem = isProblemResidue(r, i, per);
  const items = [
    { label: "DSSP", value: r.ss ? `${r.ss} · ${dsspLabel(r.ss)}` : "—" },
    { label: "φ / ψ", value: r.phi === null || r.psi === null ? "—" : `${fmtAngle(r.phi, 1)}° / ${fmtAngle(r.psi, 1)}°` },
    {
      label: "Ramachandran",
      value: r.rama ? <><Badge tone={RAMA_TONE[r.rama]}>{RAMA_SHORT_LABEL[r.rama]}</Badge>{point && <span className="text-2"> {classLabel(point.class)}</span>}</> : "—",
    },
    { label: "Contatti sterici", value: r.clashes ? plural(r.clashes, "contatto grave", "contatti gravi") : "nessuno" },
    { label: "RSA", value: r.rsa === null ? "—" : `${fmtNum(r.rsa, 2)} · ${r.rsa < 0.2 ? "sepolto" : "esposto"}` },
    { label: "DOPE (media mobile)", value: fmtNum(r.dope, 3) },
    { label: isPlddt(report) ? "pLDDT" : "B-factor", value: fmtNum(r.bfactor, 1) },
    ...(qmean !== undefined ? [{ label: "QMEAN locale", value: qmean === null ? "—" : `${fmtNum(qmean, 2)}${qmean < QMEAN_LOCAL_THRESHOLD ? " · sotto 0,6" : ""}` }] : []),
    ...(prosa !== undefined ? [{ label: "ProSA (finestra 10)", value: prosa === null ? "—" : `${fmtNum(prosa, 2)}${prosa > 0 ? " · sopra lo zero" : ""}` }] : []),
    ...(per ? [{ label: "Consenso", value: flags.length ? `segnalato da ${flags.map((k) => SIGNAL_LABEL[k] ?? k).join(", ")}` : "nessuna segnalazione" }] : []),
    ...(disorder ? [{
      label: "Disordine predetto",
      value: `${disorder.flags[i] ? "sì" : "no"}${disorder.scores[i] !== null && disorder.scores[i] !== undefined ? ` · FoldIndex ${fmtNum(disorder.scores[i], 2)}` : ""}`,
    }] : []),
  ];
  return (
    <DetailPanel
      title={`${r.resname} ${r.chain}${r.resnum}`}
      label={`Dettagli di ${r.resname} ${r.chain}${r.resnum}`}
      badges={problem ? <Badge tone="err">problematico</Badge> : <Badge tone="ok">nessun problema</Badge>}
      actions={(
        <>
          {onShow3d && (
            <button type="button" className="btn btn-sm" onClick={onShow3d} title="Evidenzia e centra il residuo nella vista 3D">
              <Icon name="cube" size={13} /> Mostra nel 3D
            </button>
          )}
          {onTable && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={onTable}>
              <Icon name="list" size={13} /> Mostra nella tabella
            </button>
          )}
        </>
      )}
      onClose={onClose}
      closeLabel="Chiudi i dettagli del residuo"
    >
      <DetailGrid items={items} />
      {clashes.length > 0 && (
        <div className="small">
          <span className="text-2">Scontri elencati: </span>
          {clashes.map((c, k) => (
            <span key={k} className="nowrap">{k > 0 && " · "}{c.own} – {c.partner} {fmtNum(c.distance, 2)} Å (sovrapposizione {fmtNum(c.overlap, 2)} Å)</span>
          ))}
        </div>
      )}
    </DetailPanel>
  );
}
