// «Riesegui da pacchetto» (F9.4): caricamento e verifica di un pacchetto di riproducibilità, anteprima del manifest,
// controlli preventivi, avvio e rapporto del confronto. Logica pura in lib/rerun.ts.
import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { JobSummary, PackageSummary, RerunResult } from "../api/types";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { LaunchButton, PreflightPanel, usePreflight } from "../components/Preflight";
import { Alert, Badge, Card, Disclosure, DropZone, Empty, PageHead, Spinner, StatusBadge } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtBytes, fmtDate, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import {
  contentText, FILE_STATUS_LABEL, FILE_STATUS_TONE, fileDetail, fmtDelta, fmtValue, paramRows, rerunPreflight, ROLE_LABEL,
  scientificLine, sortFiles, toleranceText, VERDICT_HELP, VERDICT_LABEL, VERDICT_TONE, versionRows,
} from "../lib/rerun";

function ManifestPreview({ summary }: { summary: PackageSummary }) {
  const versions = versionRows(summary, summary.current);
  const params = paramRows(summary.params);
  const seeds = Object.entries(summary.seeds ?? {}).filter(([k]) => k !== "note");
  return (
    <div className="stack">
      <dl className="kv">
        <dt>Calcolo</dt><dd>{summary.kind_label} · <b>{summary.job.title || summary.job.id}</b></dd>
        <dt>Job originale</dt><dd className="mono">{summary.job.id}</dd>
        <dt>Concluso</dt><dd>{fmtDate(summary.job.finished_at ?? null)}{summary.job.duration_seconds != null ? ` · ${fmtValue(summary.job.duration_seconds)} s di calcolo` : ""}</dd>
        <dt>Pacchetto</dt><dd>creato {fmtDate(summary.created_at)} · {contentText(summary.files.by_role)} · {fmtBytes(summary.files.bytes)}</dd>
        {seeds.length > 0 && <><dt>Semi casuali</dt><dd className="mono">{seeds.map(([k, v]) => `${k} ${fmtValue(v)}`).join(" · ")}</dd></>}
        {summary.cache?.result_from_cache && <><dt>Cache</dt><dd>il risultato originale veniva dalla cache dei calcoli ({fmtDate(summary.cache.cached_at)}): la riesecuzione lo ricalcola</dd></>}
        <dt>Impronta</dt><dd className="mono tiny" title="SHA-256 del manifest: verificata insieme ai checksum di ogni file">{summary.manifest_sha256.slice(0, 16)}…</dd>
      </dl>
      {typeof summary.seeds?.note === "string" && <p className="small text-2">{summary.seeds.note}</p>}
      <div>
        <h4 style={{ marginBottom: 6 }}>Versioni</h4>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Componente</th><th>Nel pacchetto</th><th>Qui</th></tr></thead>
            <tbody>
              {versions.map((r) => (
                <tr key={r.label}>
                  <td>{r.label}</td>
                  <td className="mono small">{r.packaged}</td>
                  <td className="mono small">{r.same ? r.current : <Badge tone="warn" title="Versione diversa: un avviso, non un errore">{r.current}</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {params.length > 0 && (
        <Disclosure title="Parametri del job" extra={`${Object.keys(summary.params ?? {}).length} voci`}>
          <dl className="kv shell-kv-params">
            {params.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
          </dl>
        </Disclosure>
      )}
      {summary.remote.length > 0 && (
        <div className="stack-sm">
          <h4 style={{ margin: 0 }}>Input da servizi remoti</h4>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {summary.remote.map((r, i) => <li key={i}>{r.service}: {r.what} ({r.date ? fmtDate(r.date) : "data non nota"})</li>)}
          </ul>
        </div>
      )}
      <Disclosure title="File del pacchetto" extra={`${summary.files.count} · ${fmtBytes(summary.files.bytes)}`}>
        <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
          <table className="table">
            <tbody>
              {summary.files.list.map((f) => (
                <tr key={f.path}><td className="mono small">{f.path}</td><td className="shell-sub">{ROLE_LABEL[f.role] ?? f.role}</td><td className="num small">{fmtBytes(f.size)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Disclosure>
    </div>
  );
}

/** Il job originale del pacchetto è in questa installazione? null mentre si verifica; un 404 (pacchetto di un altro
 *  computer, job eliminato) dà false: l'id resta come testo, senza un link a una pagina vuota. */
function useLocalJob(id: string | null | undefined): boolean | null {
  const { data, error } = useAsync<boolean>(async () => (id ? !!(await api.jobStatus(id)) : false), [id]);
  return error ? false : data;
}

function RerunReport({ jobId, result }: { jobId: string; result: RerunResult }) {
  const local = useLocalJob(result.original.job_id);
  const files = sortFiles(result.files);
  const c = result.counts;
  return (
    <Card
      icon="compare"
      title="Confronto con il pacchetto"
      right={
        <>
          <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.report_file, true)} title="Rapporto leggibile salvato nel job"><Icon name="file" size={13} /> Rapporto</a>
          <a className="btn btn-sm" href={api.archiveUrl(jobId)}><Icon name="download" size={13} /> Archivio</a>
        </>
      }
    >
      <div className="stack">
        <div className="row" style={{ alignItems: "flex-start" }}>
          <Badge tone={VERDICT_TONE[result.verdict]} title={VERDICT_HELP[result.verdict]}>{VERDICT_LABEL[result.verdict]}</Badge>
          <span style={{ flex: 1 }}>{result.summary}</span>
        </div>
        <p className="small text-2" style={{ margin: 0 }}>
          Pacchetto del job {local ? <Link to={`/jobs?id=${result.original.job_id}`} className="mono">{result.original.job_id}</Link> : <span className="mono" title={local === false ? "Job non presente in questa installazione" : undefined}>{result.original.job_id}</span>}{local === false ? " (non in questa installazione)" : ""} «{result.original.title}», concluso {fmtDate(result.original.finished_at)} con BioLoom {result.original.bioloom ?? "?"}; riesecuzione con BioLoom {result.bioloom} in {fmtValue(result.duration_seconds)} s, file in <span className="mono">{result.folder}/</span>.
        </p>
        {result.warnings.length > 0 && (
          <Alert tone="warn">
            <ul style={{ margin: 0, paddingLeft: 18 }}>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </Alert>
        )}
        {result.scientific && (
          <Alert tone={result.scientific.within ? "info" : "err"}>
            <div className="stack-sm">
              <span>{result.scientific.text}</span>
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{result.scientific.checks.map((x, i) => <li key={i}>{scientificLine(x)}</li>)}</ul>
            </div>
          </Alert>
        )}
        {result.structures.length > 0 && (
          <div>
            <h4 style={{ marginBottom: 6 }}>Strutture (coordinate senza sovrapposizione)</h4>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>File</th><th className="num">Atomi in comune</th><th className="num">RMSD (Å)</th><th className="num">Catena principale (Å)</th><th className="num">Scarto massimo (Å)</th><th /></tr></thead>
                <tbody>
                  {result.structures.map((s) => (
                    <tr key={s.path}>
                      <td className="mono small">{s.path}</td>
                      <td className="num">{s.coordinates.common} / {s.coordinates.atoms}</td>
                      <td className="num">{fmtValue(s.coordinates.rmsd)}</td>
                      <td className="num">{fmtValue(s.coordinates.backbone_rmsd ?? null)}</td>
                      <td className="num" title={s.coordinates.max_atom ?? undefined}>{fmtValue(s.coordinates.max_deviation)}</td>
                      <td>{s.coordinates.within ? <Badge tone="ok">entro la tolleranza</Badge> : <Badge tone="err">oltre</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {result.metrics.map((section) => (
          <div key={section.title}>
            <h4 style={{ marginBottom: 6 }}>{section.title}</h4>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Metrica</th><th className="num">Originale</th><th className="num">Riesecuzione</th><th className="num">Differenza</th></tr></thead>
                <tbody>
                  {section.rows.map((r) => (
                    <tr key={r.key} style={r.within ? undefined : { background: "var(--err-soft)" }}>
                      <td>{r.label}</td>
                      <td className="num mono small">{fmtValue(r.original)}</td>
                      <td className="num mono small">{fmtValue(r.rerun)}</td>
                      <td className="num small">{r.within ? <span className="text-2">{fmtDelta(r) || "uguale"}</span> : <Badge tone="err">{fmtDelta(r) || "diverso"}</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
        <Disclosure title="File" extra={`${c.identical} identici · ${c.equivalent} equivalenti · ${c.different} diversi · ${c.missing} mancanti · ${c.extra} in più`}
          defaultOpen={c.different + c.missing + c.extra > 0}>
          {files.length > 0 ? (
            <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
              <table className="table">
                <tbody>
                  {files.map((f) => (
                    <tr key={f.path}>
                      <td style={{ width: 110 }}><Badge tone={FILE_STATUS_TONE[f.status]}>{FILE_STATUS_LABEL[f.status]}</Badge></td>
                      <td className="mono small">{f.path}{!f.principal && <span className="shell-sub"> (accessorio)</span>}</td>
                      <td className="small text-2">{fileDetail(f)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <span>Nessun file da confrontare.</span>}
        </Disclosure>
        <p className="small text-2">{toleranceText(result.tolerance)}</p>
      </div>
    </Card>
  );
}

function RerunJob({ id, onStatus }: { id: string; onStatus: (status: string) => void }) {
  const { job, summary, log, cancel } = useJob<RerunResult>(id, { withLog: true });
  const status = summary?.status ?? "";
  useEffect(() => {
    if (status) onStatus(status); // l'elenco delle riesecuzioni recenti si aggiorna a fine job
  }, [status, onStatus]);
  if (!summary) return <Card><div className="row"><Spinner /> Caricamento della riesecuzione…</div></Card>;
  return (
    <div className="stack">
      <JobPanel summary={summary} log={log} onCancel={cancel} />
      {summary.kind !== "rerun" && <Alert tone="warn">Il job {id} non è una riesecuzione da pacchetto.</Alert>}
      {summary.status === "completed" && job?.result && summary.kind === "rerun" && <RerunReport jobId={id} result={job.result} />}
    </div>
  );
}

export default function Rerun() {
  const { projectId } = useProject();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [summary, setSummary] = useState<PackageSummary | null>(null);
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const preflight = usePreflight(rerunPreflight(summary), 200);
  const [jobStatus, setJobStatus] = useState("");
  const { data: recent } = useAsync<JobSummary[]>(() => api.jobs({ kind: "rerun", limit: 10 }), [jobId, jobStatus]);

  const load = async (file: File) => {
    setLoading(true);
    setError(null);
    setSummary(null);
    setFileName(file.name);
    try {
      setSummary(await api.inspectPackage(file));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };
  const launch = async () => {
    if (!summary) return;
    const job = await api.rerunPackage({ packageId: summary.package_id, projectId });
    setParams({ job: job.id });
    toast.push("Riesecuzione avviata: alla fine il confronto con i risultati del pacchetto");
  };

  return (
    <div className="page">
      <PageHead title="Riesegui da pacchetto"
        lead="Carica un pacchetto riproducibile (Job → «Pacchetto riproducibile»): BioLoom verifica manifest e checksum, ricrea il calcolo con gli stessi parametri e input (cache ignorata, nessun download) e confronta i risultati con quelli del pacchetto." />
      <div className="split">
        <div className="stack">
          <Card icon="upload" title="Pacchetto">
            <div className="stack">
              <DropZone accept=".zip,application/zip" onFile={load}
                label={loading ? <span className="row" style={{ gap: 6, justifyContent: "center" }}><Spinner /> Verifica di {fileName}…</span>
                  : fileName && summary ? <>Pacchetto <b>{fileName}</b> verificato: scegline un altro o trascinalo qui</> : <>Scegli il pacchetto ZIP oppure trascinalo qui</>} />
              {error && <Alert tone="err">Pacchetto rifiutato: {error}</Alert>}
              {summary && <ManifestPreview summary={summary} />}
              {summary && !summary.rerun.supported && <Alert tone="err">{summary.rerun.reason}</Alert>}
              {summary?.rerun.supported && (
                <>
                  <PreflightPanel state={preflight} />
                  <LaunchButton preflight={preflight} onLaunch={launch}>Riesegui e confronta</LaunchButton>
                </>
              )}
              <p className="small text-2">
                Gli script MODELLER del pacchetto sono solo documentazione: la riesecuzione li rigenera dai parametri, validati come
                quelli di un calcolo nuovo. Da riga di comando: <span className="mono">python programma/bioloom.py rerun &lt;pacchetto.zip&gt; --data &lt;cartella&gt;</span> (dalla cartella di BioLoom).
              </p>
            </div>
          </Card>
          <Card icon="activity" title="Riesecuzioni recenti">
            {!recent?.length ? <p className="small text-2">Nessuna riesecuzione.</p> : (
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
          {jobId ? <RerunJob key={jobId} id={jobId} onStatus={setJobStatus} />  : <Card><Empty icon="refresh" title="Carica un pacchetto per rieseguirlo, o apri una riesecuzione recente">Alla fine della riesecuzione qui compare il confronto con i risultati del pacchetto: strutture, metriche e file.</Empty></Card>}
        </div>
      </div>
    </div>
  );
}
