import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { JobSummary, SearchEngine, SearchHit, SearchResult, Sequence, Settings, SuggestedTemplate } from "../api/types";
import { CachedBadge, RecomputeOption } from "../components/CacheOption";
import { FeatureMap } from "../components/charts";
import { CoverageAlerts, useCoverageCheck } from "../components/CoverageAlerts";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { SequencePicker } from "../components/Pickers";
import { Alert, Badge, BarCell, Card, Disclosure, Empty, ExpandAll, ExpandButton, Field, PageHead, Segmented, StatusBadge, useExpanded } from "../components/ui";
import { HitCoverageBar, HitPanel } from "../components/WorkflowParts";
import {
  autoAccessionFor, type AutoAccession, followAccession, isUniProtAccession, normalizeAccession, plddtLabel, plddtTone,
} from "../lib/alphafold";
import { useProject, useToast } from "../lib/context";
import { assessmentMap, coverageBadge, featureTracks, hitRanges, missingDomainsNote, targetAccession } from "../lib/disorder";
import { fmtEvalue, fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { modelingLink, rankTone, rankTooltip, suggestedRoles, suggestionTemplates } from "../lib/templateRank";
import { hitTracks, remoteSearchNotice } from "../lib/workflowView";

type SortKey = "rank_score" | "evalue" | "identity" | "coverage" | "bitscore" | "resolution";

/** colonne della tabella dei risultati (riga di dettaglio) */
const COLUMNS = 9;
/** template nella mappa della copertura, nell'ordine della tabella */
const MAP_HITS = 20;
/** righe aperte al più da «Apri tutti» (ogni dettaglio ha allineamento e tabelle) */
const EXPAND_ALL = 100;

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
  // motore AlphaFold DB: accessione UniProt facoltativa, precompilata dalle sequenze importate da UniProt; quella
  // precompilata segue la sequenza da cui viene (followAccession), una scritta a mano resta
  const [uniprot, setUniprot] = useState("");
  const autoAcc = useRef<AutoAccession | null>(null);
  const prefill = (s: Sequence | null | undefined) => {
    autoAcc.current = s ? autoAccessionFor(s) : null;
    setUniprot(autoAcc.current?.accession ?? "");
  };
  const open = useExpanded<string>();
  // predefinito: punteggio composito decrescente (i risultati delle versioni precedenti, senza punteggio, restano
  // nell'ordine del motore)
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "rank_score", dir: -1 });
  const [filter, setFilter] = useState("");
  const [fetching, setFetching] = useState<string | null>(null);
  const { data: library, reload: reloadLibrary } = useAsync(() => api.templates(), []);
  const { data: projectSeqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const uniprotInvalid = engine === "alphafold" && !!uniprot.trim() && !isUniProtAccession(uniprot);
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
        if (s) {
          setQuery({ name: s.name, sequence: s.sequence });
          prefill(s);
        }
      });
    }
  }, [params, projectId]);

  const submit = async () => {
    try {
      const j = await api.search({
        project_id: projectId ?? undefined, query: { name: query.name || "query", sequence: query.sequence }, engine, evalue, max_hits: maxHits, use_cache: !recompute,
        uniprot: engine === "alphafold" ? normalizeAccession(uniprot) : undefined,
      });
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
  const roles = useMemo(() => (result ? suggestedRoles(result) : new Map<string, SuggestedTemplate | null>()), [result]);
  const suggestedLink = result ? modelingLink(result, suggestionTemplates(result)) : "";
  // Disordine e domini del bersaglio rispetto al tratto allineato da ogni hit, prima di modellare (F5.5)
  const coverageRequest = useMemo(() => {
    if (!result?.hits.length) return null;
    const accession = result.uniprot && isUniProtAccession(result.uniprot) ? normalizeAccession(result.uniprot) : targetAccession(result.query, projectSeqs);
    return { sequence: result.query.sequence, accession: accession || null, ranges: hitRanges(result.hits.slice(0, 500)) };
  }, [result, projectSeqs]);
  const coverage = useCoverageCheck(coverageRequest, 0);
  const assessments = useMemo(() => assessmentMap(coverage.check), [coverage.check]);
  const bestAssessment = result?.best ? assessments.get(result.best) : undefined;
  const bestBadge = coverageBadge(bestAssessment);
  const domainsNote = coverage.check && !coverage.check.note ? missingDomainsNote(coverage.check) : null;
  // mappa della copertura: disordine e domini del bersaglio sopra, poi i primi template nell'ordine della tabella
  const mapTracks = useMemo(() => {
    const check = coverage.check;
    const target = check?.disorder ? featureTracks({ disorder: check.disorder, domains: check.domains_info?.available ? check.domains : null }) : [];
    return [...target, ...hitTracks(hits, MAP_HITS)];
  }, [hits, coverage.check]);

  const addToLibrary = async (hit: SearchHit) => {
    setFetching(hit.pdb_id);
    try {
      // modelli AlphaFold: pdb_id è già l'id della libreria (AF-<ACC>-F<n>)
      const t = hit.predicted ? await api.fetchAlphaFold(hit.pdb_id) : await api.fetchTemplate(hit.pdb_id);
      toast.push(t.cached ? `${hit.pdb_id} era già in libreria` : `${hit.pdb_id} scaricato in libreria`);
      reloadLibrary();
    } catch (e) {
      toast.error(e);
    } finally {
      setFetching(null);
    }
  };
  const header = (key: SortKey, label: string) => (
    <th className="sortable num" aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
      onClick={() => setSort({ key, dir: sort.key === key ? ((-sort.dir) as 1 | -1) : key === "evalue" || key === "resolution" ? 1 : -1 })}>
      {label} {sort.key === key ? (sort.dir === 1 ? "▲" : "▼") : ""}
    </th>
  );
  const expandable = hits.slice(0, EXPAND_ALL).map((h) => h.template_id);
  const allOpen = expandable.length > 0 && expandable.every((id) => open.has(id));

  return (
    <div className="page">
      <PageHead title="Ricerca template" lead="Cerca strutture sperimentali omologhe alla sequenza bersaglio. RCSB è immediato; NCBI BLAST è più sensibile per omologie lontane ma richiede alcuni minuti; BLAST+ e MMseqs2 locali funzionano senza internet sul database pdbaa. AlphaFold DB offre modelli predetti per le proteine senza strutture sperimentali omologhe." />
      <div className="split">
        <div className="stack">
          <Card title="Query" icon="search" sub="sequenza, motore e soglie">
            <div className="stack">
              <SequencePicker
                value={query}
                onChange={(v) => {
                  setQuery({ name: v.name, sequence: v.sequence });
                  if (v.id) prefill(projectSeqs?.find((s) => s.id === v.id));
                  else {
                    // incollata o da file: l'accessione precompilata per un'altra sequenza non vale più
                    const next = followAccession(uniprot, autoAcc.current, v);
                    autoAcc.current = next.auto;
                    if (next.uniprot !== uniprot) setUniprot(next.uniprot);
                  }
                }}
              />
              <Field label="Motore" hint={remoteSearchNotice(engine) ?? undefined}>
                <Segmented label="Motore di ricerca" value={engine} onChange={setEngine} options={[{ value: "rcsb", label: "RCSB (rapido)" }, { value: "ncbi", label: "NCBI BLAST" }, { value: "local", label: "BLAST+ locale" }, { value: "mmseqs", label: "MMseqs2 locale" }, { value: "alphafold", label: "AlphaFold DB" }]} />
              </Field>
              {engine === "alphafold" && (
                <Field
                  label="Accessione UniProt (facoltativa)"
                  hint={uniprotInvalid
                    ? <span style={{ color: "var(--err)" }}>Accessione non valida: formato UniProtKB, es. P69905 (le isoforme non sono supportate).</span>
                    : "Con l'accessione si usano direttamente i suoi modelli AlphaFold. Senza, NCBI BLAST su Swiss-Prot trova le proteine più simili (1–5 min) e se ne prendono i modelli. L'accessione va a EBI; senza accessione la sequenza va a NCBI."}
                >
                  <input className="input" style={{ textTransform: "uppercase" }} placeholder="es. P69905" maxLength={20} value={uniprot} onChange={(e) => { autoAcc.current = null; setUniprot(e.target.value); }} />
                </Field>
              )}
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
              <button className="btn btn-primary" style={{ justifySelf: "start" }} disabled={!query.sequence.trim() || uniprotInvalid} onClick={submit}>
                <Icon name="search" /> Avvia ricerca
              </button>
            </div>
          </Card>
          <Card title="Ricerche recenti" icon="list" sub={recent?.length ? `ultime ${recent.length}` : undefined}>
            {!recent?.length ? <Empty icon="search" title="Nessuna ricerca in questo progetto">Le ricerche avviate compaiono qui e si riaprono con un clic.</Empty> : (
              <div className="stack-sm">
                {recent.map((r) => (
                  <button key={r.id} type="button" className="btn btn-ghost" aria-current={r.id === jobId ? "true" : undefined}
                    style={{ justifyContent: "flex-start", height: "auto", padding: "7px 10px", borderColor: r.id === jobId ? "var(--accent)" : undefined, background: r.id === jobId ? "var(--accent-soft)" : undefined }}
                    onClick={() => setParams({ job: r.id })}>
                    <span className="ellipsis" style={{ flex: 1, textAlign: "left", color: "var(--text)" }}>{r.title}</span>
                    {r.status !== "completed" && <StatusBadge status={r.status} />}
                    <span className="tiny text-2">{relTime(r.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="stack">
          {jobId && <JobPanel summary={summary} log={log} onCancel={cancel} />}
          {!jobId && (
            <Card>
              <Empty icon="search" title="Avvia una ricerca per vedere i template omologhi">
                Scegli la sequenza e il motore a sinistra: i risultati arrivano qui, ordinati per punteggio, con il dettaglio di ogni template.
              </Empty>
            </Card>
          )}
          {result && (
            <Card
              title={`${result.hits.length} template trovati`}
              icon="list"
              sub={<>{result.engine_label} · query {result.query.name} ({result.query_length} aa) <CachedBadge result={result} /></>}
              right={
                <>
                  <input className="input input-sm wf-filter" placeholder="Filtra (codice, titolo, organismo)" aria-label="Filtra i template" value={filter} onChange={(e) => setFilter(e.target.value)} />
                  {hits.length > 0 && (
                    <ExpandAll masculine noun="template" allOpen={allOpen} onExpand={() => open.expandAll(expandable)} onCollapse={open.collapseAll}
                      label={hits.length > EXPAND_ALL ? `Apri i primi ${EXPAND_ALL}` : undefined} />
                  )}
                </>
              }
            >
              <div className="stack">
                {result.engine === "alphafold" && !!result.notes?.length && result.hits.length > 0 && (
                  <div className="stack-sm">{result.notes.map((n, i) => <Alert key={i} tone="warn">{n}</Alert>)}</div>
                )}
                {/* cosa sono i risultati di AlphaFold DB: in chiaro, accanto ai risultati */}
                {result.engine === "alphafold" && result.hits.length > 0 && (
                  <Alert tone="info">
                    Modelli predetti da AlphaFold DB {result.uniprot ? `per l'accessione ${result.uniprot}` : "delle proteine Swiss-Prot più simili (NCBI BLAST)"}, non strutture sperimentali.
                    Identità e copertura vengono dal riallineamento locale (Smith-Waterman, BLOSUM62) sulla sequenza del modello; l'E-value è quello di BLAST o, con l'accessione, del solo confronto a coppie.
                    In modellazione i residui con pLDDT &lt; 70 (confidenza bassa, spesso regioni disordinate) vengono tolti dal template (opzione).
                  </Alert>
                )}
                {!!result.suggested?.templates.length && (
                  <div className="wf-suggested">
                    <div className="row">
                      <Icon name="check" size={15} />
                      <b>Template consigliati</b>
                      <span className="small text-2">
                        copertura complessiva {fmtNum(result.suggested.coverage, 1)}% ({result.suggested.covered_residues} di {result.suggested.query_length} residui) · al massimo {result.suggested.max_templates}
                      </span>
                      <button className="btn btn-sm btn-primary right" disabled={!suggestedLink} onClick={() => navigate(suggestedLink)} title="Apre la Modellazione con la sequenza e i template consigliati">
                        <Icon name="cube" size={13} /> Usa i consigliati per la modellazione
                      </button>
                    </div>
                    <ul className="small">
                      {result.suggested.templates.map((t) => (
                        <li key={t.template_id}>
                          <b>{t.template_id}</b> <Badge tone={t.role === "principale" ? "ok" : "info"}>{t.role}</Badge> punteggio {fmtNum(t.rank_score, 1)}: {t.reason}
                        </li>
                      ))}
                    </ul>
                    {(result.suggested.excluded.length > 0 || result.suggested.not_examined > 0) && (
                      <Disclosure title="Candidati esclusi" extra={`${result.suggested.excluded.length}${result.suggested.not_examined ? ` + ${result.suggested.not_examined} non esaminati` : ""}`}>
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {result.suggested.excluded.map((e) => <li key={e.template_id}><b>{e.template_id}</b>: {e.reason}</li>)}
                        </ul>
                        {result.suggested.not_examined > 0 && <p style={{ marginTop: 6 }}>{result.suggested.not_examined} candidati non esaminati: numero massimo di template raggiunto.</p>}
                      </Disclosure>
                    )}
                  </div>
                )}
                {result.best && result.hits.length > 0 && (
                  <Disclosure
                    title={`Disordine e domini del bersaglio con il template consigliato ${result.best}`}
                    extra={coverage.loading ? "verifica in corso…" : bestBadge || domainsNote ? (
                      <>
                        {bestBadge && <Badge tone="warn" title={bestBadge.title}>{bestBadge.label}</Badge>}
                        {domainsNote && <Badge title={domainsNote}>domini non verificati</Badge>}
                      </>
                    ) : undefined}
                    defaultOpen={!!bestBadge}
                  >
                    <CoverageAlerts check={coverage.check} assessment={bestAssessment} loading={coverage.loading} error={coverage.error} />
                  </Disclosure>
                )}
                {!hits.length ? (
                  result.hits.length ? <Empty icon="search" title="Nessun template corrisponde al filtro">Cancella o cambia il testo del filtro.</Empty>
                    : result.engine === "alphafold" ? (
                      // l'E-value non c'entra (con l'accessione non si usa): si mostra il perché
                      <Empty icon="search" title="Nessun modello AlphaFold trovato">
                        {result.notes?.length ? result.notes.join(" ") : "Nessuna proteina di Swiss-Prot simile ha un modello in AlphaFold DB: indica l'accessione UniProt della sequenza o prova un altro motore."}
                      </Empty>
                    )
                    : <Empty icon="search" title="Nessun template sotto la soglia di E-value">Prova ad aumentare l'E-value o a usare NCBI BLAST.</Empty>
                ) : (
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th style={{ width: 34 }} aria-label="Dettagli" />
                          <th>Template</th>
                          <th>Descrizione</th>
                          {header("rank_score", "Punteggio")}
                          {header("identity", "Identità")}
                          {header("coverage", "Copertura")}
                          {header("evalue", "E-value")}
                          {header("resolution", "Ris. (Å)")}
                          <th aria-label="Azioni" />
                        </tr>
                      </thead>
                      <tbody>
                        {hits.map((h) => {
                          const badge = coverageBadge(assessments.get(h.template_id));
                          const role = roles.get(h.template_id);
                          const isOpen = open.has(h.template_id);
                          return (
                            <Fragment key={h.template_id}>
                              <tr className={`clickable${isOpen ? " row-open" : ""}${roles.has(h.template_id) ? " wf-recommended" : ""}`} onClick={() => open.toggle(h.template_id)}>
                                <td><ExpandButton open={isOpen} onClick={() => open.toggle(h.template_id)} label={`dettagli di ${h.template_id}`} /></td>
                                <td className="nowrap">
                                  <b>{h.template_id}</b>{" "}
                                  {roles.has(h.template_id) && (role?.role === "complementare"
                                    ? <Badge tone="info" title={role.reason}>complementare</Badge>
                                    : <Badge tone="ok" title={role?.reason ?? "Miglior punteggio"}>consigliato</Badge>)}
                                  {inLibrary.has(h.pdb_id) && <> <Badge tone="accent">in libreria</Badge></>}
                                  {badge && <div style={{ marginTop: 3 }}><Badge tone={badge.tone} title={badge.title}>{badge.label}</Badge></div>}
                                  {h.predicted && (
                                    <div className="row" style={{ gap: 4, marginTop: 3 }}>
                                      <Badge tone="warn" title="Modello di AlphaFold DB: non è una struttura sperimentale">modello predetto</Badge>
                                      <Badge tone={plddtTone(h.plddt)} title="Confidenza media della predizione (0–100)">{plddtLabel(h.plddt)}</Badge>
                                    </div>
                                  )}
                                </td>
                                <td className="small" style={{ maxWidth: 320 }}>
                                  <div className="ellipsis" title={h.title || h.description}>{h.title || h.description}</div>
                                  <div className="tiny text-2 ellipsis">{h.method}{h.organism ? ` · ${h.organism}` : ""}</div>
                                </td>
                                <td className="num nowrap" title={rankTooltip(h)}>
                                  {h.rank_score !== undefined ? <Badge tone={rankTone(h.rank_score)}>{fmtNum(h.rank_score, 1)}</Badge> : "—"}
                                  {h.rank_twilight && <div className="tiny" style={{ color: "var(--warn)" }}>crepuscolare</div>}
                                </td>
                                <td className="num"><BarCell value={h.identity} /></td>
                                <td className="num">
                                  <div className="stack-sm" style={{ gap: 3 }}>
                                    <HitCoverageBar hit={h} qlen={result.query_length} />
                                    <span className="tiny">{fmtNum(h.coverage)}%</span>
                                  </div>
                                </td>
                                <td className="num nowrap">{fmtEvalue(h.evalue)}</td>
                                <td className="num">{h.resolution ? fmtNum(h.resolution, 2) : "—"}</td>
                                <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                                  <button className="btn btn-sm" disabled={fetching === h.pdb_id || inLibrary.has(h.pdb_id)} onClick={() => addToLibrary(h)}
                                    title="Scarica la struttura nella libreria" aria-label={`Scarica ${h.pdb_id} nella libreria`}>
                                    <Icon name="download" size={13} />
                                  </button>{" "}
                                  <button
                                    className="btn btn-sm btn-primary"
                                    title="Modella con questo template"
                                    aria-label={`Modella con ${h.template_id}`}
                                    onClick={() => navigate(`/modeling?name=${encodeURIComponent(result.query.name)}&seqtext=${encodeURIComponent(result.query.sequence)}&t=${h.pdb_id}:${h.chain}`)}
                                  >
                                    <Icon name="cube" size={13} />
                                  </button>
                                </td>
                              </tr>
                              {isOpen && (
                                <tr className="row-detail">
                                  <td colSpan={COLUMNS}>
                                    <HitPanel hit={h} queryLength={result.query_length}>
                                      <div className="stack-sm">
                                        <h4>Disordine e domini con questo template</h4>
                                        <CoverageAlerts check={coverage.check} assessment={assessments.get(h.template_id)} loading={coverage.loading} error={coverage.error} />
                                      </div>
                                    </HitPanel>
                                  </td>
                                </tr>
                              )}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="small text-2">Clic su una riga per tutti i dati del template, le componenti del punteggio e l'allineamento.</p>
                <Disclosure title="Come si legge il punteggio">
                  <div className="stack-sm">
                    <p>
                      Punteggio 0–100: media pesata di identità (rispetto alla curva della zona crepuscolare di Rost 1999 per la lunghezza allineata), copertura, E-value, risoluzione o pLDDT, R-free, metodo, lacune e data; ligandi e stato oligomerico sono informativi.
                      Fasce: buona ≥ 70, intermedia 40–70, scarsa &lt; 40.
                    </p>
                    <p>I consigliati sono il migliore più i template complementari che coprono almeno 10 residui nuovi o migliorano l'identità locale, senza ridondanze.</p>
                    <p>L'E-value della ricerca RCSB è stimato con la statistica di Karlin-Altschul.</p>
                  </div>
                </Disclosure>
              </div>
            </Card>
          )}
          {result && hits.length > 0 && (
            <Card title="Copertura della query" icon="align" sub={`primi ${Math.min(MAP_HITS, hits.length)} template nell'ordine della tabella${coverage.check?.disorder ? ", con disordine e domini del bersaglio" : ""}`}
              collapsible storageKey="search.coverage-map">
              <FeatureMap
                length={result.query_length}
                tracks={mapTracks}
                title={`Template allineati su ${result.query.name}`}
                titleHidden
                subtitle={`${result.query.name} · ${result.engine_label} · ${result.query_length} residui · colore per fascia del punteggio`}
                filename={`copertura_${result.query.name}`}
              />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
