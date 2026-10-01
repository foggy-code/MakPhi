import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { EntityLabels, JobKind, JobSummary, ResumeScope } from "../api/types";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { ReproButton } from "../components/ReproButton";
import { FavoriteStar, LabelEditor, LabelFilterSelect, TagChips } from "../components/Labels";
import { Card, Confirm, Empty, PageHead, Progress, StatusBadge } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtBytes, fmtDate, fmtDuration, fmtEta, isActive, KIND_LABEL, queueLabel } from "../lib/format";
import { useAsync, useInterval, useJob, useLiveJobList } from "../lib/hooks";
import { listPollInterval } from "../lib/live";
import { filterParams, filterValue, itemLabels, matchesFilter, parseFilterValue, type ItemLabels, type LabelFilter } from "../lib/tags";

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

function Detail({ id, labels, onDeleted, onChanged, onLabels }: { id: string; labels?: ItemLabels; onDeleted: () => void; onChanged: () => void; onLabels: (l: EntityLabels) => void }) {
  const toast = useToast();
  const { job, summary, log, cancel, resume, error, gone } = useJob(id, { withLog: true });
  const [confirm, setConfirm] = useState(false);
  // link a un job che non è in questa installazione (pacchetto di un altro computer, job eliminato)
  if (!summary && (gone || error)) {
    return gone
      ? <Card><Empty icon="activity" title="Job non trovato in questa installazione">Il job {id} non esiste qui: è stato eliminato o viene da un'altra installazione (per esempio il job originale di un pacchetto di riproducibilità).</Empty></Card>
      : <Card><Empty icon="activity" title="Job non letto">{error}</Empty></Card>;
  }
  if (!summary) return null;
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
  return (
    <div className="stack">
      <JobPanel summary={summary} log={log} onCancel={cancel} resume={job} onResume={doResume} compact defaultLogOpen />
      <Card
        title="Dettagli"
        right={
          <>
            <Link className="btn btn-sm btn-primary" to={OPEN_IN[summary.kind](summary.id)}>Apri risultati</Link>
            <a className="btn btn-sm" href={api.archiveUrl(id)}><Icon name="download" size={14} /> Archivio</a>
            <ReproButton jobId={id} kind={summary.kind} status={summary.status} />
            <button className="btn btn-sm btn-danger" disabled={isActive(summary.status)} onClick={() => setConfirm(true)}><Icon name="trash" size={14} /></button>
          </>
        }
      >
        <dl className="kv">
          <dt>Etichette</dt><dd><LabelEditor type="job" id={id} initial={labels} onChange={onLabels} /></dd>
          <dt>Tipo</dt><dd>{KIND_LABEL[summary.kind]}</dd>
          <dt>Creato</dt><dd>{fmtDate(summary.created_at)}</dd>
          <dt>Durata</dt><dd>{fmtDuration(summary.started_at, summary.finished_at)}</dd>
          <dt>Identificativo</dt><dd className="mono">{summary.id}</dd>
        </dl>
        {job?.files && job.files.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <h4 style={{ marginBottom: 6 }}>File prodotti ({job.files.length})</h4>
            <div className="table-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
              <table className="table">
                <tbody>
                  {job.files.map((f) => (
                    <tr key={f.path}>
                      <td className="mono">{f.path}</td>
                      <td className="num small">{fmtBytes(f.size)}</td>
                      <td style={{ width: 40 }}><a className="btn btn-sm btn-ghost" href={api.jobFileUrl(id, f.path, true)}><Icon name="download" size={13} /></a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>
      {confirm && (
        <Confirm text="Eliminare il job e tutti i suoi file?" confirmLabel="Elimina" onCancel={() => setConfirm(false)}
          onConfirm={() => api.deleteJob(id).then(() => { setConfirm(false); toast.push("Job eliminato"); onDeleted(); }).catch(toast.error)} />
      )}
    </div>
  );
}

export default function Jobs() {
  const { projectId } = useProject();
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const [scope, setScope] = useState<"project" | "all">("project");
  const [kind, setKind] = useState("");
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
  const patchLabels = (l: EntityLabels) => {
    if (!jobs) return;
    const next = jobs.map((j) => (j.id === l.entity_id ? { ...j, tags: l.tags, favorite: l.favorite } : j));
    setData(next.filter((j) => j.id === selected || matchesFilter(j, labelFilter)));
  };
  return (
    <div className="page">
      <PageHead title="Job" lead="Tutti i calcoli eseguiti in background, con stato, log, file prodotti e durata. I job attivi possono essere annullati."
        actions={<Link className="btn" to="/rerun" title="Ricrea un calcolo da un pacchetto riproducibile e confronta i risultati"><Icon name="refresh" size={14} /> Riesegui da pacchetto</Link>} />
      <div className="split">
        <Card
          title="Elenco"
          right={
            <>
              <select className="input" style={{ width: 140, minHeight: 28 }} value={scope} onChange={(e) => setScope(e.target.value as "project" | "all")}>
                <option value="project">Questo progetto</option>
                <option value="all">Tutti i progetti</option>
              </select>
              <select className="input" style={{ width: 150, minHeight: 28 }} value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="">Tutti i tipi</option>
                {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <LabelFilterSelect type="job" value={labelFilter} onChange={setLabelFilter} width={150} />
            </>
          }
        >
          {!jobs?.length ? <Empty icon="activity" title="Nessun job">{labelFilter.kind !== "all" && "Nessun job con questo filtro."}</Empty> : (
            <div className="table-wrap" style={{ maxHeight: "70vh", overflowY: "auto" }}>
              <table className="table">
                <tbody>
                  {jobs.map((j) => (
                    <tr key={j.id} className={`clickable ${j.id === selected ? "selected" : ""}`} onClick={() => setParams((p) => { const next = new URLSearchParams(p); next.set("id", j.id); return next; })}>
                      <td>
                        <div className="row" style={{ gap: 6 }}>
                          <FavoriteStar type="job" id={j.id} value={!!j.favorite} onChange={patchLabels} size={13} />
                          <StatusBadge status={j.status} /><span className="tiny muted">{KIND_LABEL[j.kind]}</span>
                          <TagChips tags={j.tags} max={3} onPick={(t) => setLabelFilter({ kind: "tag", tag: t })} />
                        </div>
                        <div className="ellipsis small" style={{ maxWidth: 330, marginTop: 3 }}>{j.title}</div>
                        {isActive(j.status) && <div style={{ marginTop: 4 }}><Progress value={j.progress} /></div>}
                        {isActive(j.status) && fmtEta(j.eta_seconds) && <div className="tiny muted" style={{ marginTop: 2 }}>{fmtEta(j.eta_seconds)}</div>}
                        {queueLabel(j) && <div className="tiny muted" style={{ marginTop: 2 }}>{queueLabel(j)}</div>}
                      </td>
                      <td className="tiny muted nowrap">{fmtDate(j.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div>{selected ? <Detail key={selected} id={selected} labels={itemLabels(jobs?.find((j) => j.id === selected))} onDeleted={() => { setParams(labelKey ? { tag: labelKey } : {}); reload(); }} onChanged={reload} onLabels={patchLabels} /> : <Card><Empty icon="activity" title="Seleziona un job per i dettagli" /></Card>}</div>
      </div>
    </div>
  );
}
