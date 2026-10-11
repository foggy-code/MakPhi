import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  DocumentItem, JobSummary, LigandRef, LoopResult, ModelEntry, ModelResult, PreparedTemplate, Sequence, StructureSource, SystemInfo, TemplateAnalysis, TemplateItem,
  TemplateRef,
} from "../api/types";
import type { RefineResult } from "../api/types";
import type { AlaScanResult, MutateResult } from "../api/types";
import { AlaScanResults, MutateDialog, MutationResults } from "../components/Mutagenesis";
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
import { PredictionPanel } from "../components/Predictor";
import { Viewer3D, type Viewer3DHandle } from "../components/Viewer3D";
import { ModelScores } from "../components/ModelScores";
import {
  Alert, Badge, Card, DetailGrid, Disclosure, Empty, ExpandAll, ExpandButton, Field, Grade, Modal, PageHead, rowActivate, Segmented, Stat, StatusBadge,
  useExpanded,
} from "../components/ui";
import { pirReference } from "../lib/alignConfidence";
import { PlddtOption } from "../components/PlddtOption";
import { hasPredictedTemplate, PLDDT_DEFAULT, predictorBadge, predictorTitle, removedRanges, trimSummary } from "../lib/alphafold";
import { compareTargets } from "../lib/compare";
import { complexRequest, complexTemplateRefs, emptyComplex, STATUS_TONE as COMPLEX_TONE, symmetryOptions, type ComplexState } from "../lib/complex";
import { useProject, useToast } from "../lib/context";
import { modelingAccession } from "../lib/disorder";
import { modelingPreflight } from "../lib/preflight";
import { LaunchButton, PreflightPanel, usePreflight } from "../components/Preflight";
import { fileStem, formatRestraints, parseRestraints, type RestraintRow } from "../lib/files";
import { downloadText, fmtNum, gradeTone, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { parsePir } from "../lib/pir";
import { clashTone, favoredTone, gFactorTone, GRADE_RULE, outlierTone, prosaTone, zdopeTone } from "../lib/qualityView";
import { dopePerResidue, plural } from "../lib/shellView";

const REFINEMENT = [
  { value: "very_fast", label: "Rapidissimo", note: "Test o screening: ottimizzazione minima senza dinamica; i modelli sono tutti uguali (ne basta uno)." },
  { value: "fast", label: "Rapido", note: "Impostazioni predefinite di MODELLER: buon compromesso per molti modelli." },
  { value: "normal", label: "Standard", note: "Dinamica di raffinamento più lunga (refine.fast): circa 1,4× il tempo di Rapido." },
  { value: "thorough", label: "Accurato", note: "Ottimizzazione ripetuta, dinamica lenta: circa 4× il tempo di Rapido." },
];

type Restraint = RestraintRow;
type Expanded = ReturnType<typeof useExpanded<string>>;

/** «Confronta…»: il modello (B) contro un template usato o un altro modello dello stesso job (A, riferimento). */
function CompareSelect({ jobId, model, models, templates }: { jobId: string; model: ModelEntry; models: ModelEntry[]; templates?: PreparedTemplate[] }) {
  const navigate = useNavigate();
  const targets = compareTargets(jobId, model.file, models, templates);
  if (model.failure || !targets.length) return null;
  return (
    <select className="input input-sm" style={{ width: 118, display: "inline-block" }} value="" title="Confronto strutturale del modello (GDT, lDDT, deviazione per residuo)"
      onChange={(e) => { const t = targets[Number(e.target.value)]; if (t) navigate(t.href); }}>
      <option value="">Confronta…</option>
      {targets.map((t, i) => <option key={t.href} value={i}>con {t.label}</option>)}
    </select>
  );
}

/** Valore con il colore dell'esito (soglie di lib/qualityView); senza esito il solo numero. */
function Toned({ tone, children }: { tone?: "ok" | "warn" | "err" | "info"; children: string }) {
  return tone ? <Badge tone={tone}>{children}</Badge> : <>{children}</>;
}

/** Dettaglio di un modello (riga espansa): energie, qualità, ligandi e interfacce, template usati. */
function ModelDetail({ model: m, templates }: { model: ModelEntry; templates?: PreparedTemplate[] }) {
  const q = m.quality;
  const perResidue = dopePerResidue(m);
  const items: { label: string; value: ReactNode }[] = [
    { label: "DOPE", value: fmtNum(m.dope, 1) },
    { label: "DOPE per residuo", value: perResidue === null ? "—" : fmtNum(perResidue, 2) },
    { label: "zDOPE (normalizzato)", value: <Toned tone={zdopeTone(m.zdope)}>{fmtNum(m.zdope, 2)}</Toned> },
    { label: "GA341", value: <Toned tone={m.ga341 == null ? undefined : m.ga341 >= 0.7 ? "ok" : "warn"}>{fmtNum(m.ga341 ?? null, 2)}</Toned> },
    { label: "molpdf", value: fmtNum(m.molpdf, 1) },
  ];
  if (q) {
    items.push(
      { label: "Voto BioLoom", value: <span className="row" style={{ gap: 6 }}><Grade grade={q.grade} /> {fmtNum(q.grade_score, 1)}/100</span> },
      { label: "Ramachandran favorite", value: <Toned tone={favoredTone(q.rama_favored)}>{`${fmtNum(q.rama_favored)}%`}</Toned> },
      { label: "Ramachandran outlier", value: <Toned tone={outlierTone(q.rama_outliers)}>{`${fmtNum(q.rama_outliers)}%`}</Toned> },
      { label: "Clashscore", value: <Toned tone={clashTone(q.clashscore)}>{fmtNum(q.clashscore, 2)}</Toned> },
      { label: "Struttura secondaria", value: `${fmtNum(q.helix_pct, 0)}% elica · ${fmtNum(q.strand_pct, 0)}% filamento β` },
      { label: "Residui", value: String(q.residues) },
    );
    if (q.prosa_z != null) items.push({ label: "ProSA II z-score", value: <Toned tone={prosaTone(q.prosa_z)}>{fmtNum(q.prosa_z, 2)}</Toned> });
    if (q.procheck_core != null) {
      items.push({
        label: "PROCHECK",
        value: <>{fmtNum(q.procheck_core)}% core · G-factor <Toned tone={gFactorTone(q.procheck_g_overall)}>{fmtNum(q.procheck_g_overall ?? null, 2)}</Toned></>,
      });
    }
  }
  if (m.ligands) {
    const c = m.ligands.counts;
    items.push({ label: "Ligandi verificati", value: `${plural(m.ligands.groups, "gruppo", "gruppi")}: ${c.buono ?? 0} buoni, ${c["da controllare"] ?? 0} da controllare, ${c.problematico ?? 0} problematici` });
  }
  if (m.complex) {
    items.push({ label: "Interfacce", value: `${plural(m.complex.interfaces, "interfaccia", "interfacce")} · ${m.complex.status}${m.complex.dockq != null ? ` · DockQ minimo ${fmtNum(m.complex.dockq, 2)}` : ""}` });
  }
  if (templates?.length) items.push({ label: "Template usati", value: templates.map((t) => `${t.code} (${t.first}–${t.last})`).join(", ") });
  items.push({ label: "File", value: <span className="mono">{m.file}</span> });
  return (
    <div className="shell-peek">
      {m.failure && <Alert tone="err">{m.failure}</Alert>}
      <DetailGrid items={items} />
      {!q && !m.failure && <span className="small text-2">Analisi di qualità non calcolata per questo modello: «Qualità» nella riga la esegue.</span>}
    </div>
  );
}

const MODEL_COLUMNS = 8;

function ModelsTable({ jobId, models, selected, onSelect, templates, viewer, onRefine, onMutate, open }: {
  jobId: string;
  models: ModelEntry[];
  selected: string;
  onSelect: (f: string) => void;
  templates?: PreparedTemplate[];
  viewer?: RefObject<Viewer3DHandle | null>;
  onRefine?: (file: string) => void;
  onMutate?: (file: string) => void;
  /** righe espanse, condivise con «Apri tutte» nell'intestazione del riquadro */
  open: Expanded;
}) {
  const navigate = useNavigate();
  return (
    <div className="table-wrap shell-models">
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 34 }} aria-label="Dettagli" />
            <th>Modello</th>
            <th className="num" title="Energia statistica DOPE: più negativa è migliore">DOPE</th>
            <th className="num" title="DOPE normalizzato: &lt; −1 simile a strutture native">zDOPE</th>
            <th className="num" title="GA341 &gt; 0,7: fold affidabile">GA341</th>
            <th className="num" title="Funzione obiettivo di MODELLER: più bassa è migliore">molpdf</th>
            <th>Qualità</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {models.map((m) => {
            const isOpen = open.has(m.file);
            return (
              <Fragment key={m.file}>
                {/* riga del modello: clic, oppure Tab e Invio/Spazio, lo mostra nel 3D (un modello fallito non si sceglie) */}
                <tr className={`${m.failure ? "" : "clickable"}${m.file === selected ? " selected" : ""}${isOpen ? " row-open" : ""}`}
                  aria-current={m.file === selected || undefined} {...(m.failure ? {} : rowActivate(() => onSelect(m.file)))}>
                  <td><ExpandButton open={isOpen} onClick={() => open.toggle(m.file)} label={`dettagli di ${m.file}`} /></td>
                  <td className="nowrap">
                    <b>{m.file}</b> {m.best && <Badge tone="ok">migliore</Badge>} {m.kind === "loop" && <Badge tone="accent">loop</Badge>}
                    {m.failure && <div className="tiny" style={{ color: "var(--err)" }}>{m.failure}</div>}
                  </td>
                  <td className="num">{fmtNum(m.dope, 1)}</td>
                  <td className="num">{fmtNum(m.zdope, 2)}</td>
                  <td className="num">{fmtNum(m.ga341, 2)}</td>
                  <td className="num">{fmtNum(m.molpdf, 1)}</td>
                  <td>
                    {m.quality ? <span className="row" style={{ gap: 6 }}><Grade grade={m.quality.grade} /> <span className="shell-sub">{fmtNum(m.quality.rama_favored)}% fav.</span></span> : <span className="shell-sub">—</span>}
                    {m.ligands && <Badge tone={STATUS_TONE[m.ligands.status]} title="Verifica dei ligandi trasferiti (contatti, scontri, coordinazione)">ligandi: {m.ligands.status}</Badge>}
                    {m.complex && <Badge tone={COMPLEX_TONE[m.complex.status]} title={`Interfacce confrontate con il template${m.complex.dockq != null ? `: DockQ minimo ${fmtNum(m.complex.dockq, 2)}` : ""}`}>interfacce: {m.complex.status}</Badge>}
                  </td>
                  <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                    <span className="shell-actions">
                      <button type="button" className="btn btn-sm" disabled={!!m.failure} title="Analisi di qualità completa" aria-label={`Qualità di ${m.file}`}
                        onClick={() => navigate(`/quality?job=${jobId}&file=${encodeURIComponent(m.file)}`)}>
                        <Icon name="shield" size={13} />
                      </button>
                      <a className="btn btn-sm" href={api.jobFileUrl(jobId, m.file, true)} title="Scarica PDB" aria-label={`Scarica ${m.file}`}><Icon name="download" size={13} /></a>
                      <a className="btn btn-sm" href={api.pymolUrl(jobId, m.file)} title="Sessione PyMOL (.pml)">PyMOL</a>
                      <CompareSelect jobId={jobId} model={m} models={models} templates={templates} />
                      {/* il viewer mostra il modello selezionato: solo il suo report riceve la vista 3D */}
                      <ReportButton jobId={jobId} model={m.file} viewer={m.file === selected ? viewer : undefined} disabled={!!m.failure} />
                      {onRefine && (
                        <button type="button" className="btn btn-sm" disabled={!!m.failure} title="Rifinitura energetica con OpenMM (minimizzazione con restrizioni sulla catena principale)" onClick={() => onRefine(m.file)}>
                          <Icon name="flask" size={13} /> Rifinisci
                        </button>
                      )}
                      {onMutate && (
                        <button type="button" className="btn btn-sm" disabled={!!m.failure} title="Mutagenesi in silico o scansione di alanina del modello (MODELLER), con la stima dell'effetto" onClick={() => onMutate(m.file)}>
                          <Icon name="dna" size={13} /> Mutagenesi
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
                {isOpen && (
                  <tr className="row-detail">
                    <td colSpan={MODEL_COLUMNS}><ModelDetail model={m} templates={templates} /></td>
                  </tr>
                )}
              </Fragment>
            );
          })}
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
  const [mutate, setMutate] = useState<string | null>(null); // modello per la mutagenesi (F9.2)
  // complesso (F5.3): catene e interfacce del modello mostrato colorate nel 3D
  const [complexView, setComplexView] = useState<ComplexView | null>(null);
  const viewer = useRef<Viewer3DHandle>(null);
  const best = result.models.find((m) => m.best);
  const full = kind === "model" ? (result as ModelResult) : null;
  const open = useExpanded<string>();
  const q = best?.quality;
  const gTone = gradeTone(q?.grade);
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
      {best && q && (
        <div className="grid grid-4">
          <Stat label="Voto del modello migliore" tone={gTone === "muted" ? undefined : gTone}
            value={<span className="row"><Grade grade={q.grade} large /> <span>{fmtNum(q.grade_score, 1)}<small>/100</small></span></span>}
            detail={<><span>{GRADE_RULE}.</span><span>Il voto parte da 100 e toglie punti per gli outlier di Ramachandran, i contatti sterici e lo zDOPE sopra −1; il dettaglio è nella pagina Qualità.</span></>} />
          <Stat label="Ramachandran" tone={favoredTone(q.rama_favored)} value={<>{fmtNum(q.rama_favored)}<small>% favorite</small></>} note={`${fmtNum(q.rama_outliers)}% outlier`}
            detail={<><span>Favorite: obiettivo oltre il 98% (MolProbity), sotto il 90% problematico.</span><span>Outlier: fino allo 0,5% nessuna penalità, oltre il 2% problematico.</span></>} />
          <Stat label="Clashscore" tone={clashTone(q.clashscore)} value={fmtNum(q.clashscore, 2)} note="contatti gravi / 1000 atomi"
            detail={<span>Contatti sterici gravi tra atomi pesanti ogni 1000 atomi: fino a 5 buono, oltre 20 problematico.</span>} />
          <Stat label="DOPE" tone={zdopeTone(best.zdope)} value={fmtNum(best.dope, 0)} note={`zDOPE ${fmtNum(best.zdope, 2)} · GA341 ${fmtNum(best.ga341, 2)}`}
            detail={<><span>DOPE: energia statistica, si confronta tra modelli della stessa sequenza (più negativa è migliore).</span><span>zDOPE sotto −1: simile alle strutture native; sopra 0 probabilmente scarso. GA341 da 0,7: fold affidabile.</span></>} />
        </div>
      )}
      {q && (q.prosa_z != null || q.procheck_core != null) && (
        <div className="grid grid-4">
          <Stat label="ProSA II z-score" tone={prosaTone(q.prosa_z)} value={fmtNum(q.prosa_z ?? null, 2)} note="energia del ripiegamento"
            detail={<span>Confrontato con le strutture sperimentali di lunghezza simile: intorno a −6 o meno buono, sopra −2 segnala problemi (dipende dalla lunghezza).</span>} />
          <Stat label="PROCHECK core" tone={gFactorTone(q.procheck_g_overall)} value={<>{fmtNum(q.procheck_core ?? null)}<small>%</small></>} note={`G-factor ${fmtNum(q.procheck_g_overall ?? null, 2)}`}
            detail={<span>Residui nelle regioni più favorite del grafico di Ramachandran di PROCHECK; G-factor globale sopra −0,5 normale, sotto −1 insolito.</span>} />
        </div>
      )}
      <Card
        icon="cube"
        title={`${result.models.length} modelli`}
        sub="Il migliore ha il DOPE più basso; clic su una riga per vederlo nel 3D, freccia per tutti i valori"
        right={
          <>
            {result.models.length > 1 && (
              <ExpandAll allOpen={open.size === result.models.length} onExpand={() => open.expandAll(result.models.map((m) => m.file))} onCollapse={open.collapseAll} noun="righe dei modelli" />
            )}
            <button type="button" className="btn btn-sm" onClick={() => setLoop(true)}><Icon name="wand" size={14} /> Raffina un loop</button>
            <a className="btn btn-sm" href={api.archiveUrl(jobId)}><Icon name="download" size={14} /> Archivio completo</a>
          </>
        }
      >
        <div className="stack">
          <ModelsTable jobId={jobId} models={result.models} selected={selected} onSelect={setSelected} templates={full?.templates} viewer={viewer} onRefine={setRefine} onMutate={setMutate} open={open} />
          <ModelScores models={result.models} selected={selected} onSelect={setSelected} filename={`punteggi_modelli_${jobId.slice(0, 8)}`} />
        </div>
      </Card>
      {full?.ligands && (
        <Card icon="flask" title="Ligandi, cofattori e metalli trasferiti" sub="verifica del modello migliore; il dettaglio e la vista 3D sono nella pagina Qualità"
          collapsible storageKey="modeling.ligands">
          <div className="stack-sm">
            <div className="small text-2">
              Trasferiti: {full.ligands.transferred.length ? full.ligands.transferred.map((g) => `${groupTitle(g)} (${g.label}, da ${g.template})`).join(", ") : "nessuno"}
            </div>
            {full.ligands.verification ? <LigandTable report={full.ligands.verification} /> : <Alert tone="warn">Nessun gruppo eteroatomico nel modello migliore.</Alert>}
          </div>
        </Card>
      )}
      {full?.complex && <ComplexResults jobId={jobId} complex={full.complex} selected={selected} best={result.best_model} onView={setComplexView} />}
      <Card icon="cube" title={`Struttura 3D · ${selected}`} sub="Il report del modello selezionato include questa vista">
        <Viewer3D url={api.jobFileUrl(jobId, selected)} handle={viewer} coloring={complexView?.coloring ?? null} highlight={complexView?.highlight ?? []} centerKey={complexView?.centerKey} />
      </Card>
      {/* controllo di plausibilità del modello selezionato con una predizione esterna (F9.3) */}
      <PredictionPanel key={selected} source={{ type: "job", job_id: jobId, file: selected }} label={selected} residues={full && !full.complex ? full.target.sequence.length : null} />
      {full && (
        <Card icon="align" title="Allineamento usato da MODELLER" collapsible storageKey="modeling.alignment"
          sub={plural(full.templates.length, "template", "template")}
          right={<button type="button" className="btn btn-sm" onClick={savePir}><Icon name="save" size={14} /> Salva PIR nel progetto</button>}>
          <div className="stack-sm">
            <div className="small text-2">
              Template: {full.templates.map((t) => `${t.code} (${t.first}–${t.last}${t.ligands ? `, ${t.ligands} ligandi` : ""}${t.waters ? `, ${t.waters} ${t.waters === 1 ? "acqua" : "acque"}` : ""})`).join(", ")}
            </div>
            {full.templates.filter((t) => t.predicted).map((t) => (
              <div key={t.code} className="small row" style={{ gap: 6 }}>
                <Badge tone="warn" title={predictorTitle(t)}>{predictorBadge(t)}</Badge>
                <span className="text-2">{t.code}: {trimSummary(t)}{removedRanges(t) && <span> · tolti {removedRanges(t)}</span>}</span>
              </div>
            ))}
            {entries.length > 1 && <AlignmentView ids={entries.map((e) => e.code)} aligned={entries.map((e) => e.sequence)} reference={pirReference(entries.map((e) => e.kind))} />}
          </div>
        </Card>
      )}
      {loop && <LoopDialog source={{ type: "job", job_id: jobId, file: selected }} label={selected} onClose={() => setLoop(false)} />}
      {refine && <RefineDialog source={{ type: "job", job_id: jobId, file: refine }} label={refine} onClose={() => setRefine(null)} />}
      {mutate && <MutateDialog source={{ type: "job", job_id: jobId, file: mutate }} label={mutate} onClose={() => setMutate(null)} />}
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
  const [mutateOpen, setMutateOpen] = useState(false); // mutagenesi della struttura scelta (F9.2)
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
  }, [projectId, mode, target, nModels, refinement, seed, loopOn, loopModels, loopRegions, restraints, plddtTrim, plddtMin, ligandsOn, ligandRefs, quality, extended, activeTemplates, complexState, complexCheck]);
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
    // una risposta arrivata dopo il cambio di progetto (o dopo l'uscita dalla pagina) non sostituisce la sequenza
    let cancelled = false;
    if (seqId && projectId) {
      api.sequences(projectId)
        .then((l: Sequence[]) => {
          const s = l.find((x) => x.id === seqId);
          if (s && !cancelled) setTarget({ name: s.name, sequence: s.sequence });
        })
        .catch((e) => {
          if (!cancelled) toast.error(e);
        });
    }
    if (t.length) setTemplates(t.map((x) => { const [id, ch] = x.split(":"); return { id, chains: ch ? ch.split("") : [] }; }));
    if (params.get("lig") === "1") setLigandsOn(true); // esempi con HETATM e acque: riquadro dei ligandi aperto
    if (doc) {
      setMode("pir");
      setDocId(doc);
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- link letto all'apertura e all'arrivo del progetto: rileggerlo a ogni cambio dell'URL annullerebbe le modifiche
  }, [projectId]);

  useEffect(() => {
    if (mode !== "pir" || !docId || !projectId) return;
    let cancelled = false; // documento cambiato prima della risposta: i template restano quelli del documento nuovo
    api.document(projectId, docId).then((d) => {
      const tm = (d.meta?.templates as TemplateRef[] | undefined) ?? [];
      if (tm.length && !cancelled) setTemplates(tm);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
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
  // riepilogo delle opzioni avanzate accanto al titolo (visibile anche a sezione chiusa)
  const advancedSummary = [loopOn && "loop", restraints.length && plural(restraints.length, "vincolo", "vincoli"), `seme ${seed}`].filter(Boolean).join(" · ");
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
          <Card icon="dna" title="Bersaglio e template">
            <div className="stack">
              <Segmented label="Modalità di modellazione" value={mode} onChange={setMode} options={[{ value: "auto", label: "Allineamento automatico" }, { value: "pir", label: "Da documento PIR" }, { value: "complex", label: "Complesso" }]} />
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
                    <span className="small text-2">viene salvato tra i documenti del progetto</span>
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
          <Card icon="settings" title="Parametri">
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
                <Segmented label="Raffinamento" value={refinement} onChange={setRefinement} options={REFINEMENT.map((r) => ({ value: r.value, label: r.label }))} />
              </Field>
              {predictedChosen && <PlddtOption enabled={plddtTrim} threshold={plddtMin} onEnabled={setPlddtTrim} onThreshold={setPlddtMin} />}
              <LigandPicker templates={activeTemplates} known={templateInfo} enabled={ligandsOn} onEnabled={setLigandsOn} selection={ligandSel}
                onSelection={(sel, refs, ready, failed) => { setLigandSel(sel); setLigandRefs(refs); setLigandsReady(ready); setLigandsFailed(failed); }} />
              <Disclosure title="Opzioni avanzate" extra={advancedSummary}>
                <div className="stack">
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
                          <button type="button" className="btn btn-sm" onClick={() => downloadText("vincoli_bioloom.txt", formatRestraints(restraints))}><Icon name="download" size={13} /> Esporta</button>
                        )}
                        <button type="button" className="btn btn-sm" onClick={() => setRestraints([...restraints, { type: "helix", start: "", end: "" }])}><Icon name="plus" size={13} /> Vincolo</button>
                      </div>
                    </div>
                    {restraints.map((r, i) => (
                      <div key={i} className="shell-restraint">
                        <select className="input" style={{ width: 120 }} value={r.type} aria-label={`Tipo del vincolo ${i + 1}`}
                          onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { type: e.target.value as Restraint["type"] } : x)))}>
                          <option value="helix">α-elica</option>
                          <option value="strand">filamento β</option>
                          <option value="distance">distanza</option>
                        </select>
                        {r.type === "distance" ? (
                          <>
                            <input className="input" style={{ width: 100 }} placeholder="CA:10:A" aria-label="Primo atomo" value={r.atom1 ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, atom1: e.target.value } : x)))} />
                            <input className="input" style={{ width: 100 }} placeholder="CA:50:A" aria-label="Secondo atomo" value={r.atom2 ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, atom2: e.target.value } : x)))} />
                            <input className="input" style={{ width: 70 }} type="number" placeholder="Å" aria-label="Distanza (Å)" value={r.mean ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, mean: Number(e.target.value), stdev: x.stdev ?? 0.5 } : x)))} />
                          </>
                        ) : (
                          <>
                            <input className="input" style={{ width: 90 }} placeholder="10:A" aria-label="Primo residuo" value={r.start ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, start: e.target.value } : x)))} />
                            <input className="input" style={{ width: 90 }} placeholder="20:A" aria-label="Ultimo residuo" value={r.end ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, end: e.target.value } : x)))} />
                          </>
                        )}
                        <button type="button" className="btn btn-ghost btn-icon" aria-label={`Togli il vincolo ${i + 1}`} title="Togli il vincolo" onClick={() => setRestraints(restraints.filter((_, k) => k !== i))}>
                          <Icon name="x" size={14} />
                        </button>
                      </div>
                    ))}
                    <span className="small text-2">
                      Residui nella numerazione del modello (es. 10:A); atomi come NOME:residuo:catena. Da file: una riga per vincolo,
                      es. <span className="mono">helix 10:A 20:A</span>, <span className="mono">strand 30:A 35:A</span>, <span className="mono">distance CA:10:A CA:50:A 8.0 0.5</span>.
                    </span>
                  </div>
                  <Field label="Seme casuale MODELLER" hint="Tra −50000 e −2: cambia per ottenere modelli diversi."><input className="input" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></Field>
                </div>
              </Disclosure>
              {/* controlli preventivi: disordine e domini, template, licenze, tempi, spazio (errori bloccano, avvisi con conferma) */}
              <PreflightPanel state={preflight} />
              <LaunchButton preflight={preflight} disabled={!canSubmit || !!modellerMissing} onLaunch={submit}>Avvia modellazione</LaunchButton>
              {mode !== "complex" && templates.some((t) => !t.chains.length) && <span className="small" style={{ color: "var(--warn)" }}>Seleziona almeno una catena per ogni template.</span>}
              {mode === "complex" && !complexCheck.request && complexCheck.errors[0] && <span className="small" style={{ color: "var(--warn)" }}>{complexCheck.errors[0]}</span>}
              {ligandsOn && !ligandsReady && activeTemplates.length > 0 && (ligandsFailed.length
                ? <span className="small" style={{ color: "var(--warn)" }}>Ligandi di {ligandsFailed.join(", ")} non letti: usa «Riprova» nel riquadro dei ligandi oppure togli la spunta.</span>
                : <span className="small text-2">Lettura dei ligandi dei template in corso…</span>)}
            </div>
          </Card>
          <Card icon="wand" title="Raffinamento dei loop e rifinitura energetica" sub="di un modello, un template o un file PDB/mmCIF">
            <div className="stack-sm">
              <StructurePicker label="Struttura" value={loopSource} onChange={setLoopSource} />
              <div className="row">
                <button type="button" className="btn btn-sm" disabled={!loopSource || !!modellerMissing} onClick={() => setLoopOpen(true)}>
                  <Icon name="wand" size={13} /> Scegli il loop da raffinare
                </button>
                <button type="button" className="btn btn-sm" disabled={!loopSource} onClick={() => setRefineOpen(true)} title="Minimizzazione con OpenMM e restrizioni sulla catena principale">
                  <Icon name="flask" size={13} /> Rifinitura energetica (OpenMM)
                </button>
                <button type="button" className="btn btn-sm" disabled={!loopSource || !!modellerMissing} onClick={() => setMutateOpen(true)} title="Mutazioni puntuali o multiple, varianti di UniProt e scansione di alanina con MODELLER">
                  <Icon name="dna" size={13} /> Mutagenesi e scansione di alanina
                </button>
              </div>
            </div>
          </Card>
          {mutateOpen && loopSource && (
            <MutateDialog source={loopSource} label={loopSource.id ?? loopSource.file ?? "struttura"} onClose={() => setMutateOpen(false)} />
          )}
          {loopOpen && loopSource && (
            <LoopDialog source={loopSource} label={loopSource.id ?? loopSource.file ?? "struttura"} onClose={() => setLoopOpen(false)} />
          )}
          {refineOpen && loopSource && (
            <RefineDialog source={loopSource} label={loopSource.id ?? loopSource.file ?? "struttura"} onClose={() => setRefineOpen(false)} />
          )}
          <Card icon="activity" title="Modellazioni recenti">
            {!recent?.length ? <p className="small text-2">Nessuna.</p> : (
              <div className="shell-recent">
                {recent.map((r) => (
                  <button key={r.id} type="button" className={r.id === jobId ? "on" : ""} aria-current={r.id === jobId || undefined} onClick={() => setParams({ job: r.id })}>
                    <StatusBadge status={r.status} />
                    <span className="ellipsis" title={r.title}>{r.title}</span>
                    <span className="when">{relTime(r.created_at)}</span>
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
            : job.kind === "mutate" ? <MutationResults key={job.id} jobId={job.id} result={job.result as unknown as MutateResult} />
              : job.kind === "alascan" ? <AlaScanResults key={job.id} jobId={job.id} result={job.result as unknown as AlaScanResult} />
                : <Results key={job.id} jobId={job.id} result={job.result} kind={job.kind} />)}
        </div>
      </div>
    </div>
  );
}
