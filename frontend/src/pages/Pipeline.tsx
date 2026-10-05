import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { JobItemsEvent, JobSummary, PipelineItem, PipelinePage, PipelineRequest, PipelineResult, ResumeScope, SearchEngine, Sequence, TemplateItem, TemplateMode, TemplateRef } from "../api/types";
import { LaunchButton, PreflightPanel, usePreflight } from "../components/Preflight";
import { pipelinePreflight } from "../lib/preflight";
import { RecomputeOption } from "../components/CacheOption";
import { SequenceFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { PipelineItemActions, PipelineModelView } from "../components/PipelineItemActions";
import { TemplatePicker } from "../components/Pickers";
import { FormSection } from "../components/WorkflowParts";
import { PlddtOption } from "../components/PlddtOption";
import { StackBar } from "../components/charts";
import { Alert, Badge, BarCell, Card, DetailGrid, Disclosure, Empty, ExpandAll, ExpandButton, Field, Grade, PageHead, Segmented, StatusBadge, useExpanded } from "../components/ui";
import { hasPredictedTemplate, pipelineFastaName, PLDDT_DEFAULT } from "../lib/alphafold";
import { useProject, useToast } from "../lib/context";
import { fmtDate, fmtDuration, fmtEvalue, fmtNum, isActive, relTime, searchCacheHitsLabel, STATUS_LABEL } from "../lib/format";
import { recordsToFasta } from "../lib/files";
import { useAsync, useInterval, useJob, useJobsFeed, useLiveJobList, useThrottled } from "../lib/hooks";
import { pipelineStatusParts, plural } from "../lib/shellView";
import { ENGINE_LABEL } from "../lib/workflowView";
import { clampMaxTemplates, itemTemplates, itemTemplatesTooltip, MAX_TEMPLATES_DEFAULT, MAX_TEMPLATES_MAX, MAX_TEMPLATES_MIN } from "../lib/templateRank";

const SORTS: { key: string; label: string }[] = [
  { key: "idx", label: "#" },
  { key: "name", label: "Sequenza" },
  { key: "status", label: "Stato" },
  { key: "template", label: "Template" },
  { key: "identity", label: "Identità" },
  { key: "coverage", label: "Copertura" },
  { key: "dope", label: "DOPE" },
  { key: "grade_score", label: "Voto" },
  { key: "rama_favored", label: "Fav. %" },
  { key: "clashscore", label: "Clash" },
];

/** Dettaglio di un elemento (riga espansa): esito, template scelti con le motivazioni, metriche, tempi. */
function ItemDetail({ item: it }: { item: PipelineItem }) {
  const chosen = itemTemplates(it);
  const m = it.metrics ?? {};
  const items: { label: string; value: ReactNode }[] = [
    { label: "Lunghezza", value: `${it.length} aa` },
    { label: "Stato", value: it.status !== "completed" && it.stage ? `${STATUS_LABEL[it.status] ?? it.status} · ${it.stage}` : STATUS_LABEL[it.status] ?? it.status },
    { label: "Identità", value: it.identity == null ? "—" : `${fmtNum(it.identity, 1)}%` },
    { label: typeof m.combined_coverage === "number" ? "Copertura complessiva" : "Copertura", value: it.coverage == null ? "—" : `${fmtNum(it.coverage, 1)}%` },
    { label: "E-value", value: fmtEvalue(it.evalue) },
    { label: "Origine del template", value: m.template_predicted ? "AlphaFold DB (predetto)" : ENGINE_LABEL[it.template_source as SearchEngine] ?? it.template_source ?? "—" },
    { label: "DOPE", value: fmtNum(it.dope, 0) },
    { label: "GA341", value: fmtNum(it.ga341, 2) },
    { label: "Voto", value: it.grade ? `${it.grade} · ${fmtNum(it.grade_score, 0)}/100` : "—" },
    { label: "Ramachandran favorite", value: it.rama_favored == null ? "—" : `${fmtNum(it.rama_favored)}%` },
    { label: "Clashscore", value: fmtNum(it.clashscore, 1) },
    { label: "Modello migliore", value: it.best_model ? <span className="mono">{it.best_model}</span> : "—" },
    { label: "Durata", value: fmtDuration(it.started_at, it.finished_at) },
  ];
  if (it.finished_at) items.push({ label: "Terminato", value: fmtDate(it.finished_at) });
  if (m.plddt_removed) items.push({ label: "Residui tolti (pLDDT basso)", value: String(m.plddt_removed) });
  if (m.search_cached_at) items.push({ label: "Ricerca dalla cache", value: fmtDate(m.search_cached_at) });
  if (m.excluded_templates) items.push({ label: "Candidati esclusi", value: `${m.excluded_templates} (motivi nel log)` });
  return (
    <div className="shell-peek">
      {it.error && <Alert tone="err"><pre className="shell-error-text">{it.error}</pre></Alert>}
      {it.twilight ? <Alert tone="warn">Identità o copertura sotto la soglia della zona crepuscolare: il modello va verificato con attenzione.</Alert> : null}
      <DetailGrid items={items} />
      {chosen.some((t) => t.role) && (
        <div className="stack-sm">
          <h4>Template scelti</h4>
          <ul className="shell-file-list">
            {chosen.map((t) => <li key={t.id}><b className="mono">{t.id}</b>{t.role ? ` (${t.role})` : ""}{t.reason ? `: ${t.reason}` : ""}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

const ITEM_COLUMNS = 12;

function Items({ jobId, running, cacheHits }: { jobId: string; running: boolean; cacheHits?: number }) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState({ key: "idx", dir: "asc" });
  const [q, setQ] = useState("");
  const [view, setView] = useState<{ name: string; path: string } | null>(null);
  const open = useExpanded<number>();
  const { data, reload } = useAsync<PipelinePage>(() => api.pipelineItems(jobId, { page, size, status, sort: sort.key, direction: sort.dir, q }), [jobId, page, size, status, sort.key, sort.dir, q]);
  // la pagina si ricarica quando il flusso dell'elenco segnala elementi cambiati; polling solo senza flusso
  const refresh = useThrottled(reload, 1000);
  const live = useJobsFeed((type, event) => {
    if (type === "items" ? (event as JobItemsEvent | null)?.job_id === jobId : type === "ready" || type === "resync") refresh();
  });
  useInterval(reload, running && !live ? 3000 : null);
  const wasRunning = useRef(running);
  useEffect(() => {
    // stato finale degli elementi al termine; alla ripresa, subito gli elementi rimessi in attesa
    if (wasRunning.current !== running) reload();
    wasRunning.current = running;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const parts = useMemo(() => pipelineStatusParts(data?.counts ?? {}), [data?.counts]);
  if (!data) return null;
  const counts = data.counts;
  const indices = data.items.map((it) => it.idx);
  return (
    <Card
      icon="list"
      title="Risultati per sequenza"
      sub={searchCacheHitsLabel(cacheHits) ?? undefined}
      right={
        <>
          {data.items.length > 1 && (
            <ExpandAll allOpen={indices.every((i) => open.has(i))} onExpand={() => open.expandAll(indices)} onCollapse={open.collapseAll} noun="righe della pagina" />
          )}
          {/* il file del job (tutte le righe), diverso dal CSV del grafico qui sotto */}
          <a className="btn btn-sm" href={api.pipelineCsvUrl(jobId)} title="Scarica la tabella completa dei risultati della pipeline (CSV)">
            <Icon name="download" size={13} /> Tabella pipeline (CSV)
          </a>
          <a className="btn btn-sm btn-primary" href={api.pipelineZipUrl(jobId)}><Icon name="download" size={13} /> ZIP modelli</a>
        </>
      }
    >
      <div className="stack">
        {parts.length > 0 && (
          <StackBar parts={parts} title="Stato delle sequenze" subtitle={`${plural(data.total, "sequenza", "sequenze")} della pipeline`} filename={`pipeline_${jobId.slice(0, 8)}_stati`} />
        )}
        <div className="row">
          {["all", "completed", "running", "searching", "modeling", "pending", "failed", "filtered", "cancelled"].map((s) => {
            const n = s === "all" ? Object.values(counts).reduce((a, b) => a + b, 0) : counts[s] ?? 0;
            if (s !== "all" && !n) return null;
            return (
              <button key={s} type="button" className={`btn btn-sm ${status === s ? "btn-primary" : ""}`} aria-pressed={status === s} onClick={() => { setStatus(s); setPage(1); }}>
                {s === "all" ? "Tutte" : STATUS_LABEL[s]} · {n}
              </button>
            );
          })}
          <input className="input input-sm right" style={{ width: 220 }} placeholder="Cerca sequenza o template" aria-label="Cerca sequenza o template" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 34 }} aria-label="Dettagli" />
                {SORTS.map((s) => (
                  <th key={s.key} className={`sortable ${["identity", "coverage", "dope", "grade_score", "rama_favored", "clashscore"].includes(s.key) ? "num" : ""}`}
                    aria-sort={sort.key === s.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                    onClick={() => setSort({ key: s.key, dir: sort.key === s.key && sort.dir === "asc" ? "desc" : "asc" })}>
                    {s.label} {sort.key === s.key ? (sort.dir === "asc" ? "▲" : "▼") : ""}
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => {
                const isOpen = open.has(it.idx);
                return (
                  <Fragment key={it.idx}>
                    <tr className={`clickable${isOpen ? " row-open" : ""}`} onClick={() => open.toggle(it.idx)}>
                      <td><ExpandButton open={isOpen} onClick={() => open.toggle(it.idx)} label={`dettagli di ${it.name}`} /></td>
                      <td className="shell-sub">{it.idx + 1}</td>
                      <td><b>{it.name}</b><div className="shell-sub">{it.length} aa</div></td>
                      <td>
                        <StatusBadge status={it.status} />
                        {it.status !== "completed" && it.stage && <div className="shell-sub ellipsis" style={{ maxWidth: 180 }} title={it.error ?? it.stage}>{it.error ?? it.stage}</div>}
                      </td>
                      <td className="nowrap" title={itemTemplatesTooltip(it)}>
                        {itemTemplates(it).length ? itemTemplates(it).map((t, i) => (
                          <span key={t.id}>{i > 0 && <br />}{t.id}{t.role === "complementare" && <span className="shell-sub"> (compl.)</span>}</span>
                        )) : "—"}{" "}
                        {it.twilight ? <Badge tone="warn" title="Identità o copertura sotto soglia">crepuscolare</Badge> : null}
                        {(it.template_source === "alphafold" || it.metrics?.template_predicted) && (
                          <Badge tone="warn" title={`Modello predetto da AlphaFold DB, non struttura sperimentale${it.metrics?.plddt_removed ? `: tolti ${it.metrics.plddt_removed} residui a basso pLDDT` : ""}`}>AlphaFold</Badge>
                        )}
                        {it.metrics?.search_cached_at && <Badge tone="info" title={`Ricerca del template riusata dalla cache dei calcoli (${fmtDate(it.metrics.search_cached_at)})`}>cache</Badge>}
                        {it.evalue != null && <div className="shell-sub">E {fmtEvalue(it.evalue)}</div>}
                      </td>
                      <td className="num"><BarCell value={it.identity} /></td>
                      <td className="num"><BarCell value={it.coverage} /></td>
                      <td className="num">{fmtNum(it.dope, 0)}</td>
                      <td className="num">{it.grade ? <span className="row" style={{ justifyContent: "flex-end", gap: 6 }}><Grade grade={it.grade} /> {fmtNum(it.grade_score, 0)}</span> : "—"}</td>
                      <td className="num">{fmtNum(it.rama_favored)}</td>
                      <td className="num">{fmtNum(it.clashscore, 1)}</td>
                      <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                        <PipelineItemActions jobId={jobId} item={it} onView={() => it.best_model && setView({ name: it.name, path: it.best_model })} />
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="row-detail">
                        <td colSpan={ITEM_COLUMNS}><ItemDetail item={it} /></td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {!data.items.length && <tr><td colSpan={ITEM_COLUMNS} className="text-2">Nessun elemento con questo filtro.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="row small">
          <span className="text-2">{data.total} elementi · pagina {data.page} di {data.pages}</span>
          <div className="right row">
            <select className="input input-sm" style={{ width: 90 }} value={size} aria-label="Elementi per pagina" onChange={(e) => { setSize(Number(e.target.value)); setPage(1); }}>
              {[10, 25, 50, 100].map((s) => <option key={s} value={s}>{s}/pag.</option>)}
            </select>
            <button type="button" className="btn btn-sm" disabled={data.page <= 1} aria-label="Pagina precedente" onClick={() => setPage(data.page - 1)}>‹</button>
            <button type="button" className="btn btn-sm" disabled={data.page >= data.pages} aria-label="Pagina successiva" onClick={() => setPage(data.page + 1)}>›</button>
          </div>
        </div>
      </div>
      {view && <PipelineModelView jobId={jobId} name={view.name} model={view.path} onClose={() => setView(null)} />}
    </Card>
  );
}

export default function Pipeline() {
  const { projectId } = useProject();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [name, setName] = useState("");
  const [fasta, setFasta] = useState("");
  const [mode, setMode] = useState<TemplateMode>("auto");
  const [maxTemplates, setMaxTemplates] = useState(MAX_TEMPLATES_DEFAULT);
  const [templates, setTemplates] = useState<TemplateRef[]>([]);
  const [engine, setEngine] = useState<SearchEngine>("rcsb");
  const [evalue, setEvalue] = useState(0.001);
  const [twFilter, setTwFilter] = useState(false);
  const [twIdentity, setTwIdentity] = useState(25);
  const [twCoverage, setTwCoverage] = useState(40);
  const [models, setModels] = useState(3);
  const [refinement, setRefinement] = useState("fast");
  const [quality, setQuality] = useState(true);
  const [extended, setExtended] = useState(false);
  const [parallel, setParallel] = useState(2);
  const [recompute, setRecompute] = useState(false);
  const [plddtTrim, setPlddtTrim] = useState(true);
  const [plddtMin, setPlddtMin] = useState(PLDDT_DEFAULT);
  const { data: projectSeqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const { data: library } = useAsync<TemplateItem[]>(() => api.templates(), [templates.map((t) => t.id).join(",")]);
  // la soglia pLDDT serve solo se i template possono essere modelli AlphaFold
  const predictedPossible = mode !== "fixed" ? engine === "alphafold" : hasPredictedTemplate(templates, library);
  const { data: recent, reload: reloadRecent, setData: setRecent } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "pipeline", limit: 10 }), [projectId, jobId]);
  // stato delle pipeline recenti dal flusso dell'elenco (anche quelle avviate da un'altra scheda)
  useLiveJobList(recent, setRecent, reloadRecent, (j) => j.kind === "pipeline" && (!projectId || j.project_id === projectId));
  const { job, summary, log, cancel, resume } = useJob<PipelineResult>(jobId, { withLog: true });
  const count = (fasta.match(/^>/gm) ?? []).length;
  const running = summary ? isActive(summary.status) : false;
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current !== running) reloadRecent(); // stato aggiornato anche nell'elenco delle recenti
    wasRunning.current = running;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const doResume = async (scope: ResumeScope) => {
    const retry = scope === "failed" || summary?.status === "completed"; // da completata restano solo i falliti
    try {
      await resume(scope);
      toast.push(retry ? "Nuovo tentativo sulle sequenze fallite" : "Pipeline ripresa: si elaborano solo le sequenze non concluse");
    } catch (e) {
      toast.error(e);
    }
  };

  // corpo della pipeline: lo stesso per l'avvio e per i controlli preventivi (F8a)
  const body = useMemo<PipelineRequest>(() => ({
    project_id: projectId ?? undefined,
    name: name || undefined,
    fasta,
    template_mode: mode,
    max_templates: clampMaxTemplates(maxTemplates),
    templates,
    engine,
    evalue,
    twilight: { filter: twFilter, identity: twIdentity, coverage: twCoverage },
    options: { models, refinement, plddt_trim: plddtTrim, plddt_min: plddtMin },
    quality,
    extended_quality: quality && extended,
    parallel,
    use_cache: !recompute,
  }), [projectId, name, fasta, mode, maxTemplates, templates, engine, evalue, twFilter, twIdentity, twCoverage, models, refinement, plddtTrim, plddtMin, quality, extended, parallel, recompute]);
  const preflight = usePreflight(useMemo(() => pipelinePreflight(body, count), [body, count]), 800);

  const submit = async () => {
    try {
      const j = await api.pipeline(body);
      setParams({ job: j.id });
      toast.push("Pipeline avviata: prosegue anche chiudendo il browser");
    } catch (e) {
      toast.error(e);
    }
  };

  return (
    <div className="page">
      <PageHead title="Pipeline batch" lead="Da un multi-FASTA a modelli validati: per ogni sequenza ricerca del template, download, allineamento e modellazione, scelta del modello migliore e analisi di qualità. L'esecuzione avviene sul server e sopravvive alla chiusura del browser." />
      <div className="split">
        <div className="stack">
          <Card icon="layers" title="Nuova pipeline">
            <div className="stack">
              <FormSection title="Sequenze" hint={plural(count, "sequenza", "sequenze")}>
                <Field label="Nome"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Es. chinasi famiglia X" /></Field>
                <Field label="Sequenze (multi-FASTA)">
                  <textarea className="input" rows={8} value={fasta} onChange={(e) => setFasta(e.target.value)} placeholder={">seq1\nMKT...\n>seq2\nMAS..."} />
                </Field>
                <div className="row">
                  {!!projectSeqs?.length && (
                    <button type="button" className="btn btn-sm" title="Le sequenze importate da UniProt ricevono l'intestazione sp|ACC|NOME: con AlphaFold DB si usa l'accessione, senza passare da NCBI" onClick={() => setFasta(projectSeqs.map((s) => `>${pipelineFastaName(s)}\n${s.sequence}`).join("\n"))}>
                      <Icon name="dna" size={14} /> Usa le sequenze del progetto ({projectSeqs.length})
                    </button>
                  )}
                  <SequenceFileButton onLoaded={(parsed) => setFasta(recordsToFasta(parsed.records))}>Carica da file</SequenceFileButton>
                  <span className="small text-2">multi-FASTA, GenBank, UniProt… (anche .gz)</span>
                </div>
              </FormSection>
              <FormSection title="Template">
                <Segmented label="Scelta dei template" value={mode} onChange={setMode} options={[{ value: "auto", label: "Ricerca automatica" }, { value: "multi", label: "Più template complementari" }, { value: "fixed", label: "Template fisso" }]} />
                {mode !== "fixed" ? (
                  <>
                    {mode === "multi" && (
                      <>
                        <Field label={`Template al massimo per sequenza (${MAX_TEMPLATES_MIN}–${MAX_TEMPLATES_MAX})`}>
                          <input className="input" type="number" min={MAX_TEMPLATES_MIN} max={MAX_TEMPLATES_MAX} value={maxTemplates} onChange={(e) => setMaxTemplates(Number(e.target.value))} onBlur={() => setMaxTemplates(clampMaxTemplates(maxTemplates))} />
                        </Field>
                        <Disclosure title="Come si scelgono i template complementari">
                          <p>
                            Il primo è il template con il punteggio migliore; gli altri si aggiungono solo se coprono almeno 10 residui del bersaglio non coperti o migliorano l'identità locale, e se non sono quasi identici a uno già scelto.
                            MODELLER li usa insieme: allineamento strutturale dei template (salign) oppure, se questo ne sposta uno rispetto al bersaglio, l'allineamento di ogni template sul bersaglio (la scelta è nel log).
                            La copertura della tabella è quella complessiva; le motivazioni sono nel log, nel suggerimento della colonna Template e nel dettaglio di ogni riga.
                          </p>
                        </Disclosure>
                      </>
                    )}
                    <div className="grid grid-2">
                      <Field label="Motore">
                        <select className="input" value={engine} onChange={(e) => setEngine(e.target.value as SearchEngine)}>
                          <option value="rcsb">RCSB (rapido)</option>
                          <option value="ncbi">NCBI BLAST (lento)</option>
                          <option value="local">BLAST+ locale</option>
                          <option value="mmseqs">MMseqs2 locale</option>
                          <option value="alphafold">AlphaFold DB (modelli predetti)</option>
                        </select>
                      </Field>
                      <Field label="E-value massimo">
                        <select className="input" value={evalue} onChange={(e) => setEvalue(Number(e.target.value))}>
                          {[1e-10, 1e-5, 1e-3, 0.01, 0.1, 1].map((v) => <option key={v} value={v}>{v >= 0.01 ? v : v.toExponential(0)}</option>)}
                        </select>
                      </Field>
                    </div>
                    {engine === "alphafold" && (
                      <Alert tone="info">
                        Modelli predetti di AlphaFold DB, non strutture sperimentali. Per ogni sequenza si usa l'accessione dell'intestazione FASTA di UniProt
                        (<span className="mono">&gt;sp|P69905|HBA_HUMAN</span>, aggiunta da «Usa le sequenze del progetto» per quelle importate da UniProt); altrimenti NCBI BLAST su Swiss-Prot trova le proteine più simili (1–5 min per sequenza).
                        Le accessioni vanno a EBI, le sequenze senza accessione a NCBI.
                      </Alert>
                    )}
                    {/* le scelte correnti restano nel riepilogo anche a sezione chiusa */}
                    <Disclosure title="Zona crepuscolare e cache delle ricerche"
                      extra={`${twFilter ? `salta sotto ${twIdentity}% id. / ${twCoverage}% cop.` : "modella anche sotto soglia"} · ${recompute ? "ricalcola le ricerche" : "riusa le ricerche già fatte"}`}>
                      <div className="stack-sm">
                        <label className="check"><input type="checkbox" checked={twFilter} onChange={(e) => setTwFilter(e.target.checked)} /> Salta le sequenze in zona crepuscolare</label>
                        <div className="grid grid-2">
                          <Field label="Identità minima (%)"><input className="input" type="number" value={twIdentity} onChange={(e) => setTwIdentity(Number(e.target.value))} /></Field>
                          <Field label="Copertura minima (%)"><input className="input" type="number" value={twCoverage} onChange={(e) => setTwCoverage(Number(e.target.value))} /></Field>
                        </div>
                        <span className="small text-2">Con il filtro disattivato le sequenze sotto soglia vengono comunque modellate e contrassegnate (utile per proteine orfane).</span>
                        <RecomputeOption checked={recompute} onChange={setRecompute} label="Ricalcola le ricerche ignorando la cache" />
                        <span className="small text-2">Rilanciando le stesse sequenze con altre opzioni di modellazione, le ricerche già fatte vengono riusate.</span>
                      </div>
                    </Disclosure>
                  </>
                ) : (
                  <TemplatePicker value={templates} onChange={setTemplates} />
                )}
              </FormSection>
              <FormSection title="Modellazione e qualità">
                <div className="grid grid-3">
                  <Field label="Modelli"><input className="input" type="number" min={1} max={50} value={models} onChange={(e) => setModels(Number(e.target.value))} /></Field>
                  <Field label="Raffinamento">
                    <select className="input" value={refinement} onChange={(e) => setRefinement(e.target.value)}>
                      <option value="very_fast">Rapidissimo</option>
                      <option value="fast">Rapido</option>
                      <option value="normal">Standard</option>
                      <option value="thorough">Accurato</option>
                    </select>
                  </Field>
                  <Field label="In parallelo"><input className="input" type="number" min={1} max={8} value={parallel} onChange={(e) => setParallel(Number(e.target.value))} /></Field>
                </div>
                {predictedPossible && <PlddtOption enabled={plddtTrim} threshold={plddtMin} onEnabled={setPlddtTrim} onThreshold={setPlddtMin} />}
                <label className="check"><input type="checkbox" checked={quality} onChange={(e) => setQuality(e.target.checked)} /> Analisi di qualità del modello migliore</label>
                {quality && <label className="check"><input type="checkbox" checked={extended} onChange={(e) => setExtended(e.target.checked)} /> Includi PROCHECK e ProSA II (qualche secondo in più per sequenza)</label>}
              </FormSection>
              {/* controlli preventivi: licenza, motore e database, tempi e limiti dei servizi, spazio */}
              <PreflightPanel state={preflight} />
              <LaunchButton preflight={preflight} disabled={count < 1 || (mode === "fixed" && !templates.length)} onLaunch={submit}>Avvia pipeline</LaunchButton>
            </div>
          </Card>
          <Card icon="activity" title="Pipeline recenti">
            {!recent?.length ? <p className="small text-2">Nessuna pipeline.</p> : (
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
          {jobId ? (
            <>
              <JobPanel summary={summary} log={log} onCancel={cancel} resume={job} onResume={doResume} />
              <Items key={jobId} jobId={jobId} running={running} cacheHits={job?.id === jobId ? job.result?.search_cache_hits : undefined} />
            </>
          ) : <Card><Empty icon="layers" title="Avvia una pipeline o aprine una recente">Stato, log in tempo reale e risultati per sequenza compaiono qui; ogni riga si espande con template, metriche e tempi.</Empty></Card>}
        </div>
      </div>
    </div>
  );
}
