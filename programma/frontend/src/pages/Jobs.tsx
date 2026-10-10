import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { EntityLabels, Job, JobKind, JobSummary, ResumeScope } from "../api/types";
import { Icon } from "../components/Icon";
import { JobCleanupDialog } from "../components/JobCleanupDialog";
import { JobPanel } from "../components/JobPanel";
import { ReproButton } from "../components/ReproButton";
import { FavoriteStar, LabelEditor, LabelFilterSelect, TagChips } from "../components/Labels";
import { Alert, Card, Confirm, DetailGrid, Disclosure, Empty, ExpandButton, PageHead, Progress, rowActivate, Spinner, StatusBadge, useExpanded } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtBytes, fmtDate, fmtDuration, fmtEta, isActive, KIND_LABEL, queueLabel, relTime, STATUS_LABEL } from "../lib/format";
import { useAsync, useInterval, useJob, useLiveJobList } from "../lib/hooks";
import { listPollInterval } from "../lib/live";
import { paramRows } from "../lib/rerun";
import { logTail, matchesStatus, STATUS_FILTERS, type StatusFilter } from "../lib/shellView";
import { filterParams, filterValue, itemLabels, matchesFilter, parseFilterValue, type ItemLabels, type LabelFilter } from "../lib/tags";
import { jobTimes } from "../lib/toolsView";

const OPEN_IN: Record<JobKind, (id: string) => string> = {
  search: (id) => `/search?job=${id}`,
  align: (id) => `/alignment?job=${id}`,
  align2d: (id) => `/pir?fromjob=${id}`,
  model: (id) => `/modeling?job=${id}`,
  loop: (id) => `/modeling?job=${id}`,
  quality: (id) => `/quality?qjob=${id}`,
  qmean: (id) => `/jobs?id=${id}`,
  pipeline: (id) => `/pipeline?job=${id}`,
  selftest: () => "/settings?tab=tools",
  db_update_pdbaa: () => "/settings?tab=databases",
  db_build_mmseqs: () => "/settings?tab=databases",
  db_index_mmseqs: () => "/settings?tab=databases",
  db_convert_uniclust: () => "/settings?tab=databases",
  db_local_uniclust: () => "/settings?tab=databases",
  analysis: (id) => `/quality?ajob=${id}`,
  pockets: (id) => `/quality?pjob=${id}`,
  electrostatics: (id) => `/quality?ejob=${id}`,
  refine: (id) => `/modeling?job=${id}`,
  rerun: (id) => `/rerun?job=${id}`,
  mutate: (id) => `/modeling?job=${id}`,
  alascan: (id) => `/modeling?job=${id}`,
  predict: (id) => `/quality?prjob=${id}`,
};

/** Parametri letti al più (pannello Dettagli) e mostrati nella riga espansa dell'elenco. */
const PARAM_ROWS = 80;
const PEEK_ROWS = 12;

/** Parametri del job (chiave e valore abbreviato). */
function ParamList({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="kv shell-kv-params">
      {rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
    </dl>
  );
}

/**
 * Riga espansa dell'elenco: stato e tempi subito; parametri, file e ultime righe del log letti all'apertura (e di nuovo
 * quando cambia lo stato del job). `memo`: l'elenco si ridisegna a ogni evento dei job, la riga solo se cambia il suo.
 */
const JobPeek = memo(function JobPeek({ job: s, onOpen }: { job: JobSummary; onOpen: (id: string) => void }) {
  const { data: full, error } = useAsync<Job>(() => api.job(s.id), [s.id, s.status]);
  // offset fuori intervallo: il server restituisce la fine del log (gli ultimi 256 KB, non c'è un limite più corto),
  // non l'inizio; l'estratto si calcola una volta per lettura
  const { data: log } = useAsync<string>(() => api.jobLog(s.id, -1).then((c) => c.text), [s.id, s.status]);
  const params = paramRows(full?.params, PARAM_ROWS);
  const files = full?.files ?? [];
  const bytes = files.reduce((sum, f) => sum + f.size, 0);
  const tail = useMemo(() => (log ? logTail(log, 10) : ""), [log]);
  return (
    <div className="shell-peek">
      <DetailGrid items={[
        { label: "Tipo", value: KIND_LABEL[s.kind] },
        { label: "Stato", value: s.stage && s.status !== "completed" ? `${STATUS_LABEL[s.status] ?? s.status} · ${s.stage}` : STATUS_LABEL[s.status] ?? s.status },
        ...jobTimes(s),
        ...(isActive(s.status) ? [{ label: "Avanzamento", value: `${Math.round(s.progress * 100)}%${fmtEta(s.eta_seconds) ? ` · ${fmtEta(s.eta_seconds)}` : ""}` }] : []),
        { label: "Identificativo", value: <span className="mono">{s.id}</span> },
      ]} />
      {s.status === "failed" && s.error && (
        <Alert tone="err"><pre className="shell-error-text">{s.error}</pre></Alert>
      )}
      {error && <span className="small text-2">Dettagli non letti: {error.message}</span>}
      {params.length > 0 && (
        <Disclosure title="Parametri" extra={`${params.length} voci`}>
          <ParamList rows={params.slice(0, PEEK_ROWS)} />
          {params.length > PEEK_ROWS && <span className="small text-2">E altri {params.length - PEEK_ROWS}: elenco completo nei dettagli.</span>}
        </Disclosure>
      )}
      {files.length > 0 && (
        <Disclosure title="File prodotti" extra={full?.files_truncated ? `più di ${files.length}` : `${files.length} · ${fmtBytes(bytes)}`}>
          <ul className="shell-file-list">
            {files.slice(0, 12).map((f) => <li key={f.path}><span className="mono">{f.path}</span> · {fmtBytes(f.size)}</li>)}
            {files.length > 12 && <li>e altri {files.length - 12}: {full?.files_truncated ? "elenco dei primi file nei dettagli" : "elenco completo nei dettagli"}.</li>}
          </ul>
        </Disclosure>
      )}
      {tail && (
        <div className="stack-sm">
          <h4>Ultime righe del log</h4>
          <pre className="log shell-log-tail">{tail}</pre>
        </div>
      )}
      <div className="shell-peek-actions">
        <button type="button" className="btn btn-sm" onClick={() => onOpen(s.id)}><Icon name="list" size={13} /> Tutti i dettagli</button>
        {s.status === "completed" && <Link className="btn btn-sm btn-ghost" to={OPEN_IN[s.kind](s.id)}><Icon name="external" size={13} /> Apri risultati</Link>}
      </div>
    </div>
  );
});

function Detail({ id, labels, onDeleted, onChanged, onLabels }: { id: string; labels?: ItemLabels; onDeleted: () => void; onChanged: () => void; onLabels: (l: EntityLabels) => void }) {
  const toast = useToast();
  const { job, summary, log, cancel, resume, error, gone } = useJob(id, { withLog: true });
  const [confirm, setConfirm] = useState(false);
  const deleting = useRef(false); // un doppio clic sulla conferma non manda due eliminazioni
  // link a un job che non è in questa installazione (pacchetto di un altro computer, job eliminato)
  if (!summary && (gone || error)) {
    return gone
      ? <Card><Empty icon="activity" title="Job non trovato in questa installazione">Il job {id} non esiste qui: è stato eliminato, a mano o da una pulizia, o viene da un'altra installazione (per esempio il job originale di un pacchetto di riproducibilità).</Empty></Card>
      : <Card><Empty icon="activity" title="Job non letto">{error}</Empty></Card>;
  }
  if (!summary) return null;
  const remove = async () => {
    if (deleting.current) return;
    deleting.current = true;
    try {
      const r = await api.deleteJob(id);
      setConfirm(false);
      toast.push("Job eliminato");
      if (r.leftover > 0) {
        toast.push("Alcuni file del job non si sono potuti rimuovere (in uso?): chiudi i programmi che li usano e premi «Pulisci file temporanei» in Impostazioni.", "warn");
      }
      onDeleted();
    } catch (e) {
      toast.error(e);
    } finally {
      deleting.current = false;
    }
  };
  const doResume = async (scope: ResumeScope) => {
    const retry = scope === "failed" || summary.status === "completed"; // da completata restano solo i falliti
    try {
      await resume(scope);
      toast.push(retry ? "Nuovo tentativo sulle sequenze fallite" : "Pipeline ripresa");
      onChanged();
    } catch (e) {
      toast.error(e);
    }
  };
  const params = paramRows(job?.params, PARAM_ROWS);
  const files = job?.files ?? [];
  return (
    <div className="stack">
      <JobPanel summary={summary} log={log} onCancel={cancel} resume={job} onResume={doResume} compact defaultLogOpen />
      <Card
        icon="info"
        title="Dettagli"
        right={
          <>
            <Link className="btn btn-sm btn-primary" to={OPEN_IN[summary.kind](summary.id)}>Apri risultati</Link>
            <a className="btn btn-sm" href={api.archiveUrl(id)}><Icon name="download" size={14} /> Archivio</a>
            <ReproButton jobId={id} kind={summary.kind} status={summary.status} />
            <button type="button" className="btn btn-sm btn-danger" disabled={isActive(summary.status)} onClick={() => setConfirm(true)} aria-label="Elimina il job"
              title={isActive(summary.status) ? "Un job attivo non si elimina: annullalo prima" : "Elimina il job e tutti i suoi file"}>
              <Icon name="trash" size={14} />
            </button>
          </>
        }
      >
        <div className="stack">
          <dl className="kv">
            <dt>Etichette</dt><dd><LabelEditor type="job" id={id} initial={labels} onChange={onLabels} /></dd>
            <dt>Tipo</dt><dd>{KIND_LABEL[summary.kind]}</dd>
            <dt>Creato</dt><dd>{fmtDate(summary.created_at)}</dd>
            <dt>Durata</dt><dd>{fmtDuration(summary.started_at, summary.finished_at)}</dd>
            <dt>Identificativo</dt><dd className="mono">{summary.id}</dd>
          </dl>
          {params.length > 0 && (
            <Disclosure title="Parametri" extra={`${params.length} voci`}>
              <ParamList rows={params} />
            </Disclosure>
          )}
          {files.length > 0 && (
            <Disclosure title="File prodotti" extra={job?.files_truncated ? `primi ${files.length}` : `${files.length} · ${fmtBytes(files.reduce((sum, f) => sum + f.size, 0))}`} defaultOpen>
              {job?.files_truncated && <p className="small text-2">Il job ha più di {files.length} file: qui ci sono i primi; tutti i file sono nell'archivio.</p>}
              <div className="table-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
                <table className="table">
                  <tbody>
                    {files.map((f) => (
                      <tr key={f.path}>
                        <td className="mono">{f.path}</td>
                        <td className="num small">{fmtBytes(f.size)}</td>
                        <td style={{ width: 40 }}>
                          <a className="btn btn-sm btn-ghost" href={api.jobFileUrl(id, f.path, true)} aria-label={`Scarica ${f.path}`} title="Scarica"><Icon name="download" size={13} /></a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Disclosure>
          )}
        </div>
      </Card>
      {confirm && (
        <Confirm text="Eliminare il job e tutti i suoi file? Non si può annullare." confirmLabel="Elimina" onCancel={() => setConfirm(false)}
          onConfirm={() => void remove()} />
      )}
    </div>
  );
}

export default function Jobs() {
  const { projectId, reload: reloadProjects } = useProject();
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const [scope, setScope] = useState<"project" | "all">("project");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const open = useExpanded<string>();
  const [cleanupOpen, setCleanupOpen] = useState(false);
  // una pulizia che finisce dopo l'uscita dalla pagina non deve cambiare l'URL (setParams riporterebbe a /jobs)
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  // filtro per etichetta o preferiti nell'URL (?tag=), così un link o la ricerca globale lo ritrovano
  const labelFilter = parseFilterValue(params.get("tag"));
  const setLabelFilter = (f: LabelFilter) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (filterValue(f)) next.set("tag", filterValue(f));
    else next.delete("tag");
    return next;
  });
  const labelKey = filterValue(labelFilter);
  const { data: jobs, reload, setData } = useAsync<JobSummary[]>(() => api.jobs({ project_id: scope === "project" ? projectId ?? undefined : undefined, kind: kind || undefined, limit: 200, ...filterParams(labelFilter) }), [scope, kind, projectId, labelKey]);
  // flusso dell'elenco: i job in elenco si aggiornano sul posto, un job nuovo o eliminato ricarica l'elenco (un job nuovo
  // non ha ancora etichette: con un filtro attivo non entra)
  const live = useLiveJobList(jobs, setData, reload,
    (j) => (scope === "all" || !projectId || j.project_id === projectId) && (!kind || j.kind === kind) && labelFilter.kind === "all");
  useInterval(reload, listPollInterval(live, !!jobs?.some((j) => isActive(j.status)), 2000, 8000));
  const shown = useMemo(() => jobs?.filter((j) => matchesStatus(j, status)) ?? [], [jobs, status]);
  const patchLabels = (l: EntityLabels) => {
    if (!jobs) return;
    const next = jobs.map((j) => (j.id === l.entity_id ? { ...j, tags: l.tags, favorite: l.favorite } : j));
    setData(next.filter((j) => j.id === selected || matchesFilter(j, labelFilter)));
  };
  // dopo una pulizia (qualunque esito): elenco, conteggi dei progetti e, se il job aperto è stato eliminato, deselezione.
  // L'assenza dall'elenco non basta a dirlo (è limitato a 200 e filtrato): si chiede lo stato del job.
  const onCleaned = () => {
    reload();
    reloadProjects().catch(() => undefined);
    if (selected && alive.current) {
      api.jobStatus(selected).catch((e) => {
        if (alive.current && e instanceof ApiError && e.status === 404) setParams(labelKey ? { tag: labelKey } : {});
      });
    }
  };
  const choose = useCallback((id: string) => setParams((p) => {
    const next = new URLSearchParams(p);
    next.set("id", id);
    return next;
  }), [setParams]);
  return (
    <div className="page">
      <PageHead title="Job"
        lead="Tutti i calcoli eseguiti in background, con stato, log, file prodotti e durata. I job attivi possono essere annullati; quelli terminati si eliminano dal dettaglio o in blocco con «Pulisci job…»; la freccia di una riga mostra parametri, file e ultime righe del log."
        actions={
          <>
            <Link className="btn" to="/rerun" title="Ricrea un calcolo da un pacchetto riproducibile e confronta i risultati"><Icon name="refresh" size={14} /> Riesegui da pacchetto</Link>
            <button type="button" className="btn" onClick={() => setCleanupOpen(true)} title="Elimina in blocco i job terminati che non servono più, con anteprima e conferma">
              <Icon name="trash" size={14} /> Pulisci job…
            </button>
          </>
        } />
      {cleanupOpen && (
        <JobCleanupDialog initial={{ scope, kind: kind || null }} onClose={() => setCleanupOpen(false)} onCleaned={onCleaned} />
      )}
      <div className="split">
        <Card icon="list" title="Elenco" sub={jobs ? `${shown.length} di ${jobs.length}` : undefined}>
          <div className="shell-list-tools">
            <select className="input input-sm" style={{ width: 150 }} value={scope} onChange={(e) => setScope(e.target.value as "project" | "all")} aria-label="Progetti">
              <option value="project">Questo progetto</option>
              <option value="all">Tutti i progetti</option>
            </select>
            <select className="input input-sm" style={{ width: 160 }} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Tipo di job">
              <option value="">Tutti i tipi</option>
              {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <LabelFilterSelect type="job" value={labelFilter} onChange={setLabelFilter} width={150} />
            <select className="input input-sm" style={{ width: 160 }} value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} aria-label="Stato">
              {STATUS_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
          </div>
          {!jobs ? <div className="empty"><Spinner label="Caricamento dei job" /></div> : !shown.length ? (
            <Empty icon="activity" title="Nessun job">{labelFilter.kind !== "all" || status !== "all" ? "Nessun job con questo filtro." : undefined}</Empty>
          ) : (
            <div className="table-wrap" style={{ maxHeight: "70vh", overflowY: "auto" }}>
              <table className="table">
                <tbody>
                  {shown.map((j) => {
                    const isOpen = open.has(j.id);
                    return (
                      <Fragment key={j.id}>
                        <tr className={`clickable${j.id === selected ? " selected" : ""}${isOpen ? " row-open" : ""}`} aria-current={j.id === selected || undefined} {...rowActivate(() => choose(j.id))}>
                          <td style={{ width: 30, verticalAlign: "top" }}>
                            <ExpandButton open={isOpen} onClick={() => open.toggle(j.id)} label={`dettagli di ${j.title || KIND_LABEL[j.kind]}`} />
                          </td>
                          <td>
                            <div className="row" style={{ gap: 6 }}>
                              <FavoriteStar type="job" id={j.id} value={!!j.favorite} onChange={patchLabels} size={13} />
                              <StatusBadge status={j.status} /><span className="shell-sub">{KIND_LABEL[j.kind]}</span>
                              <TagChips tags={j.tags} max={3} onPick={(t) => setLabelFilter({ kind: "tag", tag: t })} />
                            </div>
                            <div className="ellipsis small" style={{ maxWidth: 320, marginTop: 3 }} title={j.title}>{j.title}</div>
                            {isActive(j.status) && <div style={{ marginTop: 4 }}><Progress value={j.progress} /></div>}
                            {isActive(j.status) && fmtEta(j.eta_seconds) && <div className="shell-sub" style={{ marginTop: 2 }}>{fmtEta(j.eta_seconds)}</div>}
                            {queueLabel(j) && <div className="shell-sub" style={{ marginTop: 2 }}>{queueLabel(j)}</div>}
                          </td>
                          <td className="nowrap shell-sub" style={{ verticalAlign: "top" }} title={fmtDate(j.created_at)}>{relTime(j.created_at)}</td>
                        </tr>
                        {isOpen && (
                          <tr className="row-detail">
                            <td colSpan={3}><JobPeek job={j} onOpen={choose} /></td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div>{selected ? <Detail key={selected} id={selected} labels={itemLabels(jobs?.find((j) => j.id === selected))} onDeleted={() => { setParams(labelKey ? { tag: labelKey } : {}); reload(); }} onChanged={reload} onLabels={patchLabels} /> : <Card><Empty icon="activity" title="Seleziona un job per i dettagli">Clic su una riga per stato, log in tempo reale, parametri e file; la freccia mostra un'anteprima nell'elenco.</Empty></Card>}</div>
      </div>
    </div>
  );
}
