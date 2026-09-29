import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { JobSummary, PipelinePage, SearchEngine, Sequence, TemplateRef } from "../api/types";
import { SequenceFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { TemplatePicker } from "../components/Pickers";
import { Viewer3D } from "../components/Viewer3D";
import { Badge, BarCell, Card, Empty, Field, Grade, Modal, PageHead, Segmented, StatusBadge } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtEvalue, fmtNum, isActive, relTime, STATUS_LABEL } from "../lib/format";
import { recordsToFasta } from "../lib/files";
import { useAsync, useInterval, useJob } from "../lib/hooks";

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

function Items({ jobId, running }: { jobId: string; running: boolean }) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState({ key: "idx", dir: "asc" });
  const [q, setQ] = useState("");
  const [view, setView] = useState<{ name: string; path: string } | null>(null);
  const { data, reload } = useAsync<PipelinePage>(() => api.pipelineItems(jobId, { page, size, status, sort: sort.key, direction: sort.dir, q }), [jobId, page, size, status, sort.key, sort.dir, q]);
  useInterval(reload, running ? 3000 : null);
  useEffect(() => {
    if (!running) reload(); // stato finale degli elementi al termine della pipeline
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  if (!data) return null;
  const counts = data.counts;
  return (
    <Card
      title="Risultati per sequenza"
      right={
        <>
          <a className="btn btn-sm" href={api.pipelineCsvUrl(jobId)}><Icon name="download" size={13} /> CSV</a>
          <a className="btn btn-sm btn-primary" href={api.pipelineZipUrl(jobId)}><Icon name="download" size={13} /> ZIP modelli</a>
        </>
      }
    >
      <div className="stack">
        <div className="row">
          {["all", "completed", "running", "searching", "modeling", "pending", "failed", "filtered", "cancelled"].map((s) => {
            const n = s === "all" ? Object.values(counts).reduce((a, b) => a + b, 0) : counts[s] ?? 0;
            if (s !== "all" && !n) return null;
            return (
              <button key={s} className={`btn btn-sm ${status === s ? "btn-primary" : ""}`} onClick={() => { setStatus(s); setPage(1); }}>
                {s === "all" ? "Tutte" : STATUS_LABEL[s]} · {n}
              </button>
            );
          })}
          <input className="input right" style={{ width: 200 }} placeholder="Cerca sequenza o template" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                {SORTS.map((s) => (
                  <th key={s.key} className={`sortable ${["identity", "coverage", "dope", "grade_score", "rama_favored", "clashscore"].includes(s.key) ? "num" : ""}`}
                    onClick={() => setSort({ key: s.key, dir: sort.key === s.key && sort.dir === "asc" ? "desc" : "asc" })}>
                    {s.label} {sort.key === s.key ? (sort.dir === "asc" ? "▲" : "▼") : ""}
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => (
                <tr key={it.idx}>
                  <td className="muted">{it.idx + 1}</td>
                  <td><b>{it.name}</b><div className="tiny muted">{it.length} aa</div></td>
                  <td>
                    <StatusBadge status={it.status} />
                    {it.status !== "completed" && it.stage && <div className="tiny muted ellipsis" style={{ maxWidth: 180 }} title={it.error ?? it.stage}>{it.error ?? it.stage}</div>}
                  </td>
                  <td className="nowrap">
                    {it.template ?? "—"} {it.twilight ? <Badge tone="warn" title="Identità o copertura sotto soglia">crepuscolare</Badge> : null}
                    {it.evalue != null && <div className="tiny muted">E {fmtEvalue(it.evalue)}</div>}
                  </td>
                  <td className="num"><BarCell value={it.identity} /></td>
                  <td className="num"><BarCell value={it.coverage} /></td>
                  <td className="num">{fmtNum(it.dope, 0)}</td>
                  <td className="num">{it.grade ? <span className="row" style={{ justifyContent: "flex-end", gap: 6 }}><Grade grade={it.grade} /> {fmtNum(it.grade_score, 0)}</span> : "—"}</td>
                  <td className="num">{fmtNum(it.rama_favored)}</td>
                  <td className="num">{fmtNum(it.clashscore, 1)}</td>
                  <td className="nowrap">
                    {it.best_model && (
                      <>
                        <button className="btn btn-sm" onClick={() => setView({ name: it.name, path: it.best_model! })} title="Visualizza"><Icon name="eye" size={13} /></button>{" "}
                        <Link className="btn btn-sm" to={`/quality?job=${jobId}&file=${encodeURIComponent(it.best_model)}`} title="Qualità"><Icon name="shield" size={13} /></Link>{" "}
                        <a className="btn btn-sm" href={api.jobFileUrl(jobId, it.best_model, true)} title="Scarica PDB"><Icon name="download" size={13} /></a>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {!data.items.length && <tr><td colSpan={11} className="muted">Nessun elemento con questo filtro.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="row small">
          <span className="muted">{data.total} elementi · pagina {data.page} di {data.pages}</span>
          <div className="right row">
            <select className="input" style={{ width: 90, minHeight: 28 }} value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(1); }}>
              {[10, 25, 50, 100].map((s) => <option key={s} value={s}>{s}/pag.</option>)}
            </select>
            <button className="btn btn-sm" disabled={data.page <= 1} onClick={() => setPage(data.page - 1)}>‹</button>
            <button className="btn btn-sm" disabled={data.page >= data.pages} onClick={() => setPage(data.page + 1)}>›</button>
          </div>
        </div>
      </div>
      {view && (
        <Modal title={`Modello di ${view.name}`} wide onClose={() => setView(null)}>
          <Viewer3D url={api.jobFileUrl(jobId, view.path)} height={520} />
        </Modal>
      )}
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
  const [mode, setMode] = useState<"auto" | "fixed">("auto");
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
  const { data: projectSeqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const { data: recent } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "pipeline", limit: 10 }), [projectId, jobId]);
  const { summary, log, cancel } = useJob(jobId, { withLog: true });
  const count = (fasta.match(/^>/gm) ?? []).length;

  const submit = async () => {
    try {
      const j = await api.pipeline({
        project_id: projectId ?? undefined,
        name: name || undefined,
        fasta,
        template_mode: mode,
        templates,
        engine,
        evalue,
        twilight: { filter: twFilter, identity: twIdentity, coverage: twCoverage },
        options: { models, refinement },
        quality,
        extended_quality: quality && extended,
        parallel,
      });
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
          <Card title="Nuova pipeline">
            <div className="stack">
              <Field label="Nome"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Es. chinasi famiglia X" /></Field>
              <Field label={`Sequenze (multi-FASTA) · ${count}`}>
                <textarea className="input" rows={8} value={fasta} onChange={(e) => setFasta(e.target.value)} placeholder={">seq1\nMKT...\n>seq2\nMAS..."} />
              </Field>
              <div className="row">
                {!!projectSeqs?.length && (
                  <button className="btn btn-sm" onClick={() => setFasta(projectSeqs.map((s) => `>${s.name}\n${s.sequence}`).join("\n"))}><Icon name="dna" size={14} /> Usa le sequenze del progetto ({projectSeqs.length})</button>
                )}
                <SequenceFileButton onLoaded={(parsed) => setFasta(recordsToFasta(parsed.records))}>Carica da file</SequenceFileButton>
                <span className="tiny muted">multi-FASTA, GenBank, UniProt… (anche .gz)</span>
              </div>
              <Field label="Template">
                <Segmented value={mode} onChange={setMode} options={[{ value: "auto", label: "Ricerca automatica" }, { value: "fixed", label: "Template fisso" }]} />
              </Field>
              {mode === "auto" ? (
                <>
                  <div className="grid grid-2">
                    <Field label="Motore">
                      <select className="input" value={engine} onChange={(e) => setEngine(e.target.value as SearchEngine)}>
                        <option value="rcsb">RCSB (rapido)</option>
                        <option value="ncbi">NCBI BLAST (lento)</option>
                        <option value="local">BLAST+ locale</option>
                        <option value="mmseqs">MMseqs2 locale</option>
                      </select>
                    </Field>
                    <Field label="E-value massimo">
                      <select className="input" value={evalue} onChange={(e) => setEvalue(Number(e.target.value))}>
                        {[1e-10, 1e-5, 1e-3, 0.01, 0.1, 1].map((v) => <option key={v} value={v}>{v >= 0.01 ? v : v.toExponential(0)}</option>)}
                      </select>
                    </Field>
                  </div>
                  <label className="check"><input type="checkbox" checked={twFilter} onChange={(e) => setTwFilter(e.target.checked)} /> Salta le sequenze in zona crepuscolare</label>
                  <div className="grid grid-2">
                    <Field label="Identità minima (%)"><input className="input" type="number" value={twIdentity} onChange={(e) => setTwIdentity(Number(e.target.value))} /></Field>
                    <Field label="Copertura minima (%)"><input className="input" type="number" value={twCoverage} onChange={(e) => setTwCoverage(Number(e.target.value))} /></Field>
                  </div>
                  <span className="tiny muted">Con il filtro disattivato le sequenze sotto soglia vengono comunque modellate e contrassegnate (utile per proteine orfane).</span>
                </>
              ) : (
                <TemplatePicker value={templates} onChange={setTemplates} />
              )}
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
              <label className="check"><input type="checkbox" checked={quality} onChange={(e) => setQuality(e.target.checked)} /> Analisi di qualità del modello migliore</label>
              {quality && <label className="check"><input type="checkbox" checked={extended} onChange={(e) => setExtended(e.target.checked)} /> Includi PROCHECK e ProSA II (qualche secondo in più per sequenza)</label>}
              <button className="btn btn-primary" disabled={count < 1 || (mode === "fixed" && !templates.length)} onClick={submit}><Icon name="play" size={14} /> Avvia pipeline</button>
            </div>
          </Card>
          <Card title="Pipeline recenti">
            {!recent?.length ? <p className="small muted">Nessuna pipeline.</p> : (
              <div className="stack-sm">
                {recent.map((r) => (
                  <button key={r.id} className="btn btn-ghost" style={{ justifyContent: "flex-start", height: "auto", padding: "6px 10px", borderColor: r.id === jobId ? "var(--accent)" : undefined }} onClick={() => setParams({ job: r.id })}>
                    <StatusBadge status={r.status} />
                    <span className="ellipsis" style={{ flex: 1, textAlign: "left" }}>{r.title}</span>
                    <span className="tiny muted">{relTime(r.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="stack">
          {jobId ? (
            <>
              <JobPanel summary={summary} log={log} onCancel={cancel} />
              <Items jobId={jobId} running={summary ? isActive(summary.status) : false} />
            </>
          ) : <Card><Empty icon="layers" title="Avvia una pipeline o aprine una recente" /></Card>}
        </div>
      </div>
    </div>
  );
}
