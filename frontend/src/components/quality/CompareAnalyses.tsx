import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Job, JobSummary, QualityReport } from "../../api/types";
import { useProject } from "../../lib/context";
import { fmtNum, plural, relTime } from "../../lib/format";
import { useAsync } from "../../lib/hooks";
import { BAND_TONE, clashTone, fmtPenalty, outlierTone } from "../../lib/qualityView";
import { Badge, Card, DetailGrid, ExpandButton, Grade, useExpanded } from "../ui";

const COLUMNS = 12;

/** Metriche aggiuntive di un'analisi del confronto: penalità, contatti, superficie, consenso con i metodi. */
function AnalysisDetail({ report: r }: { report: QualityReport }) {
  const c = r.consensus;
  return (
    <div className="stack-sm">
      <DetailGrid items={[
        { label: "Punteggio", value: `${fmtNum(r.grade.score, 1)}/100` },
        { label: "Penalità Ramachandran", value: fmtPenalty(r.grade.penalties.ramachandran) },
        { label: "Penalità clash", value: fmtPenalty(r.grade.penalties.clashes) },
        { label: "Penalità zDOPE", value: r.dope?.zscore != null ? fmtPenalty(r.grade.penalties.dope) : "—" },
        { label: "Contatti gravi", value: `${r.clashes.count} su ${r.clashes.atoms} atomi` },
        { label: "Residui", value: `${r.structure.residues} · ${plural(r.structure.chains, "catena", "catene")}` },
        { label: "RSA media", value: r.sasa.mean_rsa != null ? `${fmtNum(r.sasa.mean_rsa * 100, 0)}%` : "—" },
        { label: "Residui sepolti", value: r.sasa.buried_pct != null ? `${fmtNum(r.sasa.buried_pct)}%` : "—" },
        { label: "PROCHECK core", value: r.procheck?.ramachandran.core != null ? `${fmtNum(r.procheck.ramachandran.core)}%` : "—" },
        { label: "ProSA z-score", value: fmtNum(r.prosa?.zscore ?? null, 2) },
        { label: "Avvisi", value: r.warnings.length || "nessuno" },
      ]} />
      {c ? (
        <div className="row small" style={{ gap: 6 }}>
          <span className="text-2">Consenso {fmtNum(c.score, 0)}/100 ({c.methods_used} metodi, senza QMEAN):</span>
          {c.methods.map((m) => <Badge key={m.key} tone={BAND_TONE[m.band]} title={m.measures}>{m.label} {fmtNum(m.normalized, 0)}</Badge>)}
        </div>
      ) : <p className="small text-2">Analisi precedente al consenso.</p>}
    </div>
  );
}

/** Ultime analisi di qualità completate del progetto, a confronto; le righe si espandono con più metriche. */
export function CompareAnalyses() {
  const { projectId } = useProject();
  const { data: jobs } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "quality", status: "completed", limit: 12 }), [projectId]);
  const [full, setFull] = useState<Job<QualityReport>[]>([]);
  const open = useExpanded<string>();
  useEffect(() => {
    if (!jobs?.length) return setFull([]);
    Promise.all(jobs.map((j) => api.job<QualityReport>(j.id))).then(setFull).catch(() => undefined);
  }, [jobs]);
  const done = full.filter((j) => j.result);
  if (!done.length) return null;
  return (
    <Card title="Confronto delle analisi del progetto" icon="compare" sub={`Ultime ${done.length} analisi completate`} collapsible storageKey="quality.compare">
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 34 }} aria-label="Dettagli" />
              <th>Struttura</th><th>Voto</th><th className="num">Consenso</th><th className="num">Fav. %</th><th className="num">Outlier %</th>
              <th className="num">Clashscore</th><th className="num">DOPE</th><th className="num">zDOPE</th><th className="num">Elica %</th><th className="num">β %</th>
              <th aria-label="Azioni" />
            </tr>
          </thead>
          <tbody>
            {done.map((j) => {
              const r = j.result!;
              const isOpen = open.has(j.id);
              return (
                <Fragment key={j.id}>
                  <tr className={`clickable${isOpen ? " row-open" : ""}`} onClick={() => open.toggle(j.id)}>
                    <td><ExpandButton open={isOpen} onClick={() => open.toggle(j.id)} label="altre metriche" /></td>
                    <td><b>{r.label ?? r.structure.file}</b><div className="tiny text-2">{relTime(j.finished_at)}</div></td>
                    <td><Grade grade={r.grade.grade} title={`Punteggio ${fmtNum(r.grade.score, 1)}/100`} /></td>
                    <td className="num" title={r.consensus ? `${r.consensus.methods_used} metodi (senza QMEAN)` : "analisi precedente al consenso"}>{fmtNum(r.consensus?.score ?? null, 0)}</td>
                    <td className="num">{fmtNum(r.ramachandran.summary.favored_pct)}</td>
                    <td className="num"><Badge tone={outlierTone(r.ramachandran.summary.outlier_pct)}>{fmtNum(r.ramachandran.summary.outlier_pct)}</Badge></td>
                    <td className="num"><Badge tone={clashTone(r.clashes.clashscore)}>{fmtNum(r.clashes.clashscore, 2)}</Badge></td>
                    <td className="num">{fmtNum(r.dope?.score ?? null, 0)}</td>
                    <td className="num">{fmtNum(r.dope?.zscore ?? null, 2)}</td>
                    <td className="num">{fmtNum(r.secondary_structure.helix_pct)}</td>
                    <td className="num">{fmtNum(r.secondary_structure.strand_pct)}</td>
                    <td><Link className="btn btn-sm btn-ghost" to={`/quality?qjob=${j.id}`} onClick={(e) => e.stopPropagation()}>Apri</Link></td>
                  </tr>
                  {isOpen && (
                    <tr className="row-detail">
                      <td colSpan={COLUMNS}><AnalysisDetail report={r} /></td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
