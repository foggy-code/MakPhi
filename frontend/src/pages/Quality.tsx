import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  ConsensusBand, ConsensusMethod, ConsensusRegion, ConsensusResult, DatabaseStatus, Job, JobSummary, LigandGroupCheck, ModelResult, QmeanResult, QualityReport, ResidueRow, Settings,
  StructureSource,
} from "../api/types";
import { LigandsPanel } from "../components/LigandsPanel";
import { ConsensusTracks, RamachandranPlot, ResidueProfile, ResidueStrip, StackBar } from "../components/charts";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { StructurePicker } from "../components/Pickers";
import { ReportButton } from "../components/ReportButton";
import { AnalysisJobCard, StructuralAnalysisPanel } from "../components/StructuralAnalysis";
import { Viewer3D, type Viewer3DHandle } from "../components/Viewer3D";
import { Alert, Badge, BarCell, Card, DropZone, Empty, Grade, PageHead, Stat, Tabs } from "../components/ui";
import { compareLink } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import type { AnalysisPanelMemory } from "../lib/interactions";
import type { CoordinationLine } from "../lib/ligands";
import {
  alignedPerResidue, clashHighlight, type Highlight, isProblemResidue, ligandHighlight, problemResidues, residuesHighlight, shownReportSource,
} from "../lib/qualityReport";
import {
  colorOptions, isPlddt, QMEAN_LOCAL_THRESHOLD, regionResidues, residueColoring, resiNumber, SIGNAL_LABEL, type ColorBy, type ColorOption, type ResidueRef,
} from "../lib/residueColoring";
import { qmeanRoute } from "../lib/status";
import { TABLE_ROW_HEIGHT, useVirtualRows } from "../lib/virtual";

type Tab = "rama" | "profiles" | "clashes" | "residues" | "3d" | "interactions";

function structureUrl(source: StructureSource | undefined): string | null {
  if (!source) return null;
  if (source.type === "template" && source.id) return api.templateFileUrl(source.id);
  if (source.type === "job" && source.job_id && source.file) return api.jobFileUrl(source.job_id, source.file);
  if (source.type === "upload" && source.file) return api.uploadFileUrl(source.file);
  return null;
}

function QmeanPanel({ source, onDone }: { source: StructureSource; onDone?: (jobId: string) => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: settings } = useAsync<Settings>(() => api.settings(), []);
  const { data: dbs } = useAsync<DatabaseStatus>(() => api.databases(), []);
  const { job, summary, cancel } = useJob<QmeanResult>(jobId);
  const [fallback, setFallback] = useState<boolean | null>(null);
  const done = job?.status === "completed";
  useEffect(() => {
    if (jobId && done) onDone?.(jobId); // il consenso si ricalcola includendo QMEAN
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, done]);
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

/** Confronto strutturale della struttura analizzata (B): con i template del suo job di modellazione o con un'altra. */
function CompareLinks({ source }: { source: StructureSource }) {
  const { data: job } = useAsync<Job<ModelResult> | null>(
    () => (source.type === "job" && source.job_id ? api.job<ModelResult>(source.job_id).catch(() => null) : Promise.resolve(null)),
    [source.type, source.job_id],
  );
  const templates = job?.kind === "model" ? job.result?.templates ?? [] : [];
  return (
    <div className="row" style={{ gap: 6 }}>
      {templates.map((t) => (
        <Link key={t.code} className="btn btn-sm" to={compareLink({ type: "template", id: t.id, chain: t.chains[0] }, source)} title="GDT, lDDT e deviazione per residuo rispetto al template">
          <Icon name="compare" size={13} /> Confronta con {t.code}
        </Link>
      ))}
      <Link className="btn btn-sm" to={compareLink(null, source)} title="Sovrapponi questa struttura a un riferimento a scelta">
        <Icon name="compare" size={13} /> {templates.length ? "Con un'altra struttura" : "Confronta con un'altra struttura"}
      </Link>
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

const sameResidue = (a: ResidueRef | null | undefined, b: ResidueRef) => !!a && a.chain === b.chain && a.resnum === b.resnum;

function ResidueTable({ residues, onPick, selected, consensus, plddt }: { residues: ResidueRow[]; onPick: (r: ResidueRow) => void; selected: ResidueRef | null; consensus: ConsensusResult | null; plddt: boolean }) {
  const counts = alignedPerResidue({ residues }, consensus);
  const problem = (r: ResidueRow, i: number) => isProblemResidue(r, i, counts);
  const selectedIsProblem = selected ? residues.some((r, i) => sameResidue(selected, r) && problem(r, i)) : true;
  const [only, setOnly] = useState<"all" | "problems">(selectedIsProblem ? "problems" : "all");
  const rows = residues.map((r, i) => ({ r, i })).filter(({ r, i }) => only === "all" || problem(r, i));
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
        <label className="check"><input type="radio" checked={only === "problems"} onChange={() => setOnly("problems")} /> Solo residui problematici</label>
        <label className="check"><input type="radio" checked={only === "all"} onChange={() => setOnly("all")} /> Tutti ({residues.length})</label>
        {counts && <span className="muted">Problematici: outlier, clash o segnalati da almeno 2 metodi del consenso.</span>}
      </div>
      {!rows.length ? <Alert tone="ok">{counts ? "Nessun outlier di Ramachandran, residuo coinvolto in clash o segnalato da almeno 2 metodi del consenso." : "Nessun outlier di Ramachandran né residuo coinvolto in clash."}</Alert> : (
        <div ref={virtual.ref} onScroll={virtual.onScroll} className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
          <table className="table">
            <thead><tr><th>Residuo</th><th>DSSP</th><th className="num">φ</th><th className="num">ψ</th><th>Ramachandran</th><th className="num">Clash</th><th className="num">RSA</th><th className="num">DOPE</th><th className="num">{plddt ? "pLDDT" : "B-factor"}</th>{counts && <th>Consenso</th>}</tr></thead>
            <tbody>
              {virtual.range.padTop > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padTop }}><td colSpan={columns} /></tr>}
              {rows.slice(virtual.range.start, virtual.range.end).map(({ r, i }, n) => {
                const sel = sameResidue(selected, r);
                return (
                  <tr key={`${r.chain}${r.resnum}`} data-vrow={virtual.range.start + n} style={rowStyle} className={`clickable${sel ? " selected" : ""}${virtual.active ? " virtual-row" : ""}`} onClick={() => onPick(r)}>
                    <td className="nowrap"><b>{r.resname}</b> {r.chain}{r.resnum}</td>
                    <td>{r.ss}</td>
                    <td className="num">{fmtNum(r.phi, 0)}</td>
                    <td className="num">{fmtNum(r.psi, 0)}</td>
                    <td>{r.rama ? <Badge tone={r.rama === "favored" ? "ok" : r.rama === "allowed" ? "info" : "err"}>{r.rama === "favored" ? "favorita" : r.rama === "allowed" ? "consentita" : "outlier"}</Badge> : "—"}</td>
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

/** Scheda del residuo selezionato (dal 3D, dai grafici o dalla tabella). */
function ResidueDetail({ report, selected, consensus, onTable }: { report: QualityReport; selected: ResidueRef; consensus: ConsensusResult | null; onTable: () => void }) {
  const i = report.residues.findIndex((r) => sameResidue(selected, r));
  if (i < 0) return null;
  const r = report.residues[i];
  const per = consensus && consensus.per_residue.counts.length === report.residues.length ? consensus.per_residue : null;
  const qmean = per?.values.qmean?.[i];
  const flags = per?.flags[i] ?? [];
  return (
    <div className="row small" style={{ gap: 14 }}>
      <b>{r.resname} {r.chain}{r.resnum}</b>
      <span>DSSP {r.ss}</span>
      <span>φ/ψ {fmtNum(r.phi, 0)}/{fmtNum(r.psi, 0)} · {r.rama ? { favored: "favorita", allowed: "consentita", outlier: "outlier" }[r.rama] : "—"}</span>
      <span>clash {r.clashes}</span>
      <span>RSA {fmtNum(r.rsa, 2)}</span>
      <span>DOPE {fmtNum(r.dope, 3)}</span>
      <span>{isPlddt(report) ? "pLDDT" : "B-factor"} {fmtNum(r.bfactor, 1)}</span>
      {qmean != null && <span>QMEAN {fmtNum(qmean, 2)}</span>}
      {per && <span>{flags.length ? <>segnalato da <b>{flags.map((k) => SIGNAL_LABEL[k] ?? k).join(", ")}</b></> : "nessuna segnalazione"}</span>}
      <button className="btn btn-sm btn-ghost" onClick={onTable}>Mostra nella tabella</button>
    </div>
  );
}

const BAND_TONE: Record<ConsensusBand, "ok" | "warn" | "err"> = { buona: "ok", intermedia: "warn", scarsa: "err" };

function fmtRaw(m: ConsensusMethod): string {
  if (m.key === "grade") return `${fmtNum(m.raw, 1)}/100`;
  if (m.key === "procheck") return `${fmtNum(m.raw, 1)}%`;
  return fmtNum(m.raw, 2);
}

/** Consenso tra i metodi: punteggio, valori normalizzati, discordanze, regioni e segnalazioni per residuo. */
function ConsensusPanel({ report, consensus, error, selected, onSelect, onRegion }: {
  report: QualityReport;
  consensus: ConsensusResult | null;
  error: string | null;
  selected: ResidueRef | null;
  onSelect: (r: ResidueRow) => void;
  onRegion: (region: ConsensusRegion) => void;
}) {
  if (!consensus) {
    return error ? <Card title="Consenso tra i metodi"><Alert tone="warn">{error}</Alert></Card> : null;
  }
  const c = consensus;
  return (
    <Card title="Consenso tra i metodi" sub="scale e pesi dichiarati in domain/consensus.py" right={c.band && <Badge tone={BAND_TONE[c.band]}>fascia {c.band}</Badge>}>
      <div className="stack">
        {error && <Alert tone="warn">{error}</Alert>}
        <div className="row" style={{ gap: 18, alignItems: "center" }}>
          <Stat label="Punteggio di consenso" value={<>{fmtNum(c.score, 0)}<small>/100</small></>} note={`${c.methods_used} metodi su ${c.methods_total} · buona ≥ 70, scarsa < 40`} />
          <div className="small text-2" style={{ flex: 1, minWidth: 240 }}>
            Ogni metodo è portato su 0–100 con una scala lineare a tratti costruita sulle soglie della letteratura (70 = soglia di affidabilità, 40 = soglia dei problemi); il consenso è la media pesata dei soli metodi disponibili. Il voto A–D resta quello di BioLoom.
            {c.missing.length > 0 && <div className="muted" style={{ marginTop: 4 }}>Non disponibili: {c.missing.map((m) => `${m.label} (${m.reason})`).join("; ")}.</div>}
          </div>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Metodo</th><th className="num">Valore</th><th>Normalizzato</th><th className="num">Peso</th><th>Fascia</th></tr></thead>
            <tbody>
              {c.methods.map((m) => (
                <tr key={m.key}>
                  <td><b>{m.label}</b><div className="tiny muted">{m.measures}</div></td>
                  <td className="num nowrap">{fmtRaw(m)}</td>
                  <td><BarCell value={m.normalized} label={fmtNum(m.normalized, 0)} /></td>
                  <td className="num">{fmtNum(m.weight, 2)}</td>
                  <td><Badge tone={BAND_TONE[m.band]}>{m.band}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <details className="small text-2">
          <summary>Scale e fonti</summary>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {c.methods.map((m) => (
              <li key={m.key}>
                <b>{m.label}</b>: {m.points.map(([raw, score]) => `${fmtNum(raw, m.key === "grade" || m.key === "procheck" ? 0 : 1)} → ${score}`).join(" · ")} (lineare tra i punti). <span className="muted">{m.source}.</span>
              </li>
            ))}
          </ul>
        </details>
        {c.discordances.length > 0 ? (
          <div className="stack-sm">
            <h4>Discordanze</h4>
            {c.discordances.map((d, k) => <Alert key={k} tone="warn">{d.text}</Alert>)}
          </div>
        ) : c.methods_used > 1 && <Alert tone="ok">I metodi disponibili concordano: nessuno è in fascia buona mentre un altro è in fascia scarsa.</Alert>}
        <div className="stack-sm">
          <h4>Regioni problematiche</h4>
          {c.regions.length ? (
            <div className="row">
              {c.regions.map((r) => (
                <button key={`${r.chain}${r.start}`} className="chip" onClick={() => onRegion(r)} title="Evidenzia nel 3D">{r.text}</button>
              ))}
            </div>
          ) : <p className="small muted">Nessun tratto di almeno 3 residui segnalato da almeno 2 metodi.</p>}
          {/* disordine predetto dalla sequenza (F5.5): spiega le regioni, non entra nel punteggio */}
          {!!c.disorder?.regions.length && <Alert tone="info">{c.disorder.text}</Alert>}
          <ConsensusTracks residues={report.residues} consensus={c} selected={selected} onSelect={onSelect} onRegion={onRegion} />
        </div>
        {c.warnings.map((w, k) => <p key={k} className="tiny muted">{w}</p>)}
      </div>
    </Card>
  );
}

function ColorBySelect({ options, value, onChange }: { options: ColorOption[]; value: ColorBy; onChange: (v: ColorBy) => void }) {
  return (
    <label className="check">
      Colora per
      <select className="input" style={{ width: 220, minHeight: 28, padding: "2px 6px" }} value={value} onChange={(e) => onChange(e.target.value as ColorBy)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={!o.available}>{o.label}{!o.available && o.note ? ` (${o.note})` : ""}</option>
        ))}
      </select>
    </label>
  );
}

/** Rapporto da mostrare e dove chiederne il consenso (job di qualità, oppure modello di un job). */
interface ConsensusRef {
  job: string;
  model?: string | null;
}

function Report({ report, source, consensusRef }: { report: QualityReport; source?: StructureSource; consensusRef?: ConsensusRef | null }) {
  const [tab, setTab] = useState<Tab>("rama");
  const [focus, setFocus] = useState<{ chain: string; resi: number }[]>([]);
  const [selected, setSelected] = useState<ResidueRef | null>(null);
  const [centerKey, setCenterKey] = useState(0);
  const [colorBy, setColorBy] = useState<ColorBy>("ss");
  const [qmeanJob, setQmeanJob] = useState<string | null>(null);
  const [consensus, setConsensus] = useState<ConsensusResult | null>(report.consensus ?? null);
  const [consensusError, setConsensusError] = useState<string | null>(null);
  // ligando scelto nella sezione "Ligandi e metalli": evidenziato nel 3D con le linee di coordinazione
  const [ligand, setLigand] = useState<LigandGroupCheck | null>(null);
  const [lines, setLines] = useState<CoordinationLine[]>([]);
  // scheda Interazioni: job seguito e scelte dell'allineamento, conservati quando si cambia scheda
  const [analysisMemory, setAnalysisMemory] = useState<AnalysisPanelMemory | null>(null);
  // vista 3D per il report: c'è solo con la scheda 3D aperta
  const viewer = useRef<Viewer3DHandle>(null);
  const g = report.grade;
  const rs = report.ramachandran.summary;
  const url = structureUrl(source ?? report.source);
  const plddt = isPlddt(report);

  // Il consenso della rotta aggiunge GA341 di MODELLER, QMEAN calcolato qui e vale anche per i rapporti precedenti.
  useEffect(() => {
    if (!consensusRef) return;
    let stop = false;
    api.consensus(consensusRef.job, consensusRef.model, qmeanJob)
      .then((c) => {
        if (stop) return;
        setConsensus(c);
        setConsensusError(null);
      })
      .catch((e) => !stop && setConsensusError(`Consenso non aggiornato: ${e instanceof Error ? e.message : String(e)}`));
    return () => {
      stop = true;
    };
  }, [consensusRef?.job, consensusRef?.model, qmeanJob]);

  const options = useMemo(() => colorOptions(report, consensus), [report, consensus]);
  // un'opzione non (ancora) disponibile, es. QMEAN in attesa del consenso, ripiega sulla struttura secondaria
  const shownBy: ColorBy = options.find((o) => o.value === colorBy)?.available ? colorBy : "ss";
  const coloring = useMemo(() => residueColoring(report, shownBy, consensus), [report, shownBy, consensus]);
  const onQmean = (jobId: string) => {
    setQmeanJob(jobId);
    setColorBy("qmean");
  };

  /** Evidenza nel 3D (lib/qualityReport: solo la scelta di un ligando tiene linee e zoom sul sito), centrata. */
  const show = (h: Highlight, center = true) => {
    setFocus(h.focus);
    setSelected(h.selected);
    setLigand(h.ligand);
    setLines(h.lines);
    if (center) setCenterKey((k) => k + 1);
  };
  /** Dal grafico o dalla tabella: evidenzia e centra nel 3D. */
  const pick = (r: ResidueRef) => {
    show(residuesHighlight([{ chain: r.chain, resi: resiNumber(r.resnum) }], { chain: r.chain, resnum: r.resnum }));
    setTab("3d");
  };
  /** Dal 3D: seleziona il residuo (tabella, grafici, scheda) senza spostare la vista. */
  const pick3d = (r: ResidueRef) => {
    setSelected(r);
    setFocus([{ chain: r.chain, resi: resiNumber(r.resnum) }]);
  };
  const pickLigand = (g: LigandGroupCheck) => {
    show(ligandHighlight(g));
    setTab("3d");
  };
  const pickRegion = (region: ConsensusRegion) => {
    const res = regionResidues(report, region);
    show(residuesHighlight(res.map((r) => ({ chain: r.chain, resi: resiNumber(r.resnum) })), res[0] ?? null));
    setColorBy("consensus");
    setTab("3d");
  };
  // stessa definizione del filtro "Solo residui problematici" della tabella
  const problems = useMemo(() => problemResidues(report, consensus).map((r) => ({ chain: r.chain, resi: resiNumber(r.resnum) })), [report, consensus]);
  const per = alignedPerResidue(report, consensus);
  const strip = coloring && (
    <div className="stack-sm">
      <div className="row small">
        <ColorBySelect options={options} value={shownBy} onChange={setColorBy} />
        <span className="muted">Striscia per residuo: {coloring.title}. Clic su un residuo per evidenziarlo nel 3D.</span>
      </div>
      <ResidueStrip residues={report.residues} coloring={coloring} selected={selected} onSelect={pick} />
    </div>
  );
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
              {plddt && ` · pLDDT medio ${fmtNum(report.bfactor.mean, 1)}`}
            </div>
            <div className="small muted" style={{ marginTop: 4 }}>
              Punteggio {fmtNum(g.score, 1)}/100 — penalità: Ramachandran {g.penalties.ramachandran}, clash {g.penalties.clashes}, DOPE {g.penalties.dope}
              {consensus?.score != null && <> · consenso {fmtNum(consensus.score, 0)}/100 ({consensus.band}, {consensus.methods_used} metodi)</>}
            </div>
            {plddt && <div style={{ marginTop: 6 }}><Badge tone="info" title="Riconosciuto come nella libreria dei template: intestazione di AlphaFold DB (TITLE «ALPHAFOLD … PREDICTION» senza EXPDTA, oppure ModelCIF senza _exptl.method) o nome AF-<accessione>-F<n> senza un altro metodo dichiarato, e valori tra 0 e 100">Modello AlphaFold: la colonna B-factor contiene il pLDDT</Badge></div>}
            {source && <div style={{ marginTop: 8 }}><CompareLinks source={source} /></div>}
            {consensusRef && (
              <div className="row tiny muted" style={{ marginTop: 8, gap: 8 }}>
                <ReportButton jobId={consensusRef.job} model={consensusRef.model} qmeanJob={qmeanJob} viewer={viewer} />
                <span>{tab === "3d" ? "Include la vista 3D corrente" : "Apri la scheda 3D per includere la vista nel report"}{qmeanJob ? " e il QMEAN calcolato qui" : ""}.</span>
              </div>
            )}
          </div>
          {source && <QmeanPanel source={source} onDone={onQmean} />}
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
      <ConsensusPanel report={report} consensus={consensus} error={consensusError} selected={selected} onSelect={pick} onRegion={pickRegion} />
      {report.ligands && <LigandsPanel report={report.ligands} selected={ligand} onSelect={pickLigand} />}
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
          { value: "interactions", label: "Interazioni" },
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
            {strip}
            <ResidueProfile residues={report.residues} metric="dope" onSelect={pick} selected={selected}
              threshold={per?.dope_threshold != null ? { value: per.dope_threshold, label: "picchi segnalati (media + 1 DS)" } : null} />
            {per?.values.qmean && (
              <ResidueProfile residues={report.residues} values={per.values.qmean} label="QMEAN locale per residuo" hint="Da 0 a 1; sotto 0,6 qualità bassa attesa (SWISS-MODEL)." onSelect={pick} selected={selected} height={150}
                threshold={{ value: QMEAN_LOCAL_THRESHOLD, label: "0,6" }} />
            )}
            {per?.values.prosa && (
              <ResidueProfile residues={report.residues} values={per.values.prosa} label="Energia ProSA (finestra 10)" hint="Valori positivi: parti potenzialmente problematiche (Wiederstein & Sippl 2007)." onSelect={pick} selected={selected} height={150}
                threshold={{ value: 0, label: "0" }} />
            )}
            <ResidueProfile residues={report.residues} metric="rsa" onSelect={pick} selected={selected} height={150} />
            <ResidueProfile residues={report.residues} metric={plddt ? "plddt" : "bfactor"} onSelect={pick} selected={selected} height={150} />
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
                      show(clashHighlight(c));
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
        {tab === "residues" && <ResidueTable residues={report.residues} onPick={pick} selected={selected} consensus={consensus} plddt={plddt} />}
        {tab === "3d" && (
          url ? (
            <div className="stack-sm">
              <div className="row small">
                <ColorBySelect options={options} value={shownBy} onChange={setColorBy} />
                <button className="btn btn-sm" onClick={() => show(residuesHighlight(problems, selected))}>Evidenzia tutti i residui problematici ({problems.length})</button>
                <button className="btn btn-sm btn-ghost" onClick={() => show(residuesHighlight([]), false)}>Nessuna evidenza</button>
                <span className="muted">Evidenziati a bastoncini arancioni · clic su un residuo per selezionarlo.{lines.length > 0 && ligand && ` Linee tratteggiate: coordinazione di ${ligand.resname} ${ligand.chain}${ligand.resnum}.`}</span>
              </div>
              <Viewer3D url={url} highlight={focus} format={url.endsWith(".cif") ? "cif" : "pdb"} coloring={coloring} onPickResidue={pick3d} centerKey={centerKey} handle={viewer} lines={lines} centerZoom={!!ligand} />
              {coloring && <ResidueStrip residues={report.residues} coloring={coloring} selected={selected} onSelect={pick} />}
              {selected && <ResidueDetail report={report} selected={selected} consensus={consensus} onTable={() => setTab("residues")} />}
            </div>
          ) : <p className="small muted">Struttura non disponibile per la visualizzazione 3D.</p>
        )}
        {/* analisi strutturale (F7): interazioni, interfacce, conservazione della stessa struttura */}
        {tab === "interactions" && (
          (source ?? report.source) ? <StructuralAnalysisPanel source={(source ?? report.source)!} url={url} memory={analysisMemory} onMemory={setAnalysisMemory} />
            : <p className="small muted">Struttura del rapporto non disponibile: rilancia l'analisi di qualità.</p>
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
          <thead><tr><th>Struttura</th><th>Voto</th><th className="num">Consenso</th><th className="num">Fav. %</th><th className="num">Outlier %</th><th className="num">Clashscore</th><th className="num">DOPE</th><th className="num">zDOPE</th><th className="num">Elica %</th><th className="num">β %</th><th /></tr></thead>
          <tbody>
            {full.filter((j) => j.result).map((j) => {
              const r = j.result!;
              return (
                <tr key={j.id}>
                  <td><b>{r.label ?? r.structure.file}</b><div className="tiny muted">{relTime(j.finished_at)}</div></td>
                  <td><Grade grade={r.grade.grade} /></td>
                  <td className="num" title={r.consensus ? `${r.consensus.methods_used} metodi (senza QMEAN)` : "analisi precedente al consenso"}>{fmtNum(r.consensus?.score ?? null, 0)}</td>
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
  const ajob = params.get("ajob"); // analisi strutturale da un link (F7)
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
  // la struttura del rapporto mostrato, non quella scelta nel selettore (vale solo per la prossima analisi)
  const reportSource = shownReportSource(qjob, job?.params?.source, stored);
  // dove chiedere il consenso ricalcolato (QMEAN, GA341): il job di qualità oppure il modello del job
  const consensusRef = useMemo(() => (qjob ? { job: qjob } : modelJob && modelFile ? { job: modelJob, model: modelFile } : null), [qjob, modelJob, modelFile]);

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
              <p className="tiny muted">Il consenso (0–100) combina voto, z-DOPE, GA341, QMEAN, ProSA e PROCHECK, quando disponibili, e indica i metodi che discordano e le regioni segnalate da più metodi.</p>
            </div>
          </Card>
        </div>
        <div className="stack">
          {qjob && <JobPanel summary={summary} log={log} onCancel={cancel} />}
          {ajob && !qjob && !report && <AnalysisJobCard jobId={ajob} />}
          {!qjob && !report && !ajob && (
            <Card>
              {modelJob ? (
                <Empty icon="shield" title="Analisi non ancora calcolata per questo modello">
                  <button className="btn btn-primary" onClick={() => run(source)}>Analizza ora</button>
                </Empty>
              ) : <Empty icon="shield" title="Scegli una struttura o carica un file" />}
            </Card>
          )}
          {report && <Report key={qjob ?? `${modelJob}/${modelFile}`} report={report} source={reportSource} consensusRef={consensusRef} />}
          <Compare />
        </div>
      </div>
    </div>
  );
}
