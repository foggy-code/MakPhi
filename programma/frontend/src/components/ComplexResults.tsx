import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ComplexAssessment, ComplexResult, InterfaceComparison } from "../api/types";
import { comparisonHighlight, complexColoring, missingAssessment, STATUS_TONE } from "../lib/complex";
import { fmtNum } from "../lib/format";
import type { ResidueColoring } from "../lib/residueColoring";
import { ExpandAll } from "./ToolParts";
import { Alert, Badge, Card, DetailGrid, ExpandButton, rowActivate, Spinner, useExpanded } from "./ui";

export interface ComplexView {
  coloring: ResidueColoring | null;
  highlight: { chain: string; resi: number }[];
  centerKey?: number;
}

const COLUMNS = 9;

/** Dettaglio del confronto di un'interfaccia: contatti, residui conservati e persi, catene di DockQ, testo. */
function ComparisonDetail({ c }: { c: InterfaceComparison }) {
  return (
    <div className="stack-sm">
      <span className="small">{c.text}</span>
      <DetailGrid items={[
        { label: "Area modello / template", value: `${fmtNum(c.area_model, 0)} / ${fmtNum(c.area_template, 0)} Å²${c.area_ratio != null ? ` (${fmtNum(c.area_ratio, 0)}%)` : ""}` },
        { label: "Contatti del template riprodotti", value: `${c.reproduced_contacts} su ${c.native_contacts}` },
        { label: "Residui d'interfaccia del template", value: `${c.template_residues} (${c.conserved} conservati, ${c.unaligned} non allineati)` },
        { label: "DockQ", value: c.dockq != null ? `${fmtNum(c.dockq, 3)}${c.dockq_class ? ` · ${c.dockq_class}` : ""}` : "non valutabile" },
        { label: "Recettore · ligando", value: c.receptor || c.ligand ? `${c.receptor ?? "—"} · ${c.ligand ?? "—"} (la catena più lunga sovrapposta, la più corta misurata)` : "—" },
      ]} />
      <div className="grid grid-2">
        <div className="stack-sm">
          <h4>Conservati ({c.conserved_residues.length})</h4>
          {c.conserved_residues.length ? <div className="tools-chips">{c.conserved_residues.map((r) => <span key={r} className="chip mono">{r}</span>)}</div> : <span className="small text-2">nessuno</span>}
        </div>
        <div className="stack-sm">
          <h4>Persi nel modello ({c.lost_residues.length})</h4>
          {c.lost_residues.length ? <div className="tools-chips">{c.lost_residues.map((r) => <span key={r} className="chip mono">{r}</span>)}</div> : <span className="small text-2">nessuno</span>}
        </div>
      </div>
    </div>
  );
}

/**
 * Risultati di un modello a più catene (F5.3): catene del modello e del template, simmetria, confronto di ogni
 * interfaccia con quella del template (residui conservati, aree, DockQ; righe espandibili con contatti e residui) e
 * vista 3D colorata per catena con le interfacce evidenziate (`onView`, applicata dal viewer della pagina). Il confronto
 * è quello del modello mostrato: per il migliore è nel risultato, per gli altri nel loro rapporto di qualità (se
 * analizzati).
 */
export function ComplexResults({ jobId, complex, selected, best, onView }: { jobId: string; complex: ComplexResult; selected: string; best: string; onView: (v: ComplexView | null) => void }) {
  const [other, setOther] = useState<{ file: string; assessment: ComplexAssessment | null } | null>(null);
  const [pair, setPair] = useState<number | null>(null);
  const [colorOn, setColorOn] = useState(true);
  const [center, setCenter] = useState(0);
  const open = useExpanded<number>();
  useEffect(() => {
    setPair(null);
    open.collapseAll(); // le righe aperte erano le interfacce del modello precedente
    if (selected === best) return;
    let current = true;
    setOther(null);
    api.modelQuality(jobId, selected)
      .then((q) => current && setOther({ file: selected, assessment: q.complex ?? null }))
      .catch(() => current && setOther({ file: selected, assessment: null }));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, selected, best]);
  const loadingOther = selected !== best && other?.file !== selected;
  const assessment = selected === best ? complex.assessment : other?.file === selected ? other.assessment : null;
  const view = useMemo((): ComplexView | null => {
    if (!colorOn || !assessment) return null;
    const chosen = pair === null ? null : assessment.comparison[pair] ?? null;
    return { coloring: complexColoring(assessment, pair), highlight: comparisonHighlight(assessment, chosen), centerKey: chosen ? center : undefined };
  }, [assessment, pair, colorOn, center]);
  useEffect(() => {
    onView(view);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);
  useEffect(() => () => onView(null), []); // eslint-disable-line react-hooks/exhaustive-deps

  const sym = complex.symmetry;
  const missing = missingAssessment(selected, best);
  const current = pair === null ? null : assessment?.comparison[pair] ?? null;
  const rows = assessment?.comparison ?? [];
  return (
    <Card icon="layers" title={`Complesso · ${complex.chains.length} catene`} sub={`interfacce di ${selected}${complex.template ? ` confrontate con il template ${complex.template}` : ""}`}
      right={assessment ? <label className="check small"><input type="checkbox" checked={colorOn} onChange={(e) => setColorOn(e.target.checked)} /> Catene e interfacce nel 3D</label> : undefined}>
      <div className="stack-sm">
        <div className="small text-2">
          Catene del modello: {complex.chains.map((c) => `${c.model_chain}${c.template_chain ? ` ← catena ${c.template_chain} del template` : ""} (${c.length} aa${c.same_as ? `, come ${c.same_as}` : c.name && c.name !== c.template_chain ? `, ${c.name}` : ""})`).join("; ")}.
          {" "}Residui numerati da 1 in ogni catena.
        </div>
        <div className="small text-2">
          {sym.applied
            ? <>Restrizioni di simmetria sui C-alfa fra {sym.groups.map((g) => g.join(" = ")).join(", ")} (peso {fmtNum(sym.weight, 1)}).</>
            : sym.requested ? "Simmetria richiesta ma non applicata: nessuna coppia di catene con la stessa sequenza." : "Senza restrizioni di simmetria."}
        </div>
        {loadingOther ? <Spinner /> : !assessment ? (
          <Alert tone={missing.tone}>{missing.text}</Alert>
        ) : (
          <>
            {assessment.summary && rows.length > 0 && <Alert tone={assessment.tone}>{assessment.summary}</Alert>}
            {rows.length > 0 ? (
              <>
                {rows.length > 1 && (
                  <div className="tools-bar">
                    <div className="right"><ExpandAll allOpen={open.size === rows.length} onExpand={() => open.expandAll(rows.map((_, i) => i))} onCollapse={open.collapseAll} /></div>
                  </div>
                )}
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th style={{ width: 34 }} aria-label="Dettagli" />
                        <th>Catene</th>
                        <th className="num" title="Area d'interfaccia = metà dell'area sepolta (PISA); fra parentesi quella del template">Area (template), Å²</th>
                        <th className="num" title="Residui d'interfaccia del template il cui residuo allineato è d'interfaccia anche nel modello">Conservati</th>
                        <th className="num" title="Frazione dei contatti fra residui del template (entro 5 Å) presenti nel modello">Fnat</th>
                        <th className="num" title="RMSD dello scheletro dei residui d'interfaccia dopo la sovrapposizione">iRMS</th>
                        <th className="num" title="RMSD della catena più corta dopo la sovrapposizione della più lunga">LRMS</th>
                        <th className="num" title="DockQ (Basu & Wallner 2016) con il template come riferimento">DockQ</th>
                        <th>Esito</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((c, i) => {
                        const isOpen = open.has(i);
                        return (
                          <Fragment key={i}>
                            <tr className={`clickable${pair === i ? " selected" : isOpen ? " row-open" : ""}`} aria-selected={pair === i}
                              {...rowActivate(() => { setPair(pair === i ? null : i); setCenter((n) => n + 1); })}>
                              <td><ExpandButton open={isOpen} onClick={() => open.toggle(i)} label="dettagli dell'interfaccia" /></td>
                              <td className="nowrap"><b>{c.model_chains.map((m) => m ?? "?").join("–")}</b><div className="tiny text-2">{c.template_chains ? `template ${c.template_chains.join("–")}` : "assente nel template"}</div></td>
                              <td className="num">{fmtNum(c.area_model, 0)} ({fmtNum(c.area_template, 0)})</td>
                              <td className="num">{c.conserved_percent === null ? "—" : `${fmtNum(c.conserved_percent, 0)}% (${c.conserved}/${c.template_residues})`}</td>
                              <td className="num">{fmtNum(c.fnat, 2)}</td>
                              <td className="num">{fmtNum(c.irms, 2)}</td>
                              <td className="num">{fmtNum(c.lrms, 2)}</td>
                              <td className="num">{fmtNum(c.dockq, 2)}</td>
                              <td><Badge tone={STATUS_TONE[c.status]}>{c.status}</Badge></td>
                            </tr>
                            {isOpen && (
                              <tr className="row-detail">
                                <td colSpan={COLUMNS}><ComparisonDetail c={c} /></td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            ) : <p className="small text-2">{assessment.interfaces.pairs.length ? "Interfacce del modello senza un template da confrontare." : "Nessuna interfaccia fra le catene del modello."}</p>}
            {current ? <Alert tone={current.tone}>{current.text}</Alert>
              : rows.length > 0 && <span className="small text-2">Clic su una riga per vederne il testo e centrare l'interfaccia nel 3D (verde: conservata dal template, arancione: solo nel modello, rosso: persa); la freccia apre contatti e residui.</span>}
            {assessment.notes.map((n, i) => <Alert key={i} tone="warn">{n}</Alert>)}
            <span className="small text-2">{assessment.limits}</span>
          </>
        )}
      </div>
    </Card>
  );
}
