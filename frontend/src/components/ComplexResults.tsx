import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ComplexAssessment, ComplexResult } from "../api/types";
import { comparisonHighlight, complexColoring, missingAssessment, STATUS_TONE } from "../lib/complex";
import { fmtNum } from "../lib/format";
import type { ResidueColoring } from "../lib/residueColoring";
import { Alert, Badge, Card, Spinner } from "./ui";

export interface ComplexView {
  coloring: ResidueColoring | null;
  highlight: { chain: string; resi: number }[];
  centerKey?: number;
}

/**
 * Risultati di un modello a più catene (F5.3): catene del modello e del template, simmetria, confronto di ogni
 * interfaccia con quella del template (residui conservati, aree, DockQ) e vista 3D colorata per catena con le interfacce
 * evidenziate (`onView`, applicata dal viewer della pagina). Il confronto è quello del modello mostrato: per il migliore
 * è nel risultato, per gli altri nel loro rapporto di qualità (se analizzati).
 */
export function ComplexResults({ jobId, complex, selected, best, onView }: { jobId: string; complex: ComplexResult; selected: string; best: string; onView: (v: ComplexView | null) => void }) {
  const [other, setOther] = useState<{ file: string; assessment: ComplexAssessment | null } | null>(null);
  const [pair, setPair] = useState<number | null>(null);
  const [colorOn, setColorOn] = useState(true);
  const [center, setCenter] = useState(0);
  useEffect(() => {
    setPair(null);
    if (selected === best) return;
    let current = true;
    setOther(null);
    api.modelQuality(jobId, selected)
      .then((q) => current && setOther({ file: selected, assessment: q.complex ?? null }))
      .catch(() => current && setOther({ file: selected, assessment: null }));
    return () => {
      current = false;
    };
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
  return (
    <Card title={`Complesso · ${complex.chains.length} catene`} sub={`interfacce di ${selected}${complex.template ? ` confrontate con il template ${complex.template}` : ""}`}
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
            {assessment.comparison.length > 0 ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
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
                    {assessment.comparison.map((c, i) => (
                      <tr key={i} className={`clickable ${pair === i ? "selected" : ""}`} onClick={() => { setPair(pair === i ? null : i); setCenter((n) => n + 1); }}>
                        <td className="nowrap"><b>{c.model_chains.map((m) => m ?? "?").join("–")}</b><div className="tiny muted">{c.template_chains ? `template ${c.template_chains.join("–")}` : "assente nel template"}</div></td>
                        <td className="num">{fmtNum(c.area_model, 0)} ({fmtNum(c.area_template, 0)})</td>
                        <td className="num">{c.conserved_percent === null ? "—" : `${fmtNum(c.conserved_percent, 0)}% (${c.conserved}/${c.template_residues})`}</td>
                        <td className="num">{fmtNum(c.fnat, 2)}</td>
                        <td className="num">{fmtNum(c.irms, 2)}</td>
                        <td className="num">{fmtNum(c.lrms, 2)}</td>
                        <td className="num">{fmtNum(c.dockq, 2)}</td>
                        <td><Badge tone={STATUS_TONE[c.status]}>{c.status}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="small muted">{assessment.interfaces.pairs.length ? "Interfacce del modello senza un template da confrontare." : "Nessuna interfaccia fra le catene del modello."}</p>}
            {current ? <Alert tone={current.tone}>{current.text}</Alert>
              : assessment.comparison.length > 0 && <span className="tiny muted">Clic su una riga per vederne il testo e centrare l'interfaccia nel 3D (verde: conservata dal template, arancione: solo nel modello, rosso: persa).</span>}
            {assessment.notes.map((n, i) => <Alert key={i} tone="warn">{n}</Alert>)}
            <span className="tiny muted">{assessment.limits}</span>
          </>
        )}
      </div>
    </Card>
  );
}
