import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  ConsensusRegion, ConsensusResult, DatabaseStatus, Job, LigandGroupCheck, ModelResult, QmeanResult, QualityReport, Settings, StructureSource,
} from "../api/types";
import { LigandsPanel } from "../components/LigandsPanel";
import { ResidueProfile, ResidueStrip } from "../components/charts";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { StructurePicker } from "../components/Pickers";
import { ReportButton } from "../components/ReportButton";
import { AnalysisJobCard, StructuralAnalysisPanel } from "../components/StructuralAnalysis";
import { ElectrostaticsPanel, PocketsPanel, ToolJobCard } from "../components/StructureTools";
import { MutagenesisPanel } from "../components/Mutagenesis";
import { PredictionJobCard, PredictionPanel } from "../components/Predictor";
import { ClashTable } from "../components/quality/ClashTable";
import { CompareAnalyses } from "../components/quality/CompareAnalyses";
import { ConsensusPanel } from "../components/quality/ConsensusPanel";
import { RamaTab } from "../components/quality/RamaTab";
import { ExternalValidation, GradeBreakdown, SecondaryStructureCard, StructureMeta, SummaryStats } from "../components/quality/ReportSummary";
import { ResidueDetail, ResidueTable } from "../components/quality/ResidueTable";
import { Viewer3D, type Viewer3DHandle } from "../components/Viewer3D";
import { Alert, Card, DetailGrid, DropZone, Empty, Grade, PageHead, Stat, Tabs } from "../components/ui";
import { compareLink } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { fmtNum } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import type { AnalysisPanelMemory } from "../lib/interactions";
import type { CoordinationLine } from "../lib/ligands";
import {
  alignedPerResidue, clashHighlight, type Highlight, ligandHighlight, problemResidues, residuesHighlight, shownReportSource,
} from "../lib/qualityReport";
import { andList, GRADE_RULE, missingAnalyses, qmeanTone } from "../lib/qualityView";
import {
  colorOptions, isPlddt, QMEAN_LOCAL_THRESHOLD, regionResidues, residueColoring, resiNumber, type ColorBy, type ColorOption, type ResidueRef,
} from "../lib/residueColoring";
import { qmeanRoute } from "../lib/status";
import type { ToolJobMemory } from "../lib/toolJobs";
import "../styles/quality.css";

type Tab = "rama" | "profiles" | "clashes" | "residues" | "3d" | "interactions" | "pockets" | "electrostatics"
  | "mutations"; // mutagenesi e scansione di alanina (F9.2)

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
  if (!settings || !dbs) return <p className="small text-2">Controllo della disponibilità di QMEAN…</p>;
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
      <div className="stack-sm">
        <p className="small text-2">{route.hint}</p>
        {fallbackToggle}
      </div>
    );
  }
  const run = () =>
    api.qmean({ project_id: projectId ?? undefined, source, fallback: settings.qmean_mode === "local" ? useFallback : undefined })
      .then((j) => setJobId(j.id)).catch(toast.error);
  const components = r ? Object.entries(r.components ?? {}) : [];
  return (
    <div className="stack-sm">
      {!jobId && (
        <>
          <div className="row">
            <button type="button" className="btn btn-sm" onClick={run}>
              <Icon name={route.source === "local" ? "play" : "external"} size={13} /> {route.label}
            </button>
            <span className="small text-2">{route.hint}</span>
          </div>
          {fallbackToggle}
        </>
      )}
      {jobId && !r && <JobPanel summary={summary} onCancel={cancel} compact />}
      {r?.fallback && <Alert tone="warn">QMEAN locale non riuscito ({r.fallback.reason}): calcolato su SWISS-MODEL.</Alert>}
      {r && (
        <div className="grid grid-3">
          <Stat label="QMEAN4 Z-score" value={fmtNum(r.qmean4_z, 2)} tone={qmeanTone(r.qmean4_z)} note="≥ −4: qualità paragonabile a strutture sperimentali"
            detail={(
              <>
                <span>Distanza, in deviazioni standard, dalle strutture sperimentali di pari dimensione: vicino a 0 accordo, −4 o meno bassa qualità.</span>
                {r.qmean6_z != null && <DetailGrid items={[{ label: "QMEAN6 Z-score", value: fmtNum(r.qmean6_z, 2) }]} />}
              </>
            )} />
          <Stat label="QMEAN4" value={fmtNum(r.qmean4, 3)} note={r.source === "local" ? `locale · ${r.method ?? "QMEAN"}` : "SWISS-MODEL"}
            detail={components.length ? (
              <>
                <span>Componenti del punteggio (potenziali statistici).</span>
                <DetailGrid items={components.map(([k, v]) => ({ label: k, value: fmtNum(v, 3) }))} />
              </>
            ) : undefined} />
          <Stat label={r.method === "QMEAN" ? "Media locale" : "QMEANDisCo"} value={fmtNum(r.qmeandisco, 3)}
            note={r.url ? <a href={r.url} target="_blank" rel="noreferrer">rapporto completo</a> : r.uniclust ? `${r.uniclust}${r.uniclust_version ? ` (${r.uniclust_version})` : ""}` : undefined}
            detail={<span>Qualità locale media da 0 a 1; per residuo sotto {fmtNum(QMEAN_LOCAL_THRESHOLD, 1)} qualità bassa attesa (profilo nella scheda «Profili per residuo»).</span>} />
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

function ColorBySelect({ options, value, onChange }: { options: ColorOption[]; value: ColorBy; onChange: (v: ColorBy) => void }) {
  return (
    <label className="check">
      Colora per
      <select className="input input-sm" style={{ width: 220 }} value={value} onChange={(e) => onChange(e.target.value as ColorBy)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={!o.available}>{o.label}{!o.available && o.note ? ` (${o.note})` : ""}</option>
        ))}
      </select>
    </label>
  );
}

/** Etichetta di una scheda con un conteggio, colorato quando non è zero. */
function TabLabel({ text, count, tone, title }: { text: string; count: number; tone: "warn" | "err"; title: string }) {
  return <>{text}<span className={`quality-tab-count${count ? ` quality-tab-count-${tone}` : ""}`} title={title}>{count}</span></>;
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
  // confronto con una predizione esterna (F9.3) completato sulla stessa struttura: il consenso lo include
  const [predictJob, setPredictJob] = useState<string | null>(null);
  const [consensus, setConsensus] = useState<ConsensusResult | null>(report.consensus ?? null);
  const [consensusError, setConsensusError] = useState<string | null>(null);
  // ligando scelto nella sezione "Ligandi e metalli": evidenziato nel 3D con le linee di coordinazione
  const [ligand, setLigand] = useState<LigandGroupCheck | null>(null);
  const [lines, setLines] = useState<CoordinationLine[]>([]);
  // scheda Interazioni: job seguito e scelte dell'allineamento, conservati quando si cambia scheda
  const [analysisMemory, setAnalysisMemory] = useState<AnalysisPanelMemory | null>(null);
  // schede Tasche ed Elettrostatica (F7b): job seguito, conservato quando si cambia scheda
  const [pocketsMemory, setPocketsMemory] = useState<ToolJobMemory | null>(null);
  const [electroMemory, setElectroMemory] = useState<ToolJobMemory | null>(null);
  const [mutationMemory, setMutationMemory] = useState<ToolJobMemory | null>(null); // scheda Mutagenesi (F9.2)
  // vista 3D per il report: c'è solo con la scheda 3D aperta
  const viewer = useRef<Viewer3DHandle>(null);
  const g = report.grade;
  const rs = report.ramachandran.summary;
  const url = structureUrl(source ?? report.source);
  const plddt = isPlddt(report);
  const reportSource = source ?? report.source;

  // Il consenso della rotta aggiunge GA341 di MODELLER, QMEAN calcolato qui e vale anche per i rapporti precedenti.
  useEffect(() => {
    if (!consensusRef) return;
    let stop = false;
    api.consensus(consensusRef.job, consensusRef.model, qmeanJob, predictJob)
      .then((c) => {
        if (stop) return;
        setConsensus(c);
        setConsensusError(null);
      })
      .catch((e) => !stop && setConsensusError(`Consenso non aggiornato: ${e instanceof Error ? e.message : String(e)}`));
    return () => {
      stop = true;
    };
  }, [consensusRef?.job, consensusRef?.model, qmeanJob, predictJob]);

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
  const residueFocus = (r: ResidueRef) => residuesHighlight([{ chain: r.chain, resi: resiNumber(r.resnum) }], { chain: r.chain, resnum: r.resnum });
  /** Fuori dalle schede (consenso, ProSA) e da "Mostra nel 3D": evidenzia, centra e apre il 3D. */
  const pick = (r: ResidueRef) => {
    show(residueFocus(r));
    setTab("3d");
  };
  /** Dai grafici e dalle tabelle delle schede: seleziona il residuo senza cambiare scheda (la vista espansa resta aperta). */
  const select = (r: ResidueRef) => show(residueFocus(r), false);
  const showSelected = () => {
    if (selected) pick(selected);
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
  // senza DOPE (casella spenta o MODELLER assente) il profilo dice quale metrica manca e come ottenerla
  const hasDope = report.residues.some((r) => r.dope !== null && r.dope !== undefined);
  const detail = (extra: { onShow3d?: () => void; onTable?: () => void }) => selected && (
    <ResidueDetail report={report} selected={selected} consensus={consensus} onClose={() => setSelected(null)} {...extra} />
  );
  return (
    <div className="stack">
      <Card className="quality-hero">
        <div className="quality-hero-main">
          <Grade grade={g.grade} large title={`Voto BioLoom ${g.grade}: punteggio ${fmtNum(g.score, 1)}/100`} />
          <div className="quality-hero-text">
            <h2>{report.structure.title || report.structure.file}</h2>
            {report.structure.title && report.structure.file !== report.structure.title && <span className="small text-2 mono">{report.structure.file}</span>}
            <StructureMeta report={report} consensus={consensus} />
          </div>
          <div className="quality-hero-score">
            <span>Punteggio</span>
            <b>{fmtNum(g.score, 1)}<small>/100</small></b>
          </div>
        </div>
        <GradeBreakdown report={report} />
        {(source || consensusRef) && (
          <div className="quality-hero-actions">
            {source && <CompareLinks source={source} />}
            {consensusRef && (
              <div className="row small" style={{ gap: 8 }}>
                <ReportButton jobId={consensusRef.job} model={consensusRef.model} qmeanJob={qmeanJob} predictJob={predictJob} viewer={viewer} />
                <span className="text-2">{tab === "3d" ? "Include la vista 3D corrente" : "Apri la scheda 3D per includere la vista nel report"}{qmeanJob ? " e il QMEAN calcolato qui" : ""}{predictJob ? " e il confronto con la predizione" : ""}.</span>
              </div>
            )}
          </div>
        )}
      </Card>
      {report.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      <SummaryStats report={report} />
      <ExternalValidation report={report} selected={selected} onSelect={pick} />
      {source && (
        <Card title="QMEAN" icon="shield" sub="qualità globale e locale rispetto a strutture sperimentali (Benkert et al. 2011)">
          <QmeanPanel source={source} onDone={onQmean} />
        </Card>
      )}
      <ConsensusPanel report={report} consensus={consensus} error={consensusError} selected={selected} onSelect={pick} onRegion={pickRegion} />
      {/* controllo di plausibilità con una predizione esterna (F9.3): il consenso lo include a confronto completato */}
      {reportSource && (
        <PredictionPanel source={reportSource} label={report.structure.title || report.label || report.structure.file}
          residues={report.structure.chains_detail?.[0]?.residues ?? null} onResult={setPredictJob} />
      )}
      {report.ligands && <LigandsPanel report={report.ligands} selected={ligand} onSelect={pickLigand} />}
      <SecondaryStructureCard report={report} />
      <Card title="Analisi dettagliate" icon="search" sub="clic su un residuo per i dettagli; «Mostra nel 3D» lo evidenzia nella struttura">
        <Tabs value={tab} onChange={setTab} tabs={[
          { value: "rama", label: <TabLabel text="Ramachandran" count={rs.outlier} tone="err" title="Outlier di Ramachandran" /> },
          { value: "profiles", label: "Profili per residuo" },
          { value: "clashes", label: <TabLabel text="Contatti sterici" count={report.clashes.count} tone="warn" title="Contatti sterici gravi" /> },
          { value: "residues", label: <TabLabel text="Residui" count={problems.length} tone="warn" title="Residui problematici" /> },
          { value: "3d", label: "3D" },
          { value: "interactions", label: "Interazioni" },
          { value: "pockets", label: "Tasche" },
          { value: "electrostatics", label: "Elettrostatica" },
          { value: "mutations", label: "Mutagenesi" },
        ]} />
        {tab === "rama" && <RamaTab report={report} consensus={consensus} selected={selected} onSelect={select} onShow3d={showSelected} onTable={() => setTab("residues")} />}
        {tab === "profiles" && (
          <div className="stack">
            {coloring && (
              <div className="stack-sm">
                <div className="row small">
                  <ColorBySelect options={options} value={shownBy} onChange={setColorBy} />
                  <span className="text-2">Striscia per residuo: {coloring.title}. Clic su un residuo per selezionarlo nei profili.</span>
                </div>
                {/* qui non c'è il 3D accanto (con la sua legenda): la striscia porta la legenda della scala */}
                <ResidueStrip residues={report.residues} coloring={coloring} selected={selected} onSelect={select} legend />
              </div>
            )}
            {hasDope ? (
              <ResidueProfile residues={report.residues} metric="dope" onSelect={select} selected={selected}
                threshold={per?.dope_threshold != null ? { value: per.dope_threshold, label: "picchi segnalati (media + 1 DS)" } : null} />
            ) : (
              <p className="small text-2" role="note">
                <b>Energia DOPE per residuo</b>: non calcolata in questa analisi. Spunta «Calcola il profilo DOPE (MODELLER)» in
                «Nuova analisi», in cima alla pagina, e rianalizza la struttura (serve MODELLER).
              </p>
            )}
            {per?.values.qmean && (
              <ResidueProfile residues={report.residues} values={per.values.qmean} label="QMEAN locale per residuo" hint="Da 0 a 1; sotto 0,6 qualità bassa attesa (SWISS-MODEL)." onSelect={select} selected={selected} height={150}
                threshold={{ value: QMEAN_LOCAL_THRESHOLD, label: "0,6" }} filename="profilo_qmean_locale" />
            )}
            {per?.values.prosa && (
              <ResidueProfile residues={report.residues} values={per.values.prosa} label="Energia ProSA (finestra 10)" hint="Valori positivi: parti potenzialmente problematiche (Wiederstein & Sippl 2007)." onSelect={select} selected={selected} height={150}
                threshold={{ value: 0, label: "0" }} filename="profilo_prosa" />
            )}
            <ResidueProfile residues={report.residues} metric="rsa" onSelect={select} selected={selected} height={150} />
            <ResidueProfile residues={report.residues} metric={plddt ? "plddt" : "bfactor"} onSelect={select} selected={selected} height={150} />
            {/* sotto i profili: aprendosi non sposta i grafici sotto il puntatore */}
            {detail({ onShow3d: showSelected, onTable: () => setTab("residues") }) || <p className="small text-2">Clic su un residuo per i dettagli.</p>}
          </div>
        )}
        {tab === "clashes" && (
          <ClashTable report={report} onShow={(c) => {
            show(clashHighlight(c));
            setTab("3d");
          }} />
        )}
        {tab === "residues" && (
          <div className="stack">
            <ResidueTable residues={report.residues} onPick={select} selected={selected} consensus={consensus} plddt={plddt} />
            {detail({ onShow3d: showSelected }) || <p className="small text-2">Clic su una riga per i dettagli del residuo.</p>}
          </div>
        )}
        {tab === "3d" && (
          url ? (
            <div className="stack-sm">
              <div className="row small">
                <ColorBySelect options={options} value={shownBy} onChange={setColorBy} />
                <button type="button" className="btn btn-sm" onClick={() => show(residuesHighlight(problems, selected))}>Evidenzia tutti i residui problematici ({problems.length})</button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => show(residuesHighlight([]), false)}>Nessuna evidenza</button>
                <span className="text-2">Evidenziati a bastoncini arancioni · clic su un residuo per selezionarlo.{lines.length > 0 && ligand && ` Linee tratteggiate: coordinazione di ${ligand.resname} ${ligand.chain}${ligand.resnum}.`}</span>
              </div>
              <Viewer3D url={url} highlight={focus} format={url.endsWith(".cif") ? "cif" : "pdb"} coloring={coloring} onPickResidue={pick3d} centerKey={centerKey} handle={viewer} lines={lines} centerZoom={!!ligand} />
              {coloring && <ResidueStrip residues={report.residues} coloring={coloring} selected={selected} onSelect={pick} />}
              {detail({ onTable: () => setTab("residues") })}
            </div>
          ) : <p className="small text-2">Struttura non disponibile per la visualizzazione 3D.</p>
        )}
        {/* analisi strutturale (F7): interazioni, interfacce, conservazione della stessa struttura */}
        {tab === "interactions" && (
          reportSource ? <StructuralAnalysisPanel source={reportSource} url={url} memory={analysisMemory} onMemory={setAnalysisMemory} />
            : <p className="small text-2">Struttura del rapporto non disponibile: rilancia l'analisi di qualità.</p>
        )}
        {/* tasche (fpocket) ed elettrostatica (PDB2PQR + APBS) della stessa struttura (F7b) */}
        {(tab === "pockets" || tab === "electrostatics") && !reportSource && (
          <p className="small text-2">Struttura del rapporto non disponibile: rilancia l'analisi di qualità.</p>
        )}
        {tab === "pockets" && reportSource && <PocketsPanel source={reportSource} url={url} memory={pocketsMemory} onMemory={setPocketsMemory} />}
        {tab === "electrostatics" && reportSource && <ElectrostaticsPanel source={reportSource} url={url} memory={electroMemory} onMemory={setElectroMemory} />}
        {/* mutagenesi in silico e scansione di alanina della stessa struttura (F9.2) */}
        {tab === "mutations" && (reportSource
          ? <MutagenesisPanel source={reportSource} memory={mutationMemory} onMemory={setMutationMemory} />
          : <p className="small text-2">Struttura del rapporto non disponibile: rilancia l'analisi di qualità.</p>)}
      </Card>
    </div>
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
  const pjob = params.get("pjob"); // tasche da un link (F7b)
  const ejob = params.get("ejob"); // elettrostatica da un link (F7b)
  const prjob = params.get("prjob"); // confronto con una predizione da un link (F9.3)
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

  const picker = (
    <div className="stack">
      <StructurePicker value={source} onChange={setSource} />
      <label className="check"><input type="checkbox" checked={dope} onChange={(e) => setDope(e.target.checked)} /> Calcola il profilo DOPE (MODELLER)</label>
      <label className="check"><input type="checkbox" checked={extended} onChange={(e) => setExtended(e.target.checked)} /> PROCHECK e ProSA II (se installati)</label>
      <button type="button" className="btn btn-primary" disabled={!source} onClick={() => run(source)}><Icon name="shield" size={14} /> Analizza</button>
      <hr className="sep" />
      <DropZone accept=".pdb,.ent,.cif,.mmcif" onFile={upload} label={<>Oppure carica un file <b>.pdb</b> / <b>.cif</b> da analizzare</>} />
    </div>
  );
  const howto = (
    <div className="stack-sm small text-2">
      <div className="row"><Grade grade="A" /> ≥ 85: modello affidabile per analisi strutturali.</div>
      <div className="row"><Grade grade="B" /> ≥ 70: buono; controlla le regioni segnalate.</div>
      <div className="row"><Grade grade="C" /> ≥ 55: da rifinire (loop, allineamento).</div>
      <div className="row"><Grade grade="D" /> &lt; 55: problematico; rivedi template e allineamento.</div>
      <p>Punteggio = 100 − penalità Ramachandran (outlier oltre lo 0,5%) − penalità clashscore − penalità zDOPE (se &gt; −1).</p>
      <p>Il consenso (0–100) combina voto, z-DOPE, GA341, QMEAN, ProSA e PROCHECK, quando disponibili, e indica i metodi che discordano e le regioni segnalate da più metodi.</p>
    </div>
  );
  const head = <PageHead title="Qualità strutturale" lead="Validazione di modelli e template: Ramachandran, contatti sterici, energia DOPE per residuo, struttura secondaria, accessibilità al solvente e voto complessivo A–D." />;

  // con un rapporto aperto la pagina è a una colonna: grafici e tabelle usano tutta la larghezza, la scelta di
  // un'altra struttura e la guida al voto restano in alto, richiudibili
  if (report) {
    // Rapporto salvato di un modello (?job=&file=): spesso senza PROCHECK e ProSA, la mossa probabile è rianalizzarlo,
    // quindi «Nuova analisi» parte aperta, con il modello già scelto e la nota di cosa manca (chiave propria: chiuderla
    // sugli altri rapporti non la nasconde qui). Negli altri casi parte chiusa: «Rianalizza» nell'intestazione rilancia
    // la struttura mostrata con le opzioni correnti senza aprirla.
    const savedModel = !qjob && !!modelJob && !!modelFile;
    const missing = missingAnalyses(report, { dope, extended });
    const options = andList([dope && "DOPE", extended && "PROCHECK", extended && "ProSA"].filter((o): o is string => !!o));
    return (
      <div className="page">
        {head}
        <div className="grid grid-2" style={{ alignItems: "start" }}>
          <Card
            title="Nuova analisi"
            sub={savedModel ? "rianalizza questo modello, scegline un altro o carica un file" : "rianalizza questa struttura, scegline un'altra o carica un file"}
            icon="upload"
            collapsible
            defaultOpen={savedModel}
            storageKey={savedModel ? "quality.new.model" : "quality.new"}
            right={reportSource && (
              <button type="button" className="btn btn-sm" onClick={() => run(reportSource)}
                title={`Rilancia l'analisi della struttura mostrata${options ? ` con ${options} (se installati)` : ""}`}>
                <Icon name="refresh" size={13} /> Rianalizza
              </button>
            )}
          >
            <div className="stack-sm">
              {missing.length > 0 && (
                <p className="small text-2">
                  {savedModel ? "Il rapporto salvato del modello" : "Questo rapporto"} non ha {andList(missing)}: «Rianalizza» {missing.length > 1 ? "li aggiunge, se installati" : "lo aggiunge, se installato"}.
                </p>
              )}
              {picker}
            </div>
          </Card>
          <Card title="Come leggere il voto" sub={GRADE_RULE} icon="info" collapsible defaultOpen={false} storageKey="quality.howto.report">{howto}</Card>
        </div>
        {qjob && <JobPanel summary={summary} log={log} onCancel={cancel} />}
        <Report key={qjob ?? `${modelJob}/${modelFile}`} report={report} source={reportSource} consensusRef={consensusRef} />
        <CompareAnalyses />
      </div>
    );
  }

  return (
    <div className="page">
      {head}
      <div className="split">
        <div className="stack">
          <Card title="Struttura da analizzare" icon="upload">{picker}</Card>
          <Card title="Come leggere il voto" sub={GRADE_RULE} icon="info" collapsible storageKey="quality.howto">{howto}</Card>
        </div>
        <div className="stack">
          {qjob && <JobPanel summary={summary} log={log} onCancel={cancel} />}
          {ajob && !qjob && <AnalysisJobCard jobId={ajob} />}
          {pjob && !qjob && <ToolJobCard kind="pockets" jobId={pjob} />}
          {ejob && !qjob && <ToolJobCard kind="electrostatics" jobId={ejob} />}
          {prjob && !qjob && <PredictionJobCard jobId={prjob} />}
          {!qjob && !ajob && !pjob && !ejob && !prjob && (
            <Card>
              {modelJob ? (
                <Empty icon="shield" title="Analisi non ancora calcolata per questo modello">
                  <button type="button" className="btn btn-primary" onClick={() => run(source)}>Analizza ora</button>
                </Empty>
              ) : <Empty icon="shield" title="Scegli una struttura o carica un file" />}
            </Card>
          )}
          <CompareAnalyses />
        </div>
      </div>
    </div>
  );
}
