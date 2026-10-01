import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  Align2dResult, DatabaseStatus, DocumentItem, LicenseStatus, LigandRef, ModelResult, QualityReport, SearchResult, Sequence, Settings,
  TemplateAnalysis, TemplateItem, TemplateRef, UniProtEntry,
} from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { FeatureMap } from "../components/charts";
import { useCoverageCheck } from "../components/CoverageAlerts";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { LigandPicker } from "../components/LigandPicker";
import { SequencePicker } from "../components/Pickers";
import { PlddtOption } from "../components/PlddtOption";
import { LaunchButton, PreflightPanel, usePreflight } from "../components/Preflight";
import { ReportButton } from "../components/ReportButton";
import { Alert, Badge, BarCell, Card, Empty, Field, Grade, PageHead, Segmented, Spinner, Stat } from "../components/ui";
import { Viewer3D, type Viewer3DHandle } from "../components/Viewer3D";
import { pirReference } from "../lib/alignConfidence";
import { hasPredictedTemplate, plddtLabel, plddtTone, sequenceAccession } from "../lib/alphafold";
import { compareTargets } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { disorderSummary, featureTracks, lowComplexitySummary } from "../lib/disorder";
import { fmtNum } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { defaultTransfer } from "../lib/ligands";
import { parsePir } from "../lib/pir";
import { rankTone, rankTooltip } from "../lib/templateRank";
import {
  accessionFor, accessionValid, align2dRequest, applyUrl, cleanSequence, encodeWizardParams, engineChoices, fillFromJobs, hitRole, initialState,
  isChosen, MAX_TEMPLATES, missingModelJob, modelRequest, nextEngineHint, optionNotes, optionsIssues, preflightFor, preselect, rankedHits, reachable,
  reasonedOptions, restoreState, resultSummary, searchRequest, sequenceIssues, STEPS, templatesIssues, toggleTemplate, withAccession,
  withAlignJob, withModelJob, withOptions, withPirDoc, withSearchJob, withTarget, withTemplates, type OptionContext, type WizardState,
  type WizardStep,
} from "../lib/wizard";

const REFINEMENTS = [
  { value: "very_fast", label: "Rapidissimo" },
  { value: "fast", label: "Rapido" },
  { value: "normal", label: "Standard" },
  { value: "thorough", label: "Accurato" },
] as const;

function readDraft(key: string): unknown {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeDraft(key: string, state: WizardState) {
  try {
    localStorage.setItem(key, JSON.stringify(state));
  } catch {
    /* storage non disponibile: la procedura resta nell'URL (job) */
  }
}

function Stepper({ step, max, onGo }: { step: WizardStep; max: WizardStep; onGo: (s: WizardStep) => void }) {
  return (
    <div className="row" style={{ gap: 6, flexWrap: "wrap" }} role="navigation" aria-label="Passi della procedura">
      {STEPS.map((s) => {
        const current = s.step === step;
        const open = s.step <= max;
        return (
          <button key={s.step} className={`btn btn-sm ${current ? "btn-primary" : "btn-ghost"}`} disabled={!open} aria-current={current ? "step" : undefined}
            title={s.hint} onClick={() => onGo(s.step)} style={{ height: "auto", padding: "6px 10px" }}>
            <span className={`grade ${s.step < step ? "grade-ok" : current ? "grade-info" : "grade-muted"}`} style={{ width: 22, height: 22, fontSize: 12 }}>
              {s.step < step && s.step < max ? <Icon name="check" size={12} /> : s.step}
            </span>
            {s.title}
          </button>
        );
      })}
    </div>
  );
}

export default function Wizard() {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const draftKey = `bioloom.wizard.${projectId ?? "-"}`;
  const [state, setState] = useState<WizardState>(() => restoreState(params, readDraft(draftKey)));
  const stateRef = useRef(state);
  stateRef.current = state;

  // progetto cambiato: la sua bozza (sequenze, documenti e job sono del progetto)
  const firstKey = useRef(draftKey);
  useEffect(() => {
    if (firstKey.current === draftKey) return;
    firstKey.current = draftKey;
    const next = restoreState(new URLSearchParams(), readDraft(draftKey));
    setState(next);
    setParams(encodeWizardParams(next), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey]);
  // URL → stato (link, indietro e avanti del browser)
  const urlKey = params.toString();
  useEffect(() => {
    setState((s) => applyUrl(s, new URLSearchParams(urlKey)));
  }, [urlKey]);
  // stato → bozza nel browser e URL (senza voci nuove nella cronologia: quelle le crea `go`)
  useEffect(() => {
    writeDraft(draftKey, state);
    const next = encodeWizardParams(state).toString();
    if (next !== urlKey) setParams(new URLSearchParams(next), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, draftKey]);

  const update = useCallback((fn: (s: WizardState) => WizardState) => setState((s) => fn(s)), []);
  /** Va a un passo con una voce nuova nella cronologia (si torna indietro con il browser). */
  const go = useCallback((step: WizardStep, fn?: (s: WizardState) => WizardState) => {
    const next = { ...(fn ? fn(stateRef.current) : stateRef.current), step };
    setState(next);
    setParams(encodeWizardParams(next));
  }, [setParams]);

  const search = useJob<SearchResult>(state.searchJob, { withLog: true });
  const align = useJob<Align2dResult>(state.alignJob, { withLog: true });
  const model = useJob<ModelResult>(state.modelJob, { withLog: true });
  // da un link senza bozza: bersaglio, template e opzioni dai job
  useEffect(() => {
    update((s) => fillFromJobs(s, { search: search.job, align: align.job, model: model.job }));
  }, [search.job, align.job, model.job, update]);
  // ricerca completata: i consigliati preselezionati (una volta per ricerca)
  useEffect(() => {
    const j = search.job;
    if (j?.status === "completed" && j.result) update((s) => (s.searchJob === j.id ? preselect(s, j.id, j.result!) : s));
  }, [search.job, update]);

  const done = {
    align: align.job?.id === state.alignJob && align.job?.status === "completed",
    model: model.job?.id === state.modelJob && model.job?.status === "completed",
  };
  const loaded = (!state.alignJob || !!align.job || !!align.error) && (!state.modelJob || !!model.job || !!model.error);
  const modelMissing = missingModelJob(!!model.summary, model.error, model.gone);
  const max = reachable(state, done);
  const step: WizardStep = loaded ? (Math.min(state.step, max) as WizardStep) : state.step;
  // modellazione finita mentre si guarda l'avanzamento: ai risultati. Solo se il job era in corso: tornando al passo
  // 5 con il browser (indietro) a job già concluso si resta lì, altrimenti «indietro» non uscirebbe mai dal passo 6
  const sawRunning = useRef(false);
  if (model.summary && ["queued", "running", "cancelling"].includes(model.summary.status)) sawRunning.current = true;
  useEffect(() => {
    if (state.step === 5 && done.model && sawRunning.current) {
      sawRunning.current = false;
      go(6);
    }
  }, [state.step, done.model, go]);
  // passo dell'URL non ancora raggiungibile (link vecchio, job eliminato): il più avanzato possibile
  useEffect(() => {
    if (loaded && state.step > max) update((s) => ({ ...s, step: max }));
  }, [loaded, state.step, max, update]);

  const reset = () => {
    const next = initialState();
    setState(next);
    setParams(encodeWizardParams(next));
  };

  return (
    <div className="page">
      <PageHead
        title="Nuovo modello guidato"
        lead="Dalla sequenza al modello validato in sei passi: sequenza, ricerca dei template, allineamento, opzioni, avvio e risultati. Prima di ogni calcolo BioLoom controlla licenze, template, tempi e spazio. Lo stato resta nell'indirizzo della pagina e nel browser: si può ricaricare, tornare indietro o riprendere più tardi."
        actions={<button className="btn" onClick={reset}><Icon name="refresh" size={14} /> Ricomincia</button>}
      />
      <Stepper step={step} max={max} onGo={(s) => go(s)} />
      {!loaded && <p className="small muted row" style={{ gap: 6 }}><Spinner /> Ripresa della procedura…</p>}
      {step === 1 && <SequenceStep state={state} update={update} onNext={() => go(2)} />}
      {step === 2 && <TemplateStep state={state} update={update} search={search} projectId={projectId} onNext={() => go(3)} />}
      {step === 3 && <AlignmentStep state={state} update={update} align={align} projectId={projectId} onNext={() => go(4)} />}
      {step === 4 && (
        <OptionsStep state={state} update={update} align={align.job?.id === state.alignJob ? align.job?.result ?? null : null} projectId={projectId}
          onLaunched={(id) => go(5, (s) => withModelJob(s, id))} />
      )}
      {step === 5 && (
        <Card title="5. Costruzione dei modelli" sub="avanzamento in tempo reale; il calcolo prosegue anche chiudendo la pagina">
          <div className="stack">
            {model.summary ? <JobPanel summary={model.summary} log={model.log} onCancel={model.cancel} defaultLogOpen />
              : modelMissing ? (
                <>
                  <Alert tone="err">{modelMissing}</Alert>
                  <div className="row">
                    <span className="small text-2">Rilancia la modellazione dalle opzioni: sequenza, template e allineamento restano.</span>
                    <button className="btn btn-sm right" onClick={() => go(4, (s) => withModelJob(s, null))}><Icon name="chevron" size={13} /> Torna alle opzioni</button>
                  </div>
                </>
              ) : <Spinner />}
            {done.model && (
              <div className="row">
                <span className="small text-2">Modellazione completata.</span>
                <button className="btn btn-sm btn-primary right" onClick={() => go(6)}>Vai ai risultati <Icon name="chevron" size={13} /></button>
              </div>
            )}
            {(model.summary?.status === "failed" || model.summary?.status === "cancelled") && (
              <div className="row">
                <span className="small text-2">Correggi le opzioni o l'allineamento e riprova.</span>
                <button className="btn btn-sm right" onClick={() => go(4, (s) => withModelJob(s, null))}><Icon name="chevron" size={13} /> Torna alle opzioni</button>
              </div>
            )}
          </div>
        </Card>
      )}
      {step === 6 && model.job?.result && <ResultsStep jobId={model.job.id} result={model.job.result} onNew={() => { reset(); toast.push("Nuova procedura: parti dalla sequenza", "info"); }} navigate={navigate} />}
    </div>
  );
}

type Update = (fn: (s: WizardState) => WizardState) => void;
type JobHook<R> = ReturnType<typeof useJob<R>>;

// ---------------------------------------------------------------------------------- 1. sequenza
function SequenceStep({ state, update, onNext }: { state: WizardState; update: Update; onNext: () => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const { data: projectSeqs, reload } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const [uniprot, setUniprot] = useState("");
  const [busy, setBusy] = useState(false);
  const issues = sequenceIssues(state.target);
  const sequence = cleanSequence(state.target.sequence);
  const accessionOk = accessionValid(state.accession);
  const preview = useCoverageCheck(!issues.length && accessionOk ? { sequence, accession: state.accession || null } : null);
  const inProject = projectSeqs?.some((s) => cleanSequence(s.sequence) === sequence);
  const importUniprot = async () => {
    setBusy(true);
    try {
      const entry: UniProtEntry = await api.uniprot(uniprot.trim().toUpperCase());
      update((s) => withTarget(s, { name: entry.entry_name || entry.accession, sequence: entry.sequence }, entry.accession));
      toast.push(`${entry.accession}: ${entry.protein_name} (${entry.length} aa)`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!projectId) return;
    try {
      await api.addSequence(projectId, state.target.name || "target", sequence, state.accession ? "uniprot" : "manual", state.accession ? { accession: state.accession } : {});
      toast.push("Sequenza aggiunta al progetto");
      reload();
    } catch (e) {
      toast.error(e);
    }
  };
  const dis = preview.check?.disorder;
  return (
    <div className="split">
      <Card title="1. Sequenza bersaglio">
        <div className="stack">
          <SequencePicker
            value={state.target}
            onChange={(v) => {
              const seq = v.id ? projectSeqs?.find((s) => s.id === v.id) : undefined;
              update((s) => withTarget(s, { name: v.name, sequence: v.sequence }, seq ? sequenceAccession(seq) : accessionFor(s, v)));
            }}
          />
          <Field label="Oppure da UniProt" hint="Accessione UniProtKB (es. P0CG48): sequenza e accessione per domini e AlphaFold DB.">
            <div className="row">
              <input className="input" style={{ flex: 1, textTransform: "uppercase" }} placeholder="P0CG48" maxLength={20} value={uniprot}
                onChange={(e) => setUniprot(e.target.value)} onKeyDown={(e) => e.key === "Enter" && uniprot.trim() && importUniprot()} />
              <button className="btn" disabled={!uniprot.trim() || busy} onClick={importUniprot}>{busy ? <Spinner /> : <Icon name="download" size={14} />} Importa</button>
            </div>
          </Field>
          <Field label="Accessione UniProt del bersaglio (facoltativa)" hint={accessionOk ? "Con l'accessione: domini InterPro (a UniProt e InterPro va solo l'accessione) e modelli di AlphaFold DB." : <span style={{ color: "var(--err)" }}>Formato non valido (UniProtKB, es. P69905).</span>}>
            <input className="input" style={{ textTransform: "uppercase" }} maxLength={20} value={state.accession} onChange={(e) => update((s) => withAccession(s, e.target.value))} />
          </Field>
          {state.target.sequence && issues.map((m) => <Alert key={m} tone="err">{m}</Alert>)}
          <div className="row">
            {sequence && !issues.length && projectId && projectSeqs && !inProject && (
              <button className="btn btn-sm" onClick={save}><Icon name="plus" size={13} /> Aggiungi al progetto</button>
            )}
            <button className="btn btn-primary right" disabled={!!issues.length || !accessionOk} onClick={onNext}>Avanti: template <Icon name="chevron" size={13} /></button>
          </div>
        </div>
      </Card>
      <Card title="Anteprima: disordine, bassa complessità e domini" sub="dove il modello per omologia sarà poco affidabile">
        {!sequence || issues.length ? <Empty icon="dna" title="Inserisci una sequenza proteica" />
          : preview.error ? <Alert tone="warn">Anteprima non disponibile: {preview.error}</Alert>
          : !dis || !preview.check ? <Spinner />
          : (
            <div className="stack-sm">
              <FeatureMap length={dis.length} tracks={featureTracks({ disorder: dis, domains: preview.check.domains_info?.available ? preview.check.domains : null })} />
              <p className="small text-2">{disorderSummary(dis)} {lowComplexitySummary(dis)}</p>
              {preview.check.domains_info?.note && <p className="tiny muted">{preview.check.domains_info.note}</p>}
              <p className="tiny muted">
                {sequence.length} residui. Le regioni disordinate lunghe e i domini senza template di fronte saranno costruiti senza vincoli: i controlli
                del passo 4 li confrontano con i template scelti. {dis.method}, dalla sola sequenza (indicativo).
              </p>
            </div>
          )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------- 2. template
function TemplateStep({ state, update, search, projectId, onNext }: { state: WizardState; update: Update; search: JobHook<SearchResult>; projectId: string | null; onNext: () => void }) {
  const { data: databases, loading: dbLoading } = useAsync<DatabaseStatus | null>(() => api.databases().catch(() => null), []);
  const { data: settings, loading: settingsLoading } = useAsync<Settings | null>(() => api.settings().catch(() => null), []);
  const [all, setAll] = useState(false);
  const choices = engineChoices({ databases, defaultEngine: settings?.default_search_engine, accession: state.accession });
  // motore consigliato come scelta iniziale, finché l'utente non ha cercato
  const suggested = useRef(false);
  useEffect(() => {
    if (!suggested.current && !dbLoading && !settingsLoading && !state.searchJob) {
      suggested.current = true;
      if (choices.recommended !== state.engine) update((s) => ({ ...s, engine: choices.recommended }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dbLoading, settingsLoading]);
  const running = !!search.summary && ["queued", "running", "cancelling"].includes(search.summary.status);
  const request = searchRequest(state, projectId);
  const preflight = usePreflight(running ? null : preflightFor("search", state, request as unknown as Record<string, unknown>), 300);
  const result = search.job?.id === state.searchJob && search.job?.status === "completed" ? search.job.result : null;
  const hits = useMemo(() => (result ? rankedHits(result) : []), [result]);
  const shown = all ? hits : hits.slice(0, 20);
  const hint = result ? nextEngineHint(result, state.accession) : null;
  const issues = templatesIssues(state.templates);
  const launch = async () => {
    const j = await api.search(request);
    update((s) => withSearchJob(s, j.id));
  };
  return (
    <div className="split">
      <Card title="2. Ricerca dei template">
        <div className="stack">
          <p className="small text-2"><b>Consigliato.</b> {choices.reason}</p>
          <div className="stack-sm" role="radiogroup" aria-label="Motore di ricerca">
            {choices.choices.map((c) => (
              <label key={c.engine} className="check" style={{ alignItems: "flex-start", opacity: c.available ? 1 : 0.6 }}>
                <input type="radio" name="engine" disabled={!c.available} checked={state.engine === c.engine} onChange={() => update((s) => ({ ...s, engine: c.engine }))} />
                <span>
                  <b>{c.label}</b> {c.engine === choices.recommended && <Badge tone="ok">consigliato</Badge>}
                  <span className="tiny muted" style={{ display: "block" }}>{c.note}</span>
                </span>
              </label>
            ))}
          </div>
          <PreflightPanel state={preflight} />
          <LaunchButton preflight={preflight} disabled={running} onLaunch={launch}>{state.searchJob ? "Cerca di nuovo" : "Cerca template"}</LaunchButton>
          {state.searchJob && search.summary && <JobPanel summary={search.summary} log={search.log} onCancel={search.cancel} compact />}
        </div>
      </Card>
      <Card title={result ? `${result.hits.length} template trovati` : "Template"} sub={result ? `${result.engine_label} · scelti ${state.templates.length} (al massimo ${MAX_TEMPLATES})` : undefined}>
        {!result ? <Empty icon="search" title={running ? "Ricerca in corso…" : "Avvia la ricerca per vedere i template"} /> : (
          <div className="stack-sm">
            {hint && <Alert tone="warn">{hint}</Alert>}
            {!!result.notes?.length && result.notes.map((n) => <Alert key={n} tone="info">{n}</Alert>)}
            {!!result.suggested?.templates.length && (
              <p className="tiny text-2">
                Preselezionati i consigliati (copertura complessiva {fmtNum(result.suggested.coverage, 1)}%):{" "}
                {result.suggested.templates.map((t) => `${t.template_id} (${t.role}): ${t.reason}`).join(" · ")}
              </p>
            )}
            {!!hits.length && (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th /><th>Template</th><th className="num">Punteggio</th><th className="num">Identità</th><th className="num">Copertura</th><th className="num">Ris. (Å)</th></tr></thead>
                  <tbody>
                    {shown.map((h) => {
                      const role = hitRole(result, h);
                      const chosen = isChosen(state.templates, h);
                      return (
                        <tr key={h.template_id} className={`clickable ${chosen ? "selected" : ""}`} onClick={() => update((s) => withTemplates(s, toggleTemplate(s.templates, h)))}>
                          <td><input type="checkbox" readOnly checked={chosen} aria-label={`Usa ${h.template_id}`} /></td>
                          <td className="nowrap">
                            <b>{h.template_id}</b>{" "}
                            {role && <Badge tone={role === "principale" ? "ok" : "info"}>{role === "principale" ? "consigliato" : "complementare"}</Badge>}
                            {h.predicted && <> <Badge tone="warn" title="Modello di AlphaFold DB, non struttura sperimentale">predetto</Badge> <Badge tone={plddtTone(h.plddt)}>{plddtLabel(h.plddt)}</Badge></>}
                            <div className="tiny muted ellipsis" style={{ maxWidth: 260 }}>{h.title || h.description}</div>
                          </td>
                          <td className="num" title={rankTooltip(h)}>{h.rank_score !== undefined ? <Badge tone={rankTone(h.rank_score)}>{fmtNum(h.rank_score, 1)}</Badge> : "—"}{h.rank_twilight && <div className="tiny" style={{ color: "var(--warn)" }}>crepuscolare</div>}</td>
                          <td className="num"><BarCell value={h.identity} /></td>
                          <td className="num"><BarCell value={h.coverage} /></td>
                          <td className="num">{h.resolution ? fmtNum(h.resolution, 2) : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {hits.length > 20 && <button className="btn btn-ghost btn-sm" style={{ justifySelf: "start" }} onClick={() => setAll(!all)}>{all ? "Mostra i primi 20" : `Mostra tutti (${hits.length})`}</button>}
            <p className="tiny muted">Ordine per punteggio 0–100 (identità rispetto alla curva di Rost, copertura, risoluzione…): clic su una riga per sceglierla. Una catena per voce PDB.</p>
          </div>
        )}
        <div className="row" style={{ marginTop: 10 }}>
          {state.templates.length > 0 && issues.length === 0 && <span className="small text-2">Scelti: {state.templates.map((t) => `${t.id}:${t.chains.join("")}`).join(", ")}</span>}
          <button className="btn btn-primary right" disabled={!!issues.length} onClick={onNext}>Avanti: allineamento <Icon name="chevron" size={13} /></button>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------- 3. allineamento
function AlignmentStep({ state, update, align, projectId, onNext }: { state: WizardState; update: Update; align: JobHook<Align2dResult>; projectId: string | null; onNext: () => void }) {
  const { data: docs } = useAsync<DocumentItem[]>(() => (projectId ? api.documents(projectId) : Promise.resolve([])), [projectId]);
  const { data: doc } = useAsync<DocumentItem | null>(() => (projectId && state.pirDoc ? api.document(projectId, state.pirDoc) : Promise.resolve(null)), [projectId, state.pirDoc]);
  const running = !!align.summary && ["queued", "running", "cancelling"].includes(align.summary.status);
  const request = align2dRequest(state, projectId);
  const preflight = usePreflight(running ? null : preflightFor("align2d", state, request as unknown as Record<string, unknown>), 300);
  const result = align.job?.id === state.alignJob && align.job?.status === "completed" ? align.job.result : null;
  const shownPir = state.pirDoc ? doc?.content ?? "" : result?.pir ?? "";
  const entries = useMemo(() => parsePir(shownPir), [shownPir]);
  const pirDocs = docs?.filter((d) => d.kind === "pir") ?? [];
  const ready = !!result || !!state.pirDoc;
  return (
    <div className="stack">
      <Card title="3. Allineamento del bersaglio sui template" sub={`${state.templates.map((t) => `${t.id}:${t.chains.join("")}`).join(", ")} · MODELLER align2d (salign con più template)`}>
        <div className="stack">
          <p className="small text-2">
            MODELLER allinea la sequenza sulla struttura dei template (align2d, con penalità per i gap nelle eliche e nei filamenti). La modalità
            Confidenza colora l'identità locale e segna la zona crepuscolare e i residui senza template: correggi l'allineamento nell'Editor PIR
            dove serve (loop, sito attivo) e salvalo come documento del progetto per usarlo qui.
          </p>
          <PreflightPanel state={preflight} />
          <div className="row">
            <LaunchButton preflight={preflight} disabled={running} onLaunch={async () => {
              const j = await api.align2d(request);
              update((s) => withAlignJob(s, j.id));
            }}>{state.alignJob ? "Allinea di nuovo" : "Allinea con MODELLER"}</LaunchButton>
            {result && <Link className="btn btn-sm" to={`/pir?fromjob=${state.alignJob}`} title="Apre l'allineamento nell'Editor PIR: correggilo e salvalo come documento"><Icon name="file" size={13} /> Apri nell'Editor PIR</Link>}
            <Field label="Oppure un documento PIR del progetto">
              <select className="input" value={state.pirDoc ?? ""} onChange={(e) => update((s) => withPirDoc(s, e.target.value || null))}>
                <option value="">— allineamento di MODELLER —</option>
                {pirDocs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </Field>
          </div>
          {state.alignJob && align.summary && !state.pirDoc && <JobPanel summary={align.summary} log={align.log} onCancel={align.cancel} compact />}
          {!state.pirDoc && result?.warnings?.map((w) => <Alert key={w} tone="warn">{w}</Alert>)}
          {state.pirDoc && doc && (((doc.meta?.templates as TemplateRef[] | undefined) ?? []).length
            ? <p className="tiny muted">Template del documento: {((doc.meta.templates as TemplateRef[]) ?? []).map((t) => `${t.id}:${t.chains.join("")}`).join(", ")} (usati per la modellazione).</p>
            : <p className="tiny muted">Il documento non indica i template: si usano quelli scelti al passo 2, nello stesso ordine.</p>)}
        </div>
      </Card>
      {entries.length > 1 && (
        <Card title={state.pirDoc ? `Documento ${doc?.name ?? ""}` : "Allineamento di MODELLER"}>
          <AlignmentView ids={entries.map((e) => e.code)} aligned={entries.map((e) => e.sequence)} reference={pirReference(entries.map((e) => e.kind))} initialMode="confidence" />
        </Card>
      )}
      <div className="row">
        <button className="btn btn-primary right" disabled={!ready} onClick={onNext}>Avanti: opzioni <Icon name="chevron" size={13} /></button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------- 4. opzioni
function OptionsStep({ state, update, align, projectId, onLaunched }: { state: WizardState; update: Update; align: Align2dResult | null; projectId: string | null; onLaunched: (id: string) => void }) {
  const { data: doc } = useAsync<DocumentItem | null>(() => (projectId && state.pirDoc ? api.document(projectId, state.pirDoc) : Promise.resolve(null)), [projectId, state.pirDoc]);
  const { data: library } = useAsync<TemplateItem[]>(() => api.templates(), []);
  const { data: licenses, loading: licensesLoading } = useAsync<LicenseStatus | null>(() => api.licenses().catch(() => null), []);
  const docTemplates = state.pirDoc ? ((doc?.meta?.templates as TemplateRef[] | undefined) ?? []) : [];
  const templates = docTemplates.length ? docTemplates : state.templates;
  const templatesKey = JSON.stringify(templates);
  const { data: analyses } = useAsync<Record<string, TemplateAnalysis>>(async () => {
    const out: Record<string, TemplateAnalysis> = {};
    for (const t of templates) {
      try {
        out[t.id] = await api.template(t.id);
      } catch {
        /* template scaricato dall'allineamento: la libreria lo ha; altrimenti niente ligandi predefiniti */
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templatesKey]);
  const alignment = state.pirDoc ? doc?.content ?? null : align?.pir ?? null;
  const [ligandRefs, setLigandRefs] = useState<LigandRef[]>([]);
  const [ligandsReady, setLigandsReady] = useState(false);
  const ctx: OptionContext = useMemo(() => ({
    predicted: hasPredictedTemplate(templates, library),
    ligandNames: [...new Set(templates.flatMap((t) => defaultTransfer(t, analyses?.[t.id]).names))],
    procheck: !!licenses?.procheck.installed,
    prosa: !!licenses && licenses.prosa.installed && licenses.prosa.license && licenses.prosa.mac_ok,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [templatesKey, library, analyses, licenses]);
  // predefiniti ragionati finché l'utente non cambia le opzioni
  useEffect(() => {
    if (!state.optionsTouched && analyses && !licensesLoading) update((s) => (s.optionsTouched ? s : withOptions(s, reasonedOptions(ctx, s.options), false)));
  }, [ctx, analyses, licensesLoading, state.optionsTouched, update]);
  const o = state.options;
  const notes = optionNotes(o, ctx);
  const issues = optionsIssues(o);
  const request = alignment ? modelRequest(state, { projectId, alignment, templates, ligandRefs }) : null;
  const preflight = usePreflight(request && !issues.length && (!o.ligands || ligandsReady) ? preflightFor("model", state, request as unknown as Record<string, unknown>) : null);
  const set = (patch: Partial<typeof o>) => update((s) => withOptions(s, patch));
  return (
    <div className="split">
      <Card title="4. Opzioni di modellazione" sub="valori predefiniti ragionati: ogni scelta ha la sua spiegazione">
        <div className="stack">
          <Field label="Numero di modelli" hint={notes.models}>
            <input className="input" type="number" min={1} max={100} value={o.models} onChange={(e) => set({ models: Number(e.target.value) })} />
          </Field>
          <Field label="Raffinamento" hint={notes.refinement}>
            <Segmented value={o.refinement} onChange={(v) => set({ refinement: v })} options={REFINEMENTS.map((r) => ({ value: r.value, label: r.label }))} />
          </Field>
          <Field label="Analisi di qualità" hint={notes.quality}>
            <select className="input" value={o.quality} onChange={(e) => set({ quality: e.target.value as typeof o.quality })}>
              <option value="best">Solo sul modello migliore</option>
              <option value="all">Su tutti i modelli</option>
              <option value="none">Nessuna</option>
            </select>
          </Field>
          {o.quality !== "none" && <label className="check"><input type="checkbox" checked={o.extended} onChange={(e) => set({ extended: e.target.checked })} /> Includi PROCHECK e ProSA II</label>}
          <div className="stack-sm">
            <LigandPicker templates={templates} known={analyses ?? {}} enabled={o.ligands} onEnabled={(v) => set({ ligands: v })} selection={o.ligandSel}
              onSelection={(sel, refs, ready) => {
                update((s) => withOptions(s, { ligandSel: sel }, false));
                setLigandRefs(refs);
                setLigandsReady(ready);
              }} />
            <span className="tiny muted">{notes.ligands}</span>
          </div>
          {ctx.predicted && (
            <div className="stack-sm">
              <PlddtOption enabled={o.plddtTrim} threshold={o.plddtMin} onEnabled={(v) => set({ plddtTrim: v })} onThreshold={(v) => set({ plddtMin: v })} />
              <span className="tiny muted">{notes.plddt}</span>
            </div>
          )}
          {issues.map((m) => <Alert key={m} tone="err">{m}</Alert>)}
          {!alignment && <Alert tone="warn">Allineamento non ancora disponibile: torna al passo 3.</Alert>}
        </div>
      </Card>
      <Card title="Controlli e avvio">
        <div className="stack">
          <PreflightPanel state={preflight} />
          <LaunchButton preflight={preflight} disabled={!request || !!issues.length || (o.ligands && !ligandsReady)} onLaunch={async () => {
            if (!request) return;
            const j = await api.model(request);
            onLaunched(j.id);
          }}>Avvia modellazione</LaunchButton>
          {o.ligands && !ligandsReady && <span className="tiny muted">Lettura dei ligandi dei template in corso…</span>}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------- 6. risultati
function ResultsStep({ jobId, result, onNew, navigate }: { jobId: string; result: ModelResult; onNew: () => void; navigate: ReturnType<typeof useNavigate> }) {
  const viewer = useRef<Viewer3DHandle>(null);
  const best = result.models.find((m) => m.best);
  const { data: report } = useAsync<QualityReport | null>(() => (best ? api.modelQuality(jobId, best.file).catch(() => null) : Promise.resolve(null)), [jobId, best?.file]);
  const summary = resultSummary(result, report);
  const compare = best ? compareTargets(jobId, best.file, result.models, result.templates).find((t) => t.label.startsWith("Template")) : undefined;
  if (!summary || !best) return <Card><Empty icon="cube" title="Nessun modello valido nel risultato" /></Card>;
  return (
    <div className="stack">
      {summary.warnings.map((w) => <Alert key={w} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Modello migliore (DOPE minimo)" value={<span className="row" style={{ gap: 8 }}><Grade grade={summary.grade} large /> <span className="small">{summary.file}</span></span>}
          note={summary.gradeScore !== null ? `voto ${fmtNum(summary.gradeScore, 1)}/100` : "analisi di qualità non calcolata"} />
        <Stat label="Consenso fra i metodi" value={summary.consensus !== null ? <>{fmtNum(summary.consensus, 1)}<small>/100</small></> : "—"} note={summary.consensusBand ? `fascia ${summary.consensusBand}` : "senza analisi di qualità"} />
        <Stat label="DOPE" value={fmtNum(summary.dope, 0)} note={`zDOPE ${fmtNum(summary.zdope, 2)} · GA341 ${fmtNum(summary.ga341, 2)}`} />
        <Stat label="Modelli costruiti" value={summary.models} note="il migliore ha il DOPE più basso" />
      </div>
      <Card title="6. Risultati" right={
        <>
          <button className="btn btn-sm btn-primary" onClick={() => navigate(`/quality?job=${jobId}&file=${encodeURIComponent(best.file)}`)}><Icon name="shield" size={13} /> Qualità</button>
          {compare && <button className="btn btn-sm" onClick={() => navigate(compare.href)} title={`Confronto strutturale con ${compare.label}`}><Icon name="compare" size={13} /> Confronta con il template</button>}
          <ReportButton jobId={jobId} model={best.file} viewer={viewer} />
          <Link className="btn btn-sm btn-ghost" to={`/modeling?job=${jobId}`}>Tutti i modelli</Link>
          <button className="btn btn-sm btn-ghost" onClick={onNew}><Icon name="plus" size={13} /> Nuovo modello guidato</button>
        </>
      }>
        <Viewer3D url={api.jobFileUrl(jobId, best.file)} handle={viewer} />
      </Card>
    </div>
  );
}
