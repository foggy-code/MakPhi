import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  DocumentItem, JobSummary, LigandRef, LoopResult, ModelEntry, ModelResult, PreparedTemplate, Sequence, StructureSource, SystemInfo, TemplateAnalysis, TemplateItem,
  TemplateRef,
} from "../api/types";
import type { RefineResult } from "../api/types";
import { LigandPicker } from "../components/LigandPicker";
import { LigandTable } from "../components/LigandsPanel";
import { groupTitle, STATUS_TONE } from "../lib/ligands";
import { AlignmentView } from "../components/AlignmentView";
import { ComplexInput } from "../components/ComplexInput";
import { ComplexResults, type ComplexView } from "../components/ComplexResults";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { TextFileButton } from "../components/FileLoad";
import { SequencePicker, StructurePicker, TemplatePicker } from "../components/Pickers";
import { ReportButton } from "../components/ReportButton";
import { RefineDialog, RefineResults } from "../components/Refinement";
import { Viewer3D, type Viewer3DHandle } from "../components/Viewer3D";
import { Alert, Badge, Card, Empty, Field, Grade, Modal, PageHead, Segmented, Stat } from "../components/ui";
import { pirReference } from "../lib/alignConfidence";
import { PlddtOption } from "../components/PlddtOption";
import { hasPredictedTemplate, PLDDT_DEFAULT, removedRanges, trimSummary } from "../lib/alphafold";
import { compareTargets } from "../lib/compare";
import { complexRequest, complexTemplateRefs, emptyComplex, STATUS_TONE as COMPLEX_TONE, symmetryOptions, type ComplexState } from "../lib/complex";
import { useProject, useToast } from "../lib/context";
import { modelingAccession } from "../lib/disorder";
import { modelingPreflight } from "../lib/preflight";
import { LaunchButton, PreflightPanel, usePreflight } from "../components/Preflight";
import { fileStem, formatRestraints, parseRestraints, type RestraintRow } from "../lib/files";
import { downloadText, fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { parsePir } from "../lib/pir";

const REFINEMENT = [
  { value: "very_fast", label: "Rapidissimo", note: "Test o screening: ottimizzazione minima senza dinamica; i modelli sono tutti uguali (ne basta uno)." },
  { value: "fast", label: "Rapido", note: "Impostazioni predefinite di MODELLER: buon compromesso per molti modelli." },
  { value: "normal", label: "Standard", note: "Dinamica di raffinamento più lunga (refine.fast): circa 1,4× il tempo di Rapido." },
  { value: "thorough", label: "Accurato", note: "Ottimizzazione ripetuta, dinamica lenta: circa 4× il tempo di Rapido." },
];

type Restraint = RestraintRow;

/** «Confronta…»: il modello (B) contro un template usato o un altro modello dello stesso job (A, riferimento). */
function CompareSelect({ jobId, model, models, templates }: { jobId: string; model: ModelEntry; models: ModelEntry[]; templates?: PreparedTemplate[] }) {
  const navigate = useNavigate();
  const targets = compareTargets(jobId, model.file, models, templates);
  if (model.failure || !targets.length) return null;
  return (
    <select className="input" style={{ width: 118, minHeight: 28, padding: "2px 6px", display: "inline-block" }} value="" title="Confronto strutturale del modello (GDT, lDDT, deviazione per residuo)"
      onChange={(e) => { const t = targets[Number(e.target.value)]; if (t) navigate(t.href); }}>
      <option value="">Confronta…</option>
      {targets.map((t, i) => <option key={t.href} value={i}>con {t.label}</option>)}
    </select>
  );
}

function ModelsTable({ jobId, models, selected, onSelect, templates, viewer, onRefine }: { jobId: string; models: ModelEntry[]; selected: string; onSelect: (f: string) => void; templates?: PreparedTemplate[]; viewer?: RefObject<Viewer3DHandle | null>; onRefine?: (file: string) => void }) {
  const navigate = useNavigate();
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Modello</th>
            <th className="num" title="Energia statistica DOPE: più negativa è migliore">DOPE</th>
            <th className="num" title="DOPE normalizzato: &lt; −1 simile a strutture native">zDOPE</th>
            <th className="num" title="GA341 &gt; 0,7: fold affidabile">GA341</th>
            <th className="num">molpdf</th>
            <th>Qualità</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.file} className={`clickable ${m.file === selected ? "selected" : ""}`} onClick={() => !m.failure && onSelect(m.file)}>
              <td className="nowrap">
                <b>{m.file}</b> {m.best && <Badge tone="ok">migliore</Badge>} {m.kind === "loop" && <Badge tone="accent">loop</Badge>}
                {m.failure && <div className="tiny" style={{ color: "var(--err)" }}>{m.failure}</div>}
              </td>
              <td className="num">{fmtNum(m.dope, 1)}</td>
              <td className="num">{fmtNum(m.zdope, 2)}</td>
              <td className="num">{fmtNum(m.ga341, 2)}</td>
              <td className="num">{fmtNum(m.molpdf, 1)}</td>
              <td>
                {m.quality ? <span className="row" style={{ gap: 6 }}><Grade grade={m.quality.grade} /> <span className="tiny muted">{fmtNum(m.quality.rama_favored)}% fav.</span></span> : <span className="tiny muted">—</span>}
                {m.ligands && <Badge tone={STATUS_TONE[m.ligands.status]} title="Verifica dei ligandi trasferiti (contatti, scontri, coordinazione)">ligandi: {m.ligands.status}</Badge>}
                {m.complex && <Badge tone={COMPLEX_TONE[m.complex.status]} title={`Interfacce confrontate con il template${m.complex.dockq != null ? `: DockQ minimo ${fmtNum(m.complex.dockq, 2)}` : ""}`}>interfacce: {m.complex.status}</Badge>}
              </td>
              <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                <button className="btn btn-sm" disabled={!!m.failure} title="Analisi di qualità completa" onClick={() => navigate(`/quality?job=${jobId}&file=${encodeURIComponent(m.file)}`)}>
                  <Icon name="shield" size={13} />
                </button>{" "}
                <a className="btn btn-sm" href={api.jobFileUrl(jobId, m.file, true)} title="Scarica PDB"><Icon name="download" size={13} /></a>{" "}
                <a className="btn btn-sm" href={api.pymolUrl(jobId, m.file)} title="Sessione PyMOL (.pml)">PyMOL</a>{" "}
                <CompareSelect jobId={jobId} model={m} models={models} templates={templates} />
                {/* il viewer mostra il modello selezionato: solo il suo report riceve la vista 3D */}
                <ReportButton jobId={jobId} model={m.file} viewer={m.file === selected ? viewer : undefined} disabled={!!m.failure} />
                {onRefine && <>{" "}<button className="btn btn-sm" disabled={!!m.failure} title="Rifinitura energetica con OpenMM (minimizzazione con restrizioni sulla catena principale)" onClick={() => onRefine(m.file)}><Icon name="flask" size={13} /> Rifinisci</button></>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LoopDialog({ source, label, onClose }: { source: StructureSource; label: string; onClose: () => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [chain, setChain] = useState("A");
  const [start, setStart] = useState(1);
  const [end, setEnd] = useState(8);
  const [models, setModels] = useState(4);
  const [level, setLevel] = useState("fast");
  const submit = async () => {
    try {
      const j = await api.loop({ project_id: projectId ?? undefined, source, chain, start, end, models, level });
      toast.push("Raffinamento del loop avviato");
      onClose();
      navigate(`/modeling?job=${j.id}`);
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <Modal title={`Raffina un loop di ${label}`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Annulla</button><button className="btn btn-primary" onClick={submit}>Avvia</button></>}>
      <div className="stack">
        <p className="small text-2">MODELLER ricostruisce la regione indicata con il protocollo DOPE-loop mantenendo fisso il resto del modello. Numerazione del modello (catena A per un bersaglio a catena singola).</p>
        <div className="grid grid-3">
          <Field label="Catena"><input className="input" value={chain} maxLength={1} onChange={(e) => setChain(e.target.value.toUpperCase())} /></Field>
          <Field label="Primo residuo"><input className="input" type="number" value={start} onChange={(e) => setStart(Number(e.target.value))} /></Field>
          <Field label="Ultimo residuo"><input className="input" type="number" value={end} onChange={(e) => setEnd(Number(e.target.value))} /></Field>
          <Field label="Modelli di loop"><input className="input" type="number" min={1} max={50} value={models} onChange={(e) => setModels(Number(e.target.value))} /></Field>
          <Field label="Dinamica">
            <select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="very_fast">Molto rapida</option>
              <option value="fast">Rapida</option>
              <option value="slow">Lenta</option>
            </select>
          </Field>
        </div>
      </div>
    </Modal>
  );
}

function Results({ jobId, result, kind }: { jobId: string; result: ModelResult | LoopResult; kind: string }) {
  const { projectId } = useProject();
  const toast = useToast();
  const [selected, setSelected] = useState(result.best_model);
  const [loop, setLoop] = useState(false);
  const [refine, setRefine] = useState<string | null>(null); // modello da rifinire con OpenMM (F9.1)
  // complesso (F5.3): catene e interfacce del modello mostrato colorate nel 3D
  const [complexView, setComplexView] = useState<ComplexView | null>(null);
  const viewer = useRef<Viewer3DHandle>(null);
  const best = result.models.find((m) => m.best);
  const full = kind === "model" ? (result as ModelResult) : null;
  const entries = useMemo(() => (full ? parsePir(full.alignment) : []), [full]);
  const savePir = async () => {
    if (!projectId || !full) return;
    try {
      const templates: TemplateRef[] = full.templates.map((t) => ({ id: t.id, chains: t.chains }));
      await api.saveDocument(projectId, { name: `Allineamento ${full.target.code} (job ${jobId.slice(0, 6)})`, kind: "pir", content: full.alignment, meta: { templates, job_id: jobId } });
      toast.push("PIR salvato nei documenti del progetto");
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <div className="stack">
      {full?.warnings?.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      {best?.quality && (
        <div className="grid grid-4">
          <Stat label="Voto del modello migliore" value={<span className="row"><Grade grade={best.quality.grade} large /> <span>{fmtNum(best.quality.grade_score, 1)}<small>/100</small></span></span>} />
          <Stat label="Ramachandran" value={<>{fmtNum(best.quality.rama_favored)}<small>% favorite</small></>} note={`${fmtNum(best.quality.rama_outliers)}% outlier`} />
          <Stat label="Clashscore" value={fmtNum(best.quality.clashscore, 2)} note="contatti gravi / 1000 atomi" />
          <Stat label="DOPE" value={fmtNum(best.dope, 0)} note={`zDOPE ${fmtNum(best.zdope, 2)} · GA341 ${fmtNum(best.ga341, 2)}`} />
        </div>
      )}
      {best?.quality && (best.quality.prosa_z != null || best.quality.procheck_core != null) && (
        <div className="grid grid-4">
          <Stat label="ProSA II z-score" value={fmtNum(best.quality.prosa_z ?? null, 2)} note="energia del ripiegamento" />
          <Stat label="PROCHECK core" value={<>{fmtNum(best.quality.procheck_core ?? null)}<small>%</small></>} note={`G-factor ${fmtNum(best.quality.procheck_g_overall ?? null, 2)}`} />
        </div>
      )}
      <Card
        title={`${result.models.length} modelli`}
        sub="Il migliore ha il DOPE più basso; l'analisi completa è calcolata sul migliore"
        right={
          <>
            <button className="btn btn-sm" onClick={() => setLoop(true)}><Icon name="wand" size={14} /> Raffina un loop</button>
            <a className="btn btn-sm" href={api.archiveUrl(jobId)}><Icon name="download" size={14} /> Archivio completo</a>
          </>
        }
      >
        <ModelsTable jobId={jobId} models={result.models} selected={selected} onSelect={setSelected} templates={full?.templates} viewer={viewer} onRefine={setRefine} />
      </Card>
      {full?.ligands && (
        <Card title="Ligandi, cofattori e metalli trasferiti" sub="verifica del modello migliore; il dettaglio e la vista 3D sono nella pagina Qualità">
          <div className="stack-sm">
            <div className="small text-2">
              Trasferiti: {full.ligands.transferred.length ? full.ligands.transferred.map((g) => `${groupTitle(g)} (${g.label}, da ${g.template})`).join(", ") : "nessuno"}
            </div>
            {full.ligands.verification ? <LigandTable report={full.ligands.verification} /> : <Alert tone="warn">Nessun gruppo eteroatomico nel modello migliore.</Alert>}
          </div>
        </Card>
      )}
      {full?.complex && <ComplexResults jobId={jobId} complex={full.complex} selected={selected} best={result.best_model} onView={setComplexView} />}
      <Card title={`Struttura 3D · ${selected}`} sub="Il report del modello selezionato include questa vista">
        <Viewer3D url={api.jobFileUrl(jobId, selected)} handle={viewer} coloring={complexView?.coloring ?? null} highlight={complexView?.highlight ?? []} centerKey={complexView?.centerKey} />
      </Card>
      {full && (
        <Card title="Allineamento usato da MODELLER" right={<button className="btn btn-sm" onClick={savePir}><Icon name="save" size={14} /> Salva PIR nel progetto</button>}>
          <div className="stack-sm">
            <div className="small text-2">
              Template: {full.templates.map((t) => `${t.code} (${t.first}–${t.last}${t.ligands ? `, ${t.ligands} ligandi` : ""})`).join(", ")}
            </div>
            {full.templates.filter((t) => t.predicted).map((t) => (
              <div key={t.code} className="small row" style={{ gap: 6 }}>
                <Badge tone="warn" title="Modello predetto da AlphaFold, non struttura sperimentale">AlphaFold</Badge>
                <span className="text-2">{t.code}: {trimSummary(t)}{removedRanges(t) && <span className="muted"> · tolti {removedRanges(t)}</span>}</span>
              </div>
            ))}
            {entries.length > 1 && <AlignmentView ids={entries.map((e) => e.code)} aligned={entries.map((e) => e.sequence)} reference={pirReference(entries.map((e) => e.kind))} />}
          </div>
        </Card>
      )}
      {loop && <LoopDialog source={{ type: "job", job_id: jobId, file: selected }} label={selected} onClose={() => setLoop(false)} />}
      {refine && <RefineDialog source={{ type: "job", job_id: jobId, file: refine }} label={refine} onClose={() => setRefine(null)} />}
    </div>
  );
}

export default function Modeling() {
  const { projectId } = useProject();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [mode, setMode] = useState<"auto" | "pir" | "complex">("auto");
  const [target, setTarget] = useState({ name: "", sequence: "" });
  const [templates, setTemplates] = useState<TemplateRef[]>([]);
  // modalità Complesso (F5.3): un template, una sequenza del bersaglio per catena, simmetria
  const [complexState, setComplexState] = useState<ComplexState>(() => emptyComplex());
  // memorizzati: entrano nel corpo della richiesta, da cui dipendono i controlli preventivi (niente richieste a ogni render)
  const complexCheck = useMemo(() => complexRequest(complexState), [complexState]);
  const activeTemplates = useMemo(() => (mode === "complex" ? complexTemplateRefs(complexState) : templates), [mode, complexState, templates]);
  const [docId, setDocId] = useState("");
  const [nModels, setNModels] = useState(5);
  const [refinement, setRefinement] = useState("fast");
  const [quality, setQuality] = useState<"best" | "all" | "none">("best");
  const [extended, setExtended] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [loopOn, setLoopOn] = useState(false);
  const [loopModels, setLoopModels] = useState(4);
  const [loopRegions, setLoopRegions] = useState("");
  const [restraints, setRestraints] = useState<Restraint[]>([]);
  const [seed, setSeed] = useState(-8123);
  const [plddtTrim, setPlddtTrim] = useState(true);
  const [plddtMin, setPlddtMin] = useState(PLDDT_DEFAULT);
  // ligandi, cofattori e metalli da trasferire (opzioni hetatm e ligands)
  const [ligandsOn, setLigandsOn] = useState(false);
  const [ligandSel, setLigandSel] = useState<Record<string, boolean>>({});
  const [ligandRefs, setLigandRefs] = useState<LigandRef[]>([]);
  const [ligandsReady, setLigandsReady] = useState(false);
  const [ligandsFailed, setLigandsFailed] = useState<string[]>([]);
  // analisi dei template lette dal selettore (anche dopo un download): il riquadro dei ligandi non le rilegge
  const [templateInfo, setTemplateInfo] = useState<Record<string, TemplateAnalysis>>({});
  const [loopSource, setLoopSource] = useState<StructureSource | null>(null);
  const [loopOpen, setLoopOpen] = useState(false);
  const [refineOpen, setRefineOpen] = useState(false); // rifinitura energetica della struttura scelta (F9.1)
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  // libreria riletta quando cambiano i template scelti: un modello AlphaFold appena scaricato risulta predetto
  const templateKey = activeTemplates.map((t) => t.id).join(",");
  const { data: library } = useAsync<TemplateItem[]>(() => api.templates(), [templateKey]);
  const predictedChosen = hasPredictedTemplate(activeTemplates, library);
  const { data: docs, reload: reloadDocs } = useAsync<DocumentItem[]>(() => (projectId ? api.documents(projectId) : Promise.resolve([])), [projectId]);
  const { data: recent } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "model", limit: 8 }), [projectId, jobId]);
  const { job, summary, log, cancel } = useJob<ModelResult | LoopResult>(jobId, { withLog: true });
  // Disordine e domini prima di lanciare (F5.5): accessione dalla sequenza del progetto o dall'intestazione sp|ACC|…
  const { data: projectSeqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const [pirText, setPirText] = useState<string | null>(null);
  useEffect(() => {
    setPirText(null);
    if (mode !== "pir" || !docId || !projectId) return;
    let current = true;
    api.document(projectId, docId).then((d) => current && setPirText(d.content ?? "")).catch(() => undefined);
    return () => {
      current = false;
    };
  }, [mode, docId, projectId]);
  // Corpo della modellazione (senza l'allineamento, letto al lancio): lo stesso per l'avvio e per i controlli preventivi
  const body = useMemo(() => {
    const regions = loopRegions
      .split(/[,;\n]/)
      .map((r) => r.trim())
      .filter(Boolean)
      .map((r) => {
        const [s, e] = r.split("-").map((x) => x.trim());
        return { start: s, end: e };
      });
    const options = {
      models: nModels,
      refinement,
      seed,
      loop: { enabled: loopOn, models: loopModels, level: "fast", regions },
      restraints,
      // solo per i template AlphaFold; le strutture sperimentali non si ritagliano mai
      plddt_trim: plddtTrim,
      plddt_min: plddtMin,
      // ligandi: esattamente i gruppi scelti (un elenco vuoto = nessuno)
      hetatm: ligandsOn,
      ...(ligandsOn ? { ligands: ligandRefs } : {}),
      // complesso: restrizioni di simmetria solo con catene uguali
      ...(mode === "complex" ? symmetryOptions(complexState, complexCheck) : {}),
    };
    return {
      project_id: projectId ?? undefined,
      target: mode === "auto" ? { name: target.name || "target", sequence: target.sequence } : null,
      // la scelta dei ligandi è quella del riquadro Parametri, non la casella del singolo template
      templates: activeTemplates.map((t) => ({ id: t.id, chains: t.chains })),
      options,
      quality,
      extended_quality: quality !== "none" && extended,
      ...(mode === "complex" && complexCheck.request ? { complex: complexCheck.request } : {}),
    };
  }, [projectId, mode, target, templates, nModels, refinement, seed, loopOn, loopModels, loopRegions, restraints, plddtTrim, plddtMin, ligandsOn, ligandRefs, quality, extended, activeTemplates, complexState, complexCheck]);
  // Controlli preventivi (F8a), compresi disordine e domini fuori dalla copertura (F5.5): accessione dalla sequenza del
  // progetto o dall'intestazione sp|ACC|…
  // nella modalità Complesso niente avvisi di copertura (una sequenza per catena): solo i controlli del corpo
  const preflightRequest = useMemo(() => (mode === "complex"
    ? (complexCheck.request ? { kind: "model" as const, params: { ...body, alignment: null } } : null)
    : modelingPreflight({
      mode, target, templates, pirText, accession: modelingAccession({ mode, target, pirText }, projectSeqs), body,
    })), [mode, target, templates, pirText, projectSeqs, body, complexCheck]);
  const preflight = usePreflight(preflightRequest);

  // Parametri in arrivo da altre pagine (sequenza, template, documento PIR).
  useEffect(() => {
    const seqId = params.get("seq");
    const seqText = params.get("seqtext");
    const t = params.getAll("t");
    const doc = params.get("doc");
    const cmplx = params.get("cmplx"); // libreria → «Complesso»: template a più catene o assemblaggio
    if (cmplx) {
      setMode("complex");
      setComplexState(emptyComplex(cmplx));
    }
    if (seqText) setTarget({ name: params.get("name") || "target", sequence: seqText });
    if (seqId && projectId) api.sequences(projectId).then((l: Sequence[]) => { const s = l.find((x) => x.id === seqId); if (s) setTarget({ name: s.name, sequence: s.sequence }); });
    if (t.length) setTemplates(t.map((x) => { const [id, ch] = x.split(":"); return { id, chains: ch ? ch.split("") : [] }; }));
    if (doc) {
      setMode("pir");
      setDocId(doc);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (mode !== "pir" || !docId || !projectId) return;
    api.document(projectId, docId).then((d) => {
      const tm = (d.meta?.templates as TemplateRef[] | undefined) ?? [];
      if (tm.length) setTemplates(tm);
    }).catch(() => undefined);
  }, [mode, docId, projectId]);

  const submit = async () => {
    try {
      let alignment: string | null = null;
      if (mode === "pir") {
        if (!projectId || !docId) throw new Error("Scegli un documento PIR.");
        alignment = (await api.document(projectId, docId)).content ?? "";
      }
      if (mode === "complex" && !complexCheck.request) throw new Error(complexCheck.errors[0] ?? "Complesso incompleto.");
      const j = await api.model({ ...body, alignment });
      setParams({ job: j.id });
    } catch (e) {
      toast.error(e);
    }
  };

  /** PIR da file: validato e salvato tra i documenti del progetto, poi selezionato. */
  const loadPirFile = async (text: string, file: File) => {
    if (!projectId) return;
    try {
      const report = await api.validatePir(text);
      if (!report.entries.length) {
        toast.push("Il file non contiene blocchi PIR ('>P1;'). Per altri formati di allineamento usa l'Editor PIR (Apri file).", "warn");
        return;
      }
      const doc = await api.saveDocument(projectId, { name: fileStem(file.name), kind: "pir", content: text });
      reloadDocs();
      setDocId(doc.id);
      if (!report.valid) toast.push(`PIR caricato ma con errori: ${report.errors.join(" ")}`, "warn");
      else toast.push("PIR caricato e salvato nei documenti del progetto");
    } catch (e) {
      toast.error(e);
    }
  };

  const loadRestraints = (text: string) => {
    const { restraints: rows, errors } = parseRestraints(text);
    errors.slice(0, 3).forEach((e) => toast.push(e, "warn"));
    if (rows.length) {
      setRestraints(rows);
      toast.push(`${rows.length} vincoli caricati`);
    } else if (!errors.length) toast.push("Nessun vincolo nel file.", "warn");
  };

  const pirDocs = docs?.filter((d) => d.kind === "pir") ?? [];
  const modellerMissing = info && !info.modeller.available;
  const canSubmit = (mode === "complex" ? !!complexCheck.request
    : templates.length > 0 && templates.every((t) => t.chains.length > 0) && (mode === "auto" ? !!target.sequence.trim() : !!docId))
    && (!ligandsOn || ligandsReady);

  return (
    <div className="page">
      <PageHead title="Modellazione" lead="Costruzione di modelli 3D con MODELLER: allineamento automatico (align2d/salign) oppure da un PIR rifinito, ottimizzazione con vincoli spaziali e selezione del modello migliore per DOPE." />
      {modellerMissing && <Alert tone="err">MODELLER non è disponibile su questo computer. Configura l'interprete Python con MODELLER nelle Impostazioni.</Alert>}
      <div className="split">
        <div className="stack">
          <Card title="Input">
            <div className="stack">
              <Segmented value={mode} onChange={setMode} options={[{ value: "auto", label: "Allineamento automatico" }, { value: "pir", label: "Da documento PIR" }, { value: "complex", label: "Complesso" }]} />
              {mode === "complex" ? (
                <ComplexInput value={complexState} onChange={setComplexState} />
              ) : mode === "auto" ? (
                <SequencePicker value={target} onChange={setTarget} />
              ) : (
                <div className="stack-sm">
                  <Field label="Documento PIR" hint="Crealo o modificalo nell'Editor PIR, oppure caricalo da file.">
                    <select className="input" value={docId} onChange={(e) => setDocId(e.target.value)}>
                      <option value="">— scegli —</option>
                      {pirDocs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                  </Field>
                  <div className="row">
                    <TextFileButton accept=".pir,.ali,.txt" onText={loadPirFile} disabled={!projectId}>Carica un file PIR</TextFileButton>
                    <span className="tiny muted">viene salvato tra i documenti del progetto</span>
                  </div>
                </div>
              )}
              {mode !== "complex" && (
                <div className="stack-sm">
                  <span className="small text-2" style={{ fontWeight: 550 }}>Template {mode === "pir" && "(stessi del PIR, nello stesso ordine)"}</span>
                  <TemplatePicker value={templates} onChange={setTemplates} ligands={false} onAnalysis={(id, a) => setTemplateInfo((prev) => ({ ...prev, [id]: a }))} />
                </div>
              )}
            </div>
          </Card>
          <Card title="Parametri">
            <div className="stack">
              <div className="grid grid-2">
                <Field label="Numero di modelli" hint="5–10 per scegliere il migliore"><input className="input" type="number" min={1} max={100} value={nModels} onChange={(e) => setNModels(Number(e.target.value))} /></Field>
                <Field label="Analisi di qualità">
                  <select className="input" value={quality} onChange={(e) => setQuality(e.target.value as "best" | "all" | "none")}>
                    <option value="best">Solo sul modello migliore</option>
                    <option value="all">Su tutti i modelli</option>
                    <option value="none">Nessuna</option>
                  </select>
                </Field>
              </div>
              {quality !== "none" && (
                <label className="check"><input type="checkbox" checked={extended} onChange={(e) => setExtended(e.target.checked)} /> Includi PROCHECK e ProSA II nell'analisi</label>
              )}
              <Field label="Raffinamento" hint={REFINEMENT.find((r) => r.value === refinement)?.note}>
                <Segmented value={refinement} onChange={setRefinement} options={REFINEMENT.map((r) => ({ value: r.value, label: r.label }))} />
              </Field>
              {predictedChosen && <PlddtOption enabled={plddtTrim} threshold={plddtMin} onEnabled={setPlddtTrim} onThreshold={setPlddtMin} />}
              <LigandPicker templates={activeTemplates} known={templateInfo} enabled={ligandsOn} onEnabled={setLigandsOn} selection={ligandSel}
                onSelection={(sel, refs, ready, failed) => { setLigandSel(sel); setLigandRefs(refs); setLigandsReady(ready); setLigandsFailed(failed); }} />
              <button className="btn btn-ghost btn-sm" style={{ justifySelf: "start" }} onClick={() => setAdvanced(!advanced)}>
                <Icon name="chevron" size={13} /> Opzioni avanzate
              </button>
              {advanced && (
                <div className="stack card flat">
                  <label className="check"><input type="checkbox" checked={loopOn} onChange={(e) => setLoopOn(e.target.checked)} /> Raffinamento dei loop (DOPE-loop)</label>
                  {loopOn && (
                    <div className="grid grid-2">
                      <Field label="Modelli di loop per modello"><input className="input" type="number" min={1} max={50} value={loopModels} onChange={(e) => setLoopModels(Number(e.target.value))} /></Field>
                      <Field label="Regioni (facoltative)" hint="Es. 45:A-52:A, 80:A-86:A. Vuoto = gap dell'allineamento."><input className="input" value={loopRegions} onChange={(e) => setLoopRegions(e.target.value)} /></Field>
                    </div>
                  )}
                  <div className="stack-sm">
                    <div className="row"><b className="small">Vincoli aggiuntivi</b>
                      <div className="right row">
                        <TextFileButton accept=".txt,.csv,.tsv,.rsr" onText={loadRestraints}>Da file</TextFileButton>
                        {!!restraints.length && (
                          <button className="btn btn-sm" onClick={() => downloadText("vincoli_bioloom.txt", formatRestraints(restraints))}><Icon name="download" size={13} /> Esporta</button>
                        )}
                        <button className="btn btn-sm" onClick={() => setRestraints([...restraints, { type: "helix", start: "", end: "" }])}><Icon name="plus" size={13} /> Vincolo</button>
                      </div>
                    </div>
                    {restraints.map((r, i) => (
                      <div key={i} className="row">
                        <select className="input" style={{ width: 120 }} value={r.type} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { type: e.target.value as Restraint["type"] } : x)))}>
                          <option value="helix">α-elica</option>
                          <option value="strand">filamento β</option>
                          <option value="distance">distanza</option>
                        </select>
                        {r.type === "distance" ? (
                          <>
                            <input className="input" style={{ width: 100 }} placeholder="CA:10:A" value={r.atom1 ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, atom1: e.target.value } : x)))} />
                            <input className="input" style={{ width: 100 }} placeholder="CA:50:A" value={r.atom2 ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, atom2: e.target.value } : x)))} />
                            <input className="input" style={{ width: 70 }} type="number" placeholder="Å" value={r.mean ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, mean: Number(e.target.value), stdev: x.stdev ?? 0.5 } : x)))} />
                          </>
                        ) : (
                          <>
                            <input className="input" style={{ width: 90 }} placeholder="10:A" value={r.start ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, start: e.target.value } : x)))} />
                            <input className="input" style={{ width: 90 }} placeholder="20:A" value={r.end ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, end: e.target.value } : x)))} />
                          </>
                        )}
                        <button className="btn btn-ghost btn-icon" onClick={() => setRestraints(restraints.filter((_, k) => k !== i))}><Icon name="x" size={14} /></button>
                      </div>
                    ))}
                    <span className="tiny muted">
                      Residui nella numerazione del modello (es. 10:A); atomi come NOME:residuo:catena. Da file: una riga per vincolo,
                      es. <span className="mono">helix 10:A 20:A</span>, <span className="mono">strand 30:A 35:A</span>, <span className="mono">distance CA:10:A CA:50:A 8.0 0.5</span>.
                    </span>
                  </div>
                  <Field label="Seme casuale MODELLER" hint="Tra −50000 e −2: cambia per ottenere modelli diversi."><input className="input" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></Field>
                </div>
              )}
              {/* controlli preventivi: disordine e domini, template, licenze, tempi, spazio (errori bloccano, avvisi con conferma) */}
              <PreflightPanel state={preflight} />
              <LaunchButton preflight={preflight} disabled={!canSubmit || !!modellerMissing} onLaunch={submit}>Avvia modellazione</LaunchButton>
              {mode !== "complex" && templates.some((t) => !t.chains.length) && <span className="tiny" style={{ color: "var(--warn)" }}>Seleziona almeno una catena per ogni template.</span>}
              {mode === "complex" && !complexCheck.request && complexCheck.errors[0] && <span className="tiny" style={{ color: "var(--warn)" }}>{complexCheck.errors[0]}</span>}
              {ligandsOn && !ligandsReady && activeTemplates.length > 0 && (ligandsFailed.length
                ? <span className="tiny" style={{ color: "var(--warn)" }}>Ligandi di {ligandsFailed.join(", ")} non letti: usa «Riprova» nel riquadro dei ligandi oppure togli la spunta.</span>
                : <span className="tiny muted">Lettura dei ligandi dei template in corso…</span>)}
            </div>
          </Card>
          <Card title="Raffinamento dei loop e rifinitura energetica" sub="di un modello, un template o un file PDB/mmCIF">
            <div className="stack-sm">
              <StructurePicker label="Struttura" value={loopSource} onChange={setLoopSource} />
              <button className="btn btn-sm" style={{ alignSelf: "start" }} disabled={!loopSource || !!modellerMissing} onClick={() => setLoopOpen(true)}>
                <Icon name="wand" size={13} /> Scegli il loop da raffinare
              </button>
              <button className="btn btn-sm" style={{ alignSelf: "start" }} disabled={!loopSource} onClick={() => setRefineOpen(true)} title="Minimizzazione con OpenMM e restrizioni sulla catena principale">
                <Icon name="flask" size={13} /> Rifinitura energetica (OpenMM)
              </button>
            </div>
          </Card>
          {loopOpen && loopSource && (
            <LoopDialog source={loopSource} label={loopSource.id ?? loopSource.file ?? "struttura"} onClose={() => setLoopOpen(false)} />
          )}
          {refineOpen && loopSource && (
            <RefineDialog source={loopSource} label={loopSource.id ?? loopSource.file ?? "struttura"} onClose={() => setRefineOpen(false)} />
          )}
          <Card title="Modellazioni recenti">
            {!recent?.length ? <p className="small muted">Nessuna.</p> : (
              <div className="stack-sm">
                {recent.map((r) => (
                  <button key={r.id} className="btn btn-ghost" style={{ justifyContent: "flex-start", height: "auto", padding: "6px 10px", borderColor: r.id === jobId ? "var(--accent)" : undefined }} onClick={() => setParams({ job: r.id })}>
                    <span className="ellipsis" style={{ flex: 1, textAlign: "left" }}>{r.title}</span>
                    <span className="tiny muted">{relTime(r.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="stack">
          {jobId ? <JobPanel summary={summary} log={log} onCancel={cancel} /> : <Card><Empty icon="cube" title="Configura bersaglio e template, poi avvia la modellazione" /></Card>}
          {job?.status === "completed" && job.result && (job.kind === "refine"
            ? <RefineResults key={job.id} jobId={job.id} result={job.result as unknown as RefineResult} />
            : <Results key={job.id} jobId={job.id} result={job.result} kind={job.kind} />)}
        </div>
      </div>
    </div>
  );
}
