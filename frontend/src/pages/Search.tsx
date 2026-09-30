import { Fragment, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { JobSummary, SearchEngine, SearchHit, SearchResult, Sequence, Settings } from "../api/types";
import { CachedBadge, RecomputeOption } from "../components/CacheOption";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { SequencePicker } from "../components/Pickers";
import { Badge, BarCell, Card, Empty, Field, PageHead, Segmented } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtEvalue, fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";

type SortKey = "evalue" | "identity" | "coverage" | "bitscore" | "resolution";

function HitAlignment({ hit }: { hit: SearchHit }) {
  const mid = hit.query_aligned
    .split("")
    .map((c, i) => (c === hit.subject_aligned[i] ? "|" : c !== "-" && hit.subject_aligned[i] !== "-" ? "·" : " "))
    .join("");
  const rows = [];
  for (let i = 0; i < hit.query_aligned.length; i += 80) {
    rows.push(
      <div key={i} style={{ marginBottom: 8 }}>
        <div>Query    {hit.query_aligned.slice(i, i + 80)}</div>
        <div className="muted">         {mid.slice(i, i + 80)}</div>
        <div>{hit.template_id.padEnd(9, " ").slice(0, 9)}{hit.subject_aligned.slice(i, i + 80)}</div>
      </div>,
    );
  }
  return (
    <div className="stack-sm">
      <div className="small text-2">
        Query {hit.query_start}–{hit.query_end} · template {hit.subject_start}–{hit.subject_end} (di {hit.subject_length}) · positivi {fmtNum(hit.positives)}%
        {hit.organism && ` · ${hit.organism}`}
      </div>
      <pre className="seq-block" style={{ whiteSpace: "pre", maxHeight: 220 }}>{rows}</pre>
    </div>
  );
}

function CoverageBar({ hit, qlen }: { hit: SearchHit; qlen: number }) {
  const left = ((hit.query_start - 1) / qlen) * 100;
  const width = ((hit.query_end - hit.query_start + 1) / qlen) * 100;
  return (
    <div title={`Residui ${hit.query_start}–${hit.query_end} della query`} style={{ position: "relative", height: 8, background: "var(--panel-3)", borderRadius: 4, minWidth: 90 }}>
      <div style={{ position: "absolute", left: `${left}%`, width: `${width}%`, top: 0, bottom: 0, borderRadius: 4, background: hit.coverage >= 80 ? "var(--ok)" : hit.coverage >= 50 ? "var(--accent)" : "var(--warn)" }} />
    </div>
  );
}

export default function Search() {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [query, setQuery] = useState({ name: "", sequence: "" });
  const [engine, setEngine] = useState<SearchEngine>("rcsb");
  const [evalue, setEvalue] = useState(0.001);
  const [maxHits, setMaxHits] = useState(50);
  const [recompute, setRecompute] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "evalue", dir: 1 });
  const [filter, setFilter] = useState("");
  const [fetching, setFetching] = useState<string | null>(null);
  const { data: library, reload: reloadLibrary } = useAsync(() => api.templates(), []);
  const { data: recent } = useAsync<JobSummary[]>(() => api.jobs({ kind: "search", project_id: projectId ?? undefined, limit: 10 }), [projectId, jobId]);
  const { job, summary, log, cancel } = useJob<SearchResult>(jobId, { withLog: true });

  useEffect(() => {
    api.settings().then((s: Settings) => setEngine(s.default_search_engine)).catch(() => undefined);
  }, []);
  useEffect(() => {
    const sid = params.get("seq");
    if (sid && projectId) {
      api.sequences(projectId).then((list: Sequence[]) => {
        const s = list.find((x) => x.id === sid);
        if (s) setQuery({ name: s.name, sequence: s.sequence });
      });
    }
  }, [params, projectId]);

  const submit = async () => {
    try {
      const j = await api.search({ project_id: projectId ?? undefined, query: { name: query.name || "query", sequence: query.sequence }, engine, evalue, max_hits: maxHits, use_cache: !recompute });
      setParams({ job: j.id });
    } catch (e) {
      toast.error(e);
    }
  };

  const result = job?.status === "completed" ? job.result : null;
  const hits = useMemo(() => {
    if (!result) return [];
    const f = filter.toLowerCase();
    const list = result.hits.filter((h) => !f || `${h.template_id} ${h.title} ${h.organism}`.toLowerCase().includes(f));
    return [...list].sort((a, b) => {
      const va = a[sort.key] ?? Infinity;
      const vb = b[sort.key] ?? Infinity;
      return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
    });
  }, [result, filter, sort]);
  const inLibrary = new Set(library?.map((t) => t.id) ?? []);

  const addToLibrary = async (hit: SearchHit) => {
    setFetching(hit.pdb_id);
    try {
      const t = await api.fetchTemplate(hit.pdb_id);
      toast.push(t.cached ? `${hit.pdb_id} era già in libreria` : `${hit.pdb_id} scaricato in libreria`);
      reloadLibrary();
    } catch (e) {
      toast.error(e);
    } finally {
      setFetching(null);
    }
  };
  const header = (key: SortKey, label: string) => (
    <th className="sortable num" onClick={() => setSort({ key, dir: sort.key === key ? ((-sort.dir) as 1 | -1) : key === "evalue" || key === "resolution" ? 1 : -1 })}>
      {label} {sort.key === key ? (sort.dir === 1 ? "▲" : "▼") : ""}
    </th>
  );

  return (
    <div className="page">
      <PageHead title="Ricerca template" lead="Cerca strutture sperimentali omologhe alla sequenza bersaglio. RCSB è immediato; NCBI BLAST è più sensibile per omologie lontane ma richiede alcuni minuti; BLAST+ e MMseqs2 locali funzionano senza internet sul database pdbaa." />
      <div className="split">
        <div className="stack">
          <Card title="Query">
            <div className="stack">
              <SequencePicker value={query} onChange={(v) => setQuery({ name: v.name, sequence: v.sequence })} />
              <Field label="Motore">
                <Segmented value={engine} onChange={setEngine} options={[{ value: "rcsb", label: "RCSB (rapido)" }, { value: "ncbi", label: "NCBI BLAST" }, { value: "local", label: "BLAST+ locale" }, { value: "mmseqs", label: "MMseqs2 locale" }]} />
              </Field>
              <div className="grid grid-2">
                <Field label="E-value massimo">
                  <select className="input" value={evalue} onChange={(e) => setEvalue(Number(e.target.value))}>
                    {[1e-10, 1e-5, 1e-3, 0.01, 0.1, 1, 10].map((v) => (
                      <option key={v} value={v}>{v >= 0.01 ? v : v.toExponential(0)}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Risultati massimi">
                  <input className="input" type="number" min={5} max={500} value={maxHits} onChange={(e) => setMaxHits(Number(e.target.value))} />
                </Field>
              </div>
              <RecomputeOption checked={recompute} onChange={setRecompute} />
              <button className="btn btn-primary" disabled={!query.sequence.trim()} onClick={submit}>
                <Icon name="search" /> Avvia ricerca
              </button>
            </div>
          </Card>
          <Card title="Ricerche recenti">
            {!recent?.length ? <p className="small muted">Nessuna ricerca in questo progetto.</p> : (
              <div className="stack-sm">
                {recent.map((r) => (
                  <button key={r.id} className={`btn btn-ghost`} style={{ justifyContent: "flex-start", height: "auto", padding: "6px 10px", borderColor: r.id === jobId ? "var(--accent)" : undefined }} onClick={() => setParams({ job: r.id })}>
                    <span className="ellipsis" style={{ flex: 1, textAlign: "left" }}>{r.title}</span>
                    <span className="tiny muted">{relTime(r.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="stack">
          {jobId && <JobPanel summary={summary} log={log} onCancel={cancel} />}
          {!jobId && <Card><Empty icon="search" title="Avvia una ricerca per vedere i template omologhi" /></Card>}
          {result && (
            <Card
              title={`${result.hits.length} template trovati`}
              sub={<>{result.engine_label} · query {result.query.name} ({result.query_length} aa) <CachedBadge result={result} /></>}
              right={<input className="input" style={{ width: 220 }} placeholder="Filtra (codice, titolo, organismo)" value={filter} onChange={(e) => setFilter(e.target.value)} />}
            >
              {!hits.length ? <Empty icon="search" title="Nessun template sotto la soglia di E-value">Prova ad aumentare l'E-value o a usare NCBI BLAST.</Empty> : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Template</th>
                        <th>Descrizione</th>
                        {header("identity", "Identità")}
                        {header("coverage", "Copertura")}
                        {header("evalue", "E-value")}
                        {header("resolution", "Ris. (Å)")}
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {hits.map((h) => (
                        <Fragment key={h.template_id}>
                          <tr className="clickable" onClick={() => setExpanded(expanded === h.template_id ? null : h.template_id)}>
                            <td className="nowrap">
                              <b>{h.template_id}</b>{" "}
                              {h.template_id === result.best && <Badge tone="ok" title="Massimo identità × copertura">consigliato</Badge>}
                              {inLibrary.has(h.pdb_id) && <Badge tone="accent">in libreria</Badge>}
                            </td>
                            <td className="small" style={{ maxWidth: 320 }}>
                              <div className="ellipsis">{h.title || h.description}</div>
                              <div className="tiny muted ellipsis">{h.method}{h.organism ? ` · ${h.organism}` : ""}</div>
                            </td>
                            <td className="num"><BarCell value={h.identity} /></td>
                            <td className="num">
                              <div className="stack-sm" style={{ gap: 3 }}>
                                <CoverageBar hit={h} qlen={result.query_length} />
                                <span className="tiny">{fmtNum(h.coverage)}%</span>
                              </div>
                            </td>
                            <td className="num nowrap">{fmtEvalue(h.evalue)}</td>
                            <td className="num">{h.resolution ? fmtNum(h.resolution, 2) : "—"}</td>
                            <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                              <button className="btn btn-sm" disabled={fetching === h.pdb_id || inLibrary.has(h.pdb_id)} onClick={() => addToLibrary(h)} title="Scarica la struttura nella libreria">
                                <Icon name="download" size={13} />
                              </button>{" "}
                              <button
                                className="btn btn-sm btn-primary"
                                title="Modella con questo template"
                                onClick={() => navigate(`/modeling?name=${encodeURIComponent(result.query.name)}&seqtext=${encodeURIComponent(result.query.sequence)}&t=${h.pdb_id}:${h.chain}`)}
                              >
                                <Icon name="cube" size={13} />
                              </button>
                            </td>
                          </tr>
                          {expanded === h.template_id && (
                            <tr>
                              <td colSpan={7} style={{ background: "var(--panel-2)" }}><HitAlignment hit={h} /></td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="tiny muted" style={{ marginTop: 8 }}>
                Identità &lt; 25% o copertura &lt; 40%: zona crepuscolare, modello a basso affidabilità. L'E-value della ricerca RCSB è stimato con la statistica di Karlin-Altschul.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
