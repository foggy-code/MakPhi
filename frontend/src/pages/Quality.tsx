import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { DatabaseStatus, Job, JobSummary, QmeanResult, QualityReport, ResidueRow, Settings, StructureSource } from "../api/types";
import { RamachandranPlot, ResidueProfile, StackBar } from "../components/charts";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { StructurePicker } from "../components/Pickers";
import { Viewer3D } from "../components/Viewer3D";
import { Alert, Badge, Card, DropZone, Empty, Grade, PageHead, Stat, Tabs } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { qmeanRoute } from "../lib/status";

type Tab = "rama" | "profiles" | "clashes" | "residues" | "3d";

function structureUrl(source: StructureSource | undefined): string | null {
  if (!source) return null;
  if (source.type === "template" && source.id) return api.templateFileUrl(source.id);
  if (source.type === "job" && source.job_id && source.file) return api.jobFileUrl(source.job_id, source.file);
  if (source.type === "upload" && source.file) return api.uploadFileUrl(source.file);
  return null;
}

function QmeanPanel({ source }: { source: StructureSource }) {
  const { projectId } = useProject();
  const toast = useToast();
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: settings } = useAsync<Settings>(() => api.settings(), []);
  const { data: dbs } = useAsync<DatabaseStatus>(() => api.databases(), []);
  const { job, summary, cancel } = useJob<QmeanResult>(jobId);
  const [fallback, setFallback] = useState<boolean | null>(null);
  if (!settings || !dbs) return null;
  const useFallback = fallback ?? settings.qmean_fallback_remote;
  const route = qmeanRoute({ ...settings, qmean_fallback_remote: useFallback }, dbs.qmean);
  const r = job?.status === "completed" ? job.result : null;
  const fallbackToggle = route.canFallback && settings.qmean_mode === "local" && (
    <label className="check tiny">
      <input type="checkbox" checked={useFallback} onChange={(e) => setFallback(e.target.checked)} />
      Se QMEAN locale non è disponibile o non riesce, usa SWISS-MODEL (il modello lascia il computer)
    </label>
  );
  if (!route.source && !jobId) {
    return (
      <div className="stack-sm" style={{ maxWidth: 360 }}>
        <p className="small muted">{route.hint}</p>
        {fallbackToggle}
      </div>
    );
  }
  const run = () =>
    api.qmean({ project_id: projectId ?? undefined, source, fallback: settings.qmean_mode === "local" ? useFallback : undefined })
      .then((j) => setJobId(j.id)).catch(toast.error);
  return (
    <div className="stack-sm" style={{ maxWidth: 420 }}>
      {!jobId && (
        <>
          <button className="btn btn-sm" onClick={run}>
            <Icon name={route.source === "local" ? "play" : "external"} size={13} /> {route.label}
          </button>
          <span className="tiny muted">{route.hint}</span>
          {fallbackToggle}
        </>
      )}
      {jobId && !r && <JobPanel summary={summary} onCancel={cancel} compact />}
      {r?.fallback && <Alert tone="warn">QMEAN locale non riuscito ({r.fallback.reason}): calcolato su SWISS-MODEL.</Alert>}
      {r && (
        <div className="grid grid-3">
          <Stat label="QMEAN4 Z-score" value={fmtNum(r.qmean4_z, 2)} note="≥ −4: qualità paragonabile a strutture sperimentali" />
          <Stat label="QMEAN4" value={fmtNum(r.qmean4, 3)} note={r.source === "local" ? `locale · ${r.method ?? "QMEAN"}` : "SWISS-MODEL"} />
          <Stat label={r.method === "QMEAN" ? "Media locale" : "QMEANDisCo"} value={fmtNum(r.qmeandisco, 3)} note={r.url ? <a href={r.url} target="_blank" rel="noreferrer">rapporto completo</a> : r.uniclust ? `${r.uniclust}${r.uniclust_version ? ` (${r.uniclust_version})` : ""}` : undefined} />
        </div>
      )}
    </div>
  );
}

function ExternalValidation({ report }: { report: QualityReport }) {
  const pc = report.procheck;
  const pr = report.prosa;
  if (!pc && !pr) return null;
  return (
    <div className="grid grid-2">
      {pc && (
        <Card title="PROCHECK" sub="Laskowski et al. 1993">
          <div className="stack-sm">
            <StackBar parts={[
              { label: "Core", value: pc.ramachandran.core ?? 0, color: "var(--ok)" },
              { label: "Consentite", value: pc.ramachandran.allowed ?? 0, color: "var(--accent)" },
              { label: "Generose", value: pc.ramachandran.generous ?? 0, color: "var(--warn)" },
              { label: "Non consentite", value: pc.ramachandran.disallowed ?? 0, color: "var(--err)" },
            ]} />
            <div className="grid grid-3">
              <Stat label="G-factor globale" value={fmtNum(pc.g_factors.overall, 2)} note="> −0,5 normale; < −1 insolito" />
              <Stat label="Diedri / covalenti" value={<span style={{ fontSize: 16 }}>{fmtNum(pc.g_factors.dihedrals, 2)} / {fmtNum(pc.g_factors.covalent, 2)}</span>} />
              <Stat label="Contatti ravvicinati" value={pc.bad_contacts ?? "—"} note={pc.mc_bond_angles_ok != null ? `angoli di legame nei limiti: ${fmtNum(pc.mc_bond_angles_ok)}%` : undefined} />
            </div>
            <p className="tiny muted">Una buona struttura ha oltre il 90% dei residui nelle regioni core. I grafici PostScript completi sono tra i file del job.</p>
          </div>
        </Card>
      )}
      {pr && (
        <Card title="ProSA II" sub="energia del ripiegamento (Sippl)">
          <div className="stack-sm">
            <div className="grid grid-3">
              <Stat label="Z-score" value={fmtNum(pr.zscore, 2)} note="confrontalo con le strutture sperimentali di pari lunghezza: valori positivi indicano problemi" />
              <Stat label="Z coppie" value={fmtNum(pr.zscore_pair, 2)} />
              <Stat label="Z superficie" value={fmtNum(pr.zscore_surface, 2)} />
            </div>
            <ProsaProfile profile={pr.profile} />
          </div>
        </Card>
      )}
    </div>
  );
}

/** Profilo di energia ProSA (finestra 10): i tratti sopra lo zero sono potenzialmente problematici. */
function ProsaProfile({ profile }: { profile: { index: number; energy_w10: number | null }[] }) {
  const pts = profile.filter((p) => p.energy_w10 != null) as { index: number; energy_w10: number }[];
  if (pts.length < 2) return null;
  const w = 480;
  const h = 110;
  const min = Math.min(-2, ...pts.map((p) => p.energy_w10));
  const max = Math.max(1, ...pts.map((p) => p.energy_w10));
  const x = (i: number) => ((i - pts[0].index) / Math.max(1, pts[pts.length - 1].index - pts[0].index)) * (w - 30) + 25;
  const y = (v: number) => h - 15 - ((v - min) / (max - min)) * (h - 25);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.index).toFixed(1)},${y(p.energy_w10).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto" }} role="img" aria-label="Profilo di energia ProSA">
      <line x1={25} x2={w - 5} y1={y(0)} y2={y(0)} stroke="var(--border)" strokeDasharray="4 3" />
      <text x={2} y={y(0) + 4} fontSize={10} fill="var(--muted)">0</text>
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
      {pts.filter((p) => p.energy_w10 > 0).map((p) => <circle key={p.index} cx={x(p.index)} cy={y(p.energy_w10)} r={2} fill="var(--err)" />)}
      <text x={25} y={h - 2} fontSize={10} fill="var(--muted)">residuo {pts[0].index}</text>
      <text x={w - 5} y={h - 2} fontSize={10} fill="var(--muted)" textAnchor="end">{pts[pts.length - 1].index}</text>
    </svg>
  );
}

function ResidueTable({ residues, onPick }: { residues: ResidueRow[]; onPick: (r: ResidueRow) => void }) {
  const [only, setOnly] = useState<"all" | "problems">("problems");
  const rows = residues.filter((r) => only === "all" || r.rama === "outlier" || r.clashes > 0);
  return (
    <div className="stack-sm">
      <div className="row small">
        <label className="check"><input type="radio" checked={only === "problems"} onChange={() => setOnly("problems")} /> Solo residui problematici</label>
        <label className="check"><input type="radio" checked={only === "all"} onChange={() => setOnly("all")} /> Tutti ({residues.length})</label>
      </div>
      {!rows.length ? <Alert tone="ok">Nessun outlier di Ramachandran né residuo coinvolto in clash.</Alert> : (
        <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
          <table className="table">
            <thead><tr><th>Residuo</th><th>DSSP</th><th className="num">φ</th><th className="num">ψ</th><th>Ramachandran</th><th className="num">Clash</th><th className="num">RSA</th><th className="num">DOPE</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.chain}${r.resnum}`} className="clickable" onClick={() => onPick(r)}>
                  <td className="nowrap"><b>{r.resname}</b> {r.chain}{r.resnum}</td>
                  <td>{r.ss}</td>
                  <td className="num">{fmtNum(r.phi, 0)}</td>
                  <td className="num">{fmtNum(r.psi, 0)}</td>
                  <td>{r.rama ? <Badge tone={r.rama === "favored" ? "ok" : r.rama === "allowed" ? "info" : "err"}>{r.rama === "favored" ? "favorita" : r.rama === "allowed" ? "consentita" : "outlier"}</Badge> : "—"}</td>
                  <td className="num">{r.clashes || ""}</td>
                  <td className="num">{fmtNum(r.rsa, 2)}</td>
                  <td className="num">{fmtNum(r.dope, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Report({ report, source }: { report: QualityReport; source?: StructureSource }) {
  const [tab, setTab] = useState<Tab>("rama");
  const [focus, setFocus] = useState<{ chain: string; resi: number }[]>([]);
  const g = report.grade;
  const rs = report.ramachandran.summary;
  const url = structureUrl(source ?? report.source);
  const pick = (r: { chain: string; resnum: string }) => {
    setFocus([{ chain: r.chain, resi: parseInt(r.resnum, 10) }]);
    setTab("3d");
  };
  const problems = useMemo(() => {
    const out: { chain: string; resi: number }[] = [];
    report.residues.forEach((r) => {
      if (r.rama === "outlier" || r.clashes > 0) out.push({ chain: r.chain, resi: parseInt(r.resnum, 10) });
    });
    return out;
  }, [report]);
  return (
    <div className="stack">
      <Card>
        <div className="row" style={{ gap: 18, alignItems: "center" }}>
          <Grade grade={g.grade} large />
          <div style={{ flex: 1, minWidth: 220 }}>
            <h2>{report.structure.title || report.structure.file}</h2>
            <div className="small text-2">
              {report.structure.residues} residui · {report.structure.chains} catene · {report.structure.heavy_atoms} atomi
              {report.structure.method && ` · ${report.structure.method}`}
              {report.structure.resolution && ` · ${report.structure.resolution} Å`}
            </div>
            <div className="small muted" style={{ marginTop: 4 }}>
              Punteggio {fmtNum(g.score, 1)}/100 — penalità: Ramachandran {g.penalties.ramachandran}, clash {g.penalties.clashes}, DOPE {g.penalties.dope}
            </div>
          </div>
          {source && <QmeanPanel source={source} />}
        </div>
      </Card>
      {report.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Ramachandran favorite" value={<>{fmtNum(rs.favored_pct)}<small>%</small></>} note={`${rs.outlier} outlier (${fmtNum(rs.outlier_pct)}%) · ${rs.allowed} consentite`} />
        <Stat label="Clashscore" value={fmtNum(report.clashes.clashscore, 2)} note={`${report.clashes.count} contatti gravi (atomi pesanti)`} />
        <Stat label="DOPE" value={report.dope?.score ? fmtNum(report.dope.score, 0) : "—"} note={report.dope?.zscore != null ? `zDOPE ${fmtNum(report.dope.zscore, 2)} (${report.dope.zscore < -1 ? "simile a strutture native" : "da verificare"})` : "richiede MODELLER"} />
        <Stat label="Superficie" value={report.sasa.mean_rsa != null ? <>{fmtNum(report.sasa.mean_rsa * 100, 0)}<small>% RSA media</small></> : "—"} note={report.sasa.buried_pct != null ? `${fmtNum(report.sasa.buried_pct)}% residui sepolti` : undefined} />
      </div>
      <ExternalValidation report={report} />
      <Card title="Struttura secondaria" sub={report.secondary_structure.method}>
        <StackBar parts={[
          { label: "Elica", value: report.secondary_structure.helix_pct, color: "var(--helix)" },
          { label: "Foglietto β", value: report.secondary_structure.strand_pct, color: "var(--strand)" },
          { label: "Coil", value: report.secondary_structure.coil_pct, color: "var(--coil)" },
        ]} />
      </Card>
      <Card>
        <Tabs value={tab} onChange={setTab} tabs={[
          { value: "rama", label: "Ramachandran" },
          { value: "profiles", label: "Profili per residuo" },
          { value: "clashes", label: `Contatti sterici (${report.clashes.count})` },
          { value: "residues", label: "Residui" },
          { value: "3d", label: "3D" },
        ]} />
        {tab === "rama" && (
          <div className="grid grid-2" style={{ alignItems: "start" }}>
            <RamachandranPlot report={report.ramachandran} onSelect={pick} />
            <div className="stack-sm">
              <h4>Outlier</h4>
              {report.ramachandran.points.filter((p) => p.region === "outlier").length === 0 ? <Alert tone="ok">Nessun residuo in regione non consentita.</Alert> : (
                <div className="row">
                  {report.ramachandran.points.filter((p) => p.region === "outlier").map((p) => (
                    <button key={`${p.chain}${p.resnum}`} className="chip" onClick={() => pick(p)}>{p.resname} {p.chain}{p.resnum} <span className="muted">({p.phi.toFixed(0)}, {p.psi.toFixed(0)})</span></button>
                  ))}
                </div>
              )}
              <p className="tiny muted">
                Regioni semplificate calibrate sulle aree favorite di MolProbity (Lovell et al. 2003), distinte per glicina, prolina e caso generale. Per una validazione formale usa MolProbity.
                {rs.cis_non_proline > 0 && ` Trovati ${rs.cis_non_proline} legami peptidici cis non prolinici.`}
              </p>
            </div>
          </div>
        )}
        {tab === "profiles" && (
          <div className="stack">
            <ResidueProfile residues={report.residues} metric="dope" onSelect={pick} />
            <ResidueProfile residues={report.residues} metric="rsa" onSelect={pick} height={150} />
            <ResidueProfile residues={report.residues} metric="bfactor" onSelect={pick} height={150} />
          </div>
        )}
        {tab === "clashes" && (
          !report.clashes.clashes.length ? <Alert tone="ok">Nessun contatto sterico grave (sovrapposizione &gt; 0,4 Å).</Alert> : (
            <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
              <table className="table">
                <thead><tr><th>Atomo 1</th><th>Atomo 2</th><th className="num">Distanza (Å)</th><th className="num">Sovrapposizione (Å)</th></tr></thead>
                <tbody>
                  {report.clashes.clashes.map((c, i) => (
                    <tr key={i} className="clickable" onClick={() => {
                      const [c1, r1] = c.atom1.split(" ");
                      const [c2, r2] = c.atom2.split(" ");
                      setFocus([{ chain: c1, resi: parseInt(r1, 10) }, { chain: c2, resi: parseInt(r2, 10) }]);
                      setTab("3d");
                    }}>
                      <td className="mono">{c.atom1}</td><td className="mono">{c.atom2}</td><td className="num">{c.distance.toFixed(2)}</td><td className="num">{c.overlap.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
        {tab === "residues" && <ResidueTable residues={report.residues} onPick={pick} />}
        {tab === "3d" && (
          url ? (
            <div className="stack-sm">
              <div className="row small">
                <button className="btn btn-sm" onClick={() => setFocus(problems)}>Evidenzia tutti i residui problematici ({problems.length})</button>
                <button className="btn btn-sm btn-ghost" onClick={() => setFocus([])}>Nessuna evidenza</button>
                <span className="muted">Residui evidenziati in arancione.</span>
              </div>
              <Viewer3D url={url} highlight={focus} format={url.endsWith(".cif") ? "cif" : "pdb"} />
            </div>
          ) : <p className="small muted">Struttura non disponibile per la visualizzazione 3D.</p>
        )}
      </Card>
    </div>
  );
}

function Compare() {
  const { projectId } = useProject();
  const { data: jobs } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "quality", status: "completed", limit: 12 }), [projectId]);
  const [full, setFull] = useState<Job<QualityReport>[]>([]);
  useEffect(() => {
    if (!jobs?.length) return setFull([]);
    Promise.all(jobs.map((j) => api.job<QualityReport>(j.id))).then(setFull).catch(() => undefined);
  }, [jobs]);
  if (!full.length) return null;
  return (
    <Card title="Confronto delle analisi del progetto" sub="Ultime 12 analisi completate">
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Struttura</th><th>Voto</th><th className="num">Fav. %</th><th className="num">Outlier %</th><th className="num">Clashscore</th><th className="num">DOPE</th><th className="num">zDOPE</th><th className="num">Elica %</th><th className="num">β %</th><th /></tr></thead>
          <tbody>
            {full.filter((j) => j.result).map((j) => {
              const r = j.result!;
              return (
                <tr key={j.id}>
                  <td><b>{r.label ?? r.structure.file}</b><div className="tiny muted">{relTime(j.finished_at)}</div></td>
                  <td><Grade grade={r.grade.grade} /></td>
                  <td className="num">{fmtNum(r.ramachandran.summary.favored_pct)}</td>
                  <td className="num">{fmtNum(r.ramachandran.summary.outlier_pct)}</td>
                  <td className="num">{fmtNum(r.clashes.clashscore, 2)}</td>
                  <td className="num">{fmtNum(r.dope?.score ?? null, 0)}</td>
                  <td className="num">{fmtNum(r.dope?.zscore ?? null, 2)}</td>
                  <td className="num">{fmtNum(r.secondary_structure.helix_pct)}</td>
                  <td className="num">{fmtNum(r.secondary_structure.strand_pct)}</td>
                  <td><Link className="btn btn-sm btn-ghost" to={`/quality?qjob=${j.id}`}>Apri</Link></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function Quality() {
  const { projectId } = useProject();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const qjob = params.get("qjob");
  const modelJob = params.get("job");
  const modelFile = params.get("file");
  const templateId = params.get("template");
  const [source, setSource] = useState<StructureSource | null>(null);
  const [dope, setDope] = useState(true);
  const [extended, setExtended] = useState(true);
  const [stored, setStored] = useState<QualityReport | null>(null);
  const { job, summary, log, cancel } = useJob<QualityReport>(qjob, { withLog: true });

  useEffect(() => {
    setStored(null);
    if (modelJob && modelFile) {
      const src: StructureSource = { type: "job", job_id: modelJob, file: modelFile };
      setSource(src);
      api.modelQuality(modelJob, modelFile).then((r) => setStored({ ...r, source: src })).catch(() => undefined);
    } else if (templateId) {
      setSource({ type: "template", id: templateId });
    }
  }, [modelJob, modelFile, templateId]);

  const run = async (src: StructureSource | null) => {
    if (!src) return;
    try {
      const j = await api.quality({ project_id: projectId ?? undefined, source: src, dope, procheck: extended, prosa: extended });
      setParams({ qjob: j.id });
    } catch (e) {
      toast.error(e);
    }
  };
  const upload = async (file: File) => {
    try {
      const j = await api.qualityUpload(file, projectId ?? undefined, dope, extended, extended);
      setParams({ qjob: j.id });
    } catch (e) {
      toast.error(e);
    }
  };
  const report = qjob ? (job?.status === "completed" ? job.result : null) : stored;
  const reportSource = qjob ? (job?.params?.source as StructureSource | undefined) : source ?? undefined;

  return (
    <div className="page">
      <PageHead title="Qualità strutturale" lead="Validazione di modelli e template: Ramachandran, contatti sterici, energia DOPE per residuo, struttura secondaria, accessibilità al solvente e voto complessivo A–D." />
      <div className="split">
        <div className="stack">
          <Card title="Struttura da analizzare">
            <div className="stack">
              <StructurePicker value={source} onChange={setSource} />
              <label className="check"><input type="checkbox" checked={dope} onChange={(e) => setDope(e.target.checked)} /> Calcola il profilo DOPE (MODELLER)</label>
              <label className="check"><input type="checkbox" checked={extended} onChange={(e) => setExtended(e.target.checked)} /> PROCHECK e ProSA II (se installati)</label>
              <button className="btn btn-primary" disabled={!source} onClick={() => run(source)}><Icon name="shield" size={14} /> Analizza</button>
              <hr className="sep" />
              <DropZone accept=".pdb,.ent,.cif,.mmcif" onFile={upload} label={<>Oppure carica un file <b>.pdb</b> / <b>.cif</b> da analizzare</>} />
            </div>
          </Card>
          <Card title="Come leggere il voto">
            <div className="stack-sm small text-2">
              <div className="row"><Grade grade="A" /> ≥ 85: modello affidabile per analisi strutturali.</div>
              <div className="row"><Grade grade="B" /> ≥ 70: buono; controlla le regioni segnalate.</div>
              <div className="row"><Grade grade="C" /> ≥ 55: da rifinire (loop, allineamento).</div>
              <div className="row"><Grade grade="D" /> &lt; 55: problematico; rivedi template e allineamento.</div>
              <p className="tiny muted">Punteggio = 100 − penalità Ramachandran (outlier oltre lo 0,5%) − penalità clashscore − penalità zDOPE (se &gt; −1).</p>
            </div>
          </Card>
        </div>
        <div className="stack">
          {qjob && <JobPanel summary={summary} log={log} onCancel={cancel} />}
          {!qjob && !report && (
            <Card>
              {modelJob ? (
                <Empty icon="shield" title="Analisi non ancora calcolata per questo modello">
                  <button className="btn btn-primary" onClick={() => run(source)}>Analizza ora</button>
                </Empty>
              ) : <Empty icon="shield" title="Scegli una struttura o carica un file" />}
            </Card>
          )}
          {report && <Report report={report} source={reportSource} />}
          <Compare />
        </div>
      </div>
    </div>
  );
}
