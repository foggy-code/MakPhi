import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { CoverageCheck, EntityLabels, InterProDomain, Sequence, SequenceAnalysis, UniProtEntry, UniProtHit } from "../api/types";
import { FeatureMap, SSStrip, StackBar, type Track } from "../components/charts";
import { SequenceFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { FavoriteStar, LabelEditor, TagChips } from "../components/Labels";
import { Alert, Badge, BarCell, Card, Confirm, CopyButton, DetailGrid, Disclosure, Empty, PageHead, Spinner, Stat, Tabs } from "../components/ui";
import { InfoChips } from "../components/WorkflowParts";
import { sequenceAccession } from "../lib/alphafold";
import { useProject, useToast } from "../lib/context";
import { disorderSummary, featureTracks, lowComplexitySummary, span } from "../lib/disorder";
import { fmtInt, fmtNum, relTime, wrapSequence } from "../lib/format";
import { recordsToFasta } from "../lib/files";
import { useAsync } from "../lib/hooks";
import { itemLabels } from "../lib/tags";
import { compositionParts, compositionRows, numberedSequence, ssLabel, ssParts, uniprotCommentLabel } from "../lib/workflowView";

// colori dai token del tema (styles/sequences.css e styles.css): ≥ 3:1 sui pannelli anche nel tema chiaro
const FEATURE_GROUPS: { label: string; types: string[]; color: string }[] = [
  { label: "Catena / peptide", types: ["Chain", "Peptide", "Propeptide", "Signal", "Transit peptide"], color: "var(--trk-chain)" },
  { label: "Domini e regioni", types: ["Domain", "Region", "Repeat", "Zinc finger", "Motif", "Coiled coil"], color: "var(--trk-region)" },
  { label: "Siti", types: ["Active site", "Binding site", "Site", "Metal binding"], color: "var(--trk-site)" },
  { label: "Eliche (sperimentali)", types: ["Helix"], color: "var(--helix)" },
  { label: "Filamenti β", types: ["Beta strand"], color: "var(--strand)" },
  { label: "Ponti disolfuro", types: ["Disulfide bond"], color: "var(--trk-disulfide)" },
  { label: "Modifiche", types: ["Modified residue", "Glycosylation", "Lipidation", "Cross-link"], color: "var(--trk-mod)" },
];

/** da quante sequenze l'elenco del progetto mostra il filtro */
const FILTER_FROM = 6;

function parseChains(chains: string): { start: number; end: number } | null {
  const m = chains.match(/=(\d+)-(\d+)/);
  return m ? { start: Number(m[1]), end: Number(m[2]) } : null;
}

export function uniprotTracks(entry: UniProtEntry, domains: InterProDomain[] | null): Track[] {
  const tracks: Track[] = [];
  for (const g of FEATURE_GROUPS) {
    const items = entry.features.filter((f) => g.types.includes(f.type)).map((f) => ({ start: f.start, end: f.end, title: `${f.type}${f.description ? ": " + f.description : ""}${f.ligand ? " (" + f.ligand + ")" : ""}` }));
    if (items.length) tracks.push({ label: g.label, color: g.color, items });
  }
  if (domains?.length) {
    tracks.push({ label: "InterPro", color: "var(--trk-domain)", items: domains.flatMap((d) => d.locations.map((l) => ({ start: l.start, end: l.end, title: `${d.name} (${d.accession}, ${d.type})` }))) });
  }
  const pdb = entry.pdb.map((p) => ({ ...p, range: parseChains(p.chains) })).filter((p) => p.range).slice(0, 12);
  pdb.forEach((p) => tracks.push({ label: `PDB ${p.id}${p.resolution ? ` ${p.resolution} Å` : ""}`, color: "var(--trk-pdb)", items: [{ start: p.range!.start, end: p.range!.end, title: `${p.id} ${p.method} ${p.chains}` }] }));
  return tracks;
}

/** Regioni del bersaglio in tabella: disordine, bassa complessità e domini, con i valori di ciascuna. */
function RegionTable({ check }: { check: CoverageCheck }) {
  const dis = check.disorder;
  if (!dis) return null;
  const domains = check.domains_info?.available ? check.domains : [];
  const rows = [
    ...dis.regions.map((r) => ({ kind: "Disordine", start: r.start, end: r.end, length: r.length, detail: `FoldIndex medio ${fmtNum(r.mean_score, 2)} · ${r.disordered} residui sotto 0` })),
    ...dis.low_complexity.regions.map((r) => ({ kind: "Bassa complessità", start: r.start, end: r.end, length: r.length, detail: `lettere più frequenti: ${r.composition}` })),
    ...domains.map((d) => ({ kind: "Dominio", start: d.start, end: d.end, length: d.end - d.start + 1, detail: `${d.name} (${d.accession}, ${d.type})` })),
  ].sort((a, b) => a.start - b.start);
  if (!rows.length) return <p>Nessuna regione disordinata lunga, a bassa complessità o dominio annotato.</p>;
  return (
    <div className="table-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
      <table className="table">
        <thead><tr><th>Tipo</th><th>Residui</th><th className="num">Lunghezza</th><th>Dettaglio</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="nowrap">{r.kind}</td>
              <td className="nowrap">{span(r.start, r.end)}</td>
              <td className="num">{r.length}</td>
              <td className="small">{r.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Proprietà fisico-chimiche (ProtParam di Biopython): quattro valori con spiegazione e soglie, il resto a scomparsa. */
function Properties({ p }: { p: NonNullable<SequenceAnalysis["properties"]> }) {
  const unstable = p.instability_index > 40;
  return (
    <div className="stack-sm">
      <div className="grid grid-4">
        <Stat label="Massa" value={<>{fmtNum(p.molecular_weight / 1000, 2)}<small> kDa</small></>} note={`${fmtInt(Math.round(p.molecular_weight))} Da`}
          detail="Massa media dalla composizione, senza modifiche post-traduzionali né ponti disolfuro." />
        <Stat label="Punto isoelettrico" value={fmtNum(p.isoelectric_point, 2)} note="pI teorico"
          detail="pH al quale la carica netta è zero, dalla composizione: orienta la scelta del tampone e della cromatografia a scambio ionico." />
        <Stat label="GRAVY" value={fmtNum(p.gravy, 3)} note={p.gravy > 0 ? "idrofobica" : "idrofilica"}
          detail="Media dell'idropatia di Kyte e Doolittle (1982) sui residui: positiva = complessivamente idrofobica (frequente nelle proteine di membrana), negativa = idrofilica." />
        <Stat label="Indice di instabilità" value={fmtNum(p.instability_index, 1)} note={unstable ? "instabile in vitro (> 40)" : "stabile (≤ 40)"} tone={unstable ? "warn" : "ok"}
          detail="Guruprasad et al. (1990), dalla frequenza di dipeptidi associati all'instabilità: sopra 40 la proteina è predetta instabile in vitro." />
      </div>
      <Disclosure title="Altre proprietà fisico-chimiche" extra="ProtParam (Biopython)">
        <DetailGrid items={[
          { label: "Aromaticità", value: `${fmtNum(p.aromaticity * 100, 1)}% (F, W, Y; Lobry 1994)` },
          { label: "Coefficiente di estinzione a 280 nm", value: `${fmtInt(p.extinction_coefficient)} M⁻¹ cm⁻¹ (cisteine ridotte)` },
          { label: "Assorbanza 0,1% (1 g/L)", value: p.molecular_weight ? fmtNum(p.extinction_coefficient / p.molecular_weight, 3) : "—" },
          { label: "Residui con propensione per l'elica", value: `${fmtNum(p.propensity.helix * 100, 1)}% (V, I, Y, F, W, L)` },
          { label: "… per le anse", value: `${fmtNum(p.propensity.turn * 100, 1)}% (N, P, G, S)` },
          { label: "… per il filamento β", value: `${fmtNum(p.propensity.sheet * 100, 1)}% (E, M, A, L)` },
        ]} />
      </Disclosure>
    </div>
  );
}

function SequenceDetail({ seq, onLabels }: { seq: Sequence; onLabels: (l: EntityLabels) => void }) {
  const navigate = useNavigate();
  const { data: analysis, error } = useAsync<SequenceAnalysis>(() => api.analyzeSequence(seq.sequence), [seq.id]);
  const accession = (seq.meta?.accession as string) || "";
  const { data: entry } = useAsync<UniProtEntry | null>(() => (accession ? api.uniprot(accession) : Promise.resolve(null)), [accession]);
  const { data: domains } = useAsync<InterProDomain[] | null>(() => (accession ? api.uniprotDomains(accession).catch(() => []) : Promise.resolve(null)), [accession]);
  // disordine, bassa complessità e domini riportati sulla sequenza (F5.5); domini solo con l'accessione UniProt
  const protein = analysis?.kind === "protein";
  const { data: features, error: featuresError } = useAsync<CoverageCheck | null>(
    () => (protein ? api.coverage({ sequence: seq.sequence, accession: sequenceAccession(seq) || null }) : Promise.resolve(null)), [seq.id, protein]);
  const p = analysis?.properties;
  const pred = analysis?.secondary_prediction;
  const dis = features?.disorder;
  const composition = useMemo(() => (p ? compositionRows(p.composition, seq.length) : []), [p, seq.length]);
  const meta = seq.meta ?? {};
  const reviewed = meta.reviewed === true ? "Swiss-Prot (revisionata)" : meta.reviewed === false ? "TrEMBL (non revisionata)" : null;
  return (
    <div className="stack">
      <Card
        title={seq.name}
        icon="dna"
        sub={`${fmtInt(seq.length)} residui · ${seq.source}${accession ? ` · ${accession}` : ""}`}
        right={
          <>
            <CopyButton text={`>${seq.name}\n${wrapSequence(seq.sequence)}\n`} label="Copia FASTA" />
            <button className="btn btn-sm btn-primary" onClick={() => navigate(`/search?seq=${seq.id}`)}>
              <Icon name="search" size={14} /> Cerca template
            </button>
            <button className="btn btn-sm" onClick={() => navigate(`/modeling?seq=${seq.id}`)}>
              <Icon name="cube" size={14} /> Modella
            </button>
          </>
        }
      >
        <div className="stack-sm">
          <LabelEditor key={seq.id} type="sequence" id={seq.id} initial={itemLabels(seq)} onChange={onLabels} />
          <InfoChips items={[
            { label: "Lunghezza", value: `${fmtInt(seq.length)} aa` },
            { label: "Tipo", value: analysis ? (protein ? "proteina" : analysis.kind) : null },
            { label: "Proteina", value: typeof meta.protein === "string" ? meta.protein : null },
            { label: "Organismo", value: typeof meta.organism === "string" ? meta.organism : null },
            { label: "UniProtKB", value: reviewed },
            { label: "Aggiunta", value: relTime(seq.created_at), title: seq.created_at },
          ]} />
          <pre className="seq-block wf-numbered" aria-label={`Sequenza di ${seq.name}`}>{numberedSequence(seq.sequence)}</pre>
        </div>
      </Card>
      {error && <Alert tone="err">{error.message}</Alert>}
      {!analysis && !error && <p className="small text-2 row" style={{ gap: 8 }}><Spinner /> Analisi della sequenza…</p>}
      {p && <Properties p={p} />}
      {featuresError && <Alert tone="warn">Disordine e domini non disponibili: {featuresError.message}</Alert>}
      {dis && features && (
        <Card title="Disordine, bassa complessità e domini" icon="activity"
          sub={`${dis.method} · ${dis.low_complexity.method} · ${features.domains_info?.available ? `domini ${features.domains_info.source}` : "domini non noti"}`}>
          <div className="stack">
            <FeatureMap
              length={dis.length}
              tracks={featureTracks({ disorder: dis, domains: features.domains_info?.available ? features.domains : null, gor: pred?.segments })}
              title="Disordine e domini"
              titleHidden
              subtitle={`${seq.name} · ${dis.length} residui`}
              filename={`${seq.name}_disordine_domini`}
            />
            <p className="small text-2">{disorderSummary(dis)} {lowComplexitySummary(dis)}</p>
            <p className="small text-2">
              Le regioni disordinate lunghe e i domini non coperti dal template saranno poco affidabili nel modello: Modellazione e Ricerca lo segnalano prima di lanciare.
              {features.domains_info?.note ? ` ${features.domains_info.note}` : ""}
            </p>
            <Disclosure title="Regioni predette" extra={`${dis.regions.length} disordinate · ${dis.low_complexity.regions.length} a bassa complessità`}>
              <RegionTable check={features} />
            </Disclosure>
            <Disclosure title="Metodi e limiti">
              <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
                <li>FoldIndex = {dis.scale.formula}, finestra di {dis.params.window} residui; sotto 0 probabilmente disordinato. Regioni di almeno {dis.params.min_region} residui, con al più {dis.params.max_gap} residui sopra 0 tollerati.</li>
                <li>SEG: finestra {dis.low_complexity.params.window}, K1 {fmtNum(dis.low_complexity.params.k1, 1)} e K2 {fmtNum(dis.low_complexity.params.k2, 1)} bit.</li>
                {dis.limits.map((l) => <li key={l}>{l}</li>)}
              </ul>
            </Disclosure>
          </div>
        </Card>
      )}
      {pred && (
        <Card title="Struttura secondaria predetta" icon="layers" sub={pred.method} collapsible storageKey="sequences.ss">
          <div className="stack">
            <div className="stack-sm">
              <span className="small text-2">Lungo la sequenza</span>
              <SSStrip ss3={pred.prediction} />
            </div>
            <StackBar parts={ssParts(pred.prediction)} title="Composizione della struttura secondaria predetta" titleHidden subtitle={`${seq.name} · % dei residui`}
              filename={`${seq.name}_struttura_secondaria_predetta`} />
            {pred.segments.length > 0 && (
              <Disclosure title="Segmenti predetti" extra={pred.segments.length}>
                <div className="table-wrap" style={{ maxHeight: 280, overflowY: "auto" }}>
                  <table className="table">
                    <thead><tr><th>Tipo</th><th>Residui</th><th className="num">Lunghezza</th></tr></thead>
                    <tbody>
                      {pred.segments.map((s) => (
                        <tr key={`${s.type}${s.start}`}>
                          <td>{ssLabel(s.type === "helix" ? "H" : "E")}</td>
                          <td>{span(s.start, s.end)}</td>
                          <td className="num">{s.end - s.start + 1}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Disclosure>
            )}
            <p className="small text-2">Predizione euristica dalla sola sequenza: utile come orientamento, non come dato. Per la struttura reale usa la DSSP sul template o sul modello.</p>
          </div>
        </Card>
      )}
      {entry && <UniProtCard entry={entry} domains={domains ?? null} />}
      {p && (
        <Card title="Composizione amminoacidica" icon="grid" collapsible storageKey="sequences.composition">
          <div className="stack">
            <StackBar parts={compositionParts(p.composition, seq.length)} title="Composizione in amminoacidi" titleHidden subtitle={`${seq.name} · classi di residui, % sulla lunghezza`}
              filename={`${seq.name}_composizione`} />
            <Disclosure title="Composizione per residuo" extra="20 amminoacidi standard">
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Residuo</th><th>Classe</th><th className="num">Conteggio</th><th>Frequenza</th></tr></thead>
                  <tbody>
                    {composition.map((r) => (
                      <tr key={r.letter}>
                        <td className="nowrap"><b className="mono">{r.letter}</b> {r.name}</td>
                        <td className="small">{r.group}</td>
                        <td className="num">{r.count}</td>
                        <td><BarCell value={r.pct} max={Math.max(1, composition[0]?.pct ?? 1)} tone="var(--accent)" label={`${fmtNum(r.pct, 1)}%`} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Disclosure>
          </div>
        </Card>
      )}
    </div>
  );
}

/** Voce UniProtKB: dati principali, mappa delle feature, funzione e, a scomparsa, commenti, strutture, parole chiave e GO. */
function UniProtCard({ entry, domains }: { entry: UniProtEntry; domains: InterProDomain[] | null }) {
  const comments = Object.entries(entry.comments).filter(([type]) => type !== "FUNCTION");
  return (
    <Card title="Annotazioni UniProt" icon="library" sub={`${entry.protein_name} · ${entry.organism}`} collapsible storageKey="sequences.uniprot"
      right={<a className="btn btn-sm btn-ghost" href={`https://www.uniprot.org/uniprotkb/${entry.accession}`} target="_blank" rel="noreferrer">UniProt <Icon name="external" size={13} /></a>}>
      <div className="stack">
        <DetailGrid items={[
          { label: "Accessione", value: <>{entry.accession} {entry.reviewed ? <Badge tone="ok" title="Swiss-Prot (revisionata)">SP</Badge> : <Badge title="TrEMBL (non revisionata)">TrEMBL</Badge>}</> },
          { label: "Nome della voce", value: entry.entry_name || "—" },
          { label: "Geni", value: entry.genes.join(", ") || "—" },
          { label: "Organismo", value: `${entry.organism}${entry.taxonomy_id ? ` (taxon ${entry.taxonomy_id})` : ""}` },
          { label: "Esistenza", value: entry.existence || "—" },
          { label: "Lunghezza", value: `${fmtInt(entry.length)} aa${entry.mass ? ` · ${fmtNum(entry.mass / 1000, 2)} kDa` : ""}` },
        ]} />
        <FeatureMap length={entry.length} tracks={uniprotTracks(entry, domains)} title="Feature UniProt" subtitle={`${entry.accession} · ${fmtInt(entry.length)} residui`}
          filename={`${entry.accession}_feature_uniprot`} />
        {entry.comments.FUNCTION && <p className="small text-2"><b>Funzione.</b> {entry.comments.FUNCTION.join(" ")}</p>}
        {comments.length > 0 && (
          <Disclosure title="Altre annotazioni" extra={comments.map(([type]) => uniprotCommentLabel(type)).join(" · ")}>
            <div className="stack-sm">
              {comments.map(([type, texts]) => (
                <div key={type}>
                  <h4>{uniprotCommentLabel(type)}</h4>
                  {texts.map((t, i) => <p key={i} style={{ marginTop: 3 }}>{t}</p>)}
                </div>
              ))}
            </div>
          </Disclosure>
        )}
        {entry.pdb.length > 0 && (
          <Disclosure title="Strutture sperimentali" extra={`${entry.pdb.length} nel PDB · per risoluzione`} defaultOpen={entry.pdb.length <= 10}>
            <div className="table-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
              <table className="table">
                <thead><tr><th>PDB</th><th>Metodo</th><th className="num">Risoluzione</th><th>Catene</th></tr></thead>
                <tbody>
                  {entry.pdb.map((r) => (
                    <tr key={r.id}>
                      <td className="nowrap">
                        <a href={`https://www.rcsb.org/structure/${encodeURIComponent(r.id)}`} target="_blank" rel="noreferrer"><b>{r.id}</b> <Icon name="external" size={11} /></a>
                      </td>
                      <td className="small">{r.method || "—"}</td>
                      <td className="num">{r.resolution ? `${fmtNum(r.resolution, 2)} Å` : "—"}</td>
                      <td className="small mono">{r.chains || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Disclosure>
        )}
        {entry.keywords.length > 0 && (
          <Disclosure title="Parole chiave" extra={entry.keywords.length}>
            <div className="row" style={{ gap: 6 }}>{entry.keywords.map((k) => <span key={k} className="chip">{k}</span>)}</div>
          </Disclosure>
        )}
        {entry.go.length > 0 && (
          <Disclosure title="Gene Ontology" extra={entry.go.length}>
            <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 2 }}>
              {entry.go.map((g) => <li key={g.id}><span className="mono">{g.id}</span> {g.term}</li>)}
            </ul>
          </Disclosure>
        )}
      </div>
    </Card>
  );
}

function UniProtImport({ onAdded }: { onAdded: () => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<UniProtHit[] | null>(null);
  const [entry, setEntry] = useState<UniProtEntry | null>(null);
  const [busy, setBusy] = useState(false);
  const search = async () => {
    setBusy(true);
    setEntry(null);
    try {
      const looksLikeAcc = /^[A-Z0-9]{6,10}$/i.test(q.trim());
      if (looksLikeAcc) {
        setEntry(await api.uniprot(q.trim().toUpperCase()));
        setHits(null);
      } else setHits(await api.uniprotSearch(q));
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const add = async () => {
    if (!entry || !projectId) return;
    try {
      await api.addSequence(projectId, entry.entry_name || entry.accession, entry.sequence, "uniprot", { accession: entry.accession, reviewed: entry.reviewed, protein: entry.protein_name, organism: entry.organism });
      toast.push(`${entry.accession} aggiunta al progetto`);
      onAdded();
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <div className="stack">
      <div className="row">
        <input className="input" style={{ flex: 1 }} placeholder="Accession (P02144) oppure nome/gene (es. myoglobin human)" aria-label="Cerca in UniProtKB" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && q.trim() && search()} />
        <button className="btn btn-primary" disabled={!q.trim() || busy} onClick={search}>{busy ? <Spinner /> : <Icon name="search" />} Cerca</button>
      </div>
      {hits && (
        <div className="table-wrap" style={{ maxHeight: 280, overflowY: "auto" }}>
          <table className="table">
            <thead><tr><th>Accession</th><th>Proteina</th><th>Organismo</th><th className="num">aa</th></tr></thead>
            <tbody>
              {hits.map((h) => (
                <tr key={h.accession} className={`clickable${entry?.accession === h.accession ? " selected" : ""}`} onClick={() => api.uniprot(h.accession).then(setEntry).catch(toast.error)}>
                  <td className="nowrap">{h.accession} {h.reviewed && <Badge tone="ok" title="Swiss-Prot (revisionato)">SP</Badge>}</td>
                  <td>{h.protein_name}<div className="tiny text-2">{h.genes.join(", ")}</div></td>
                  <td className="small">{h.organism}</td>
                  <td className="num">{h.length}</td>
                </tr>
              ))}
              {!hits.length && <tr><td colSpan={4} className="text-2">Nessun risultato.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {entry && (
        <div className="card flat stack-sm">
          <div className="row">
            <b>{entry.protein_name}</b>
            <Badge tone={entry.reviewed ? "ok" : undefined}>{entry.accession}</Badge>
            <button className="btn btn-primary btn-sm right" disabled={!projectId} onClick={add}><Icon name="plus" size={14} /> Aggiungi al progetto</button>
          </div>
          <InfoChips items={[
            { label: "Organismo", value: entry.organism },
            { label: "Lunghezza", value: `${fmtInt(entry.length)} aa` },
            { label: "Strutture PDB", value: entry.pdb.length },
            { label: "Geni", value: entry.genes.join(", ") },
          ]} />
          {entry.comments.FUNCTION && <p className="small text-2">{entry.comments.FUNCTION[0].slice(0, 400)}{entry.comments.FUNCTION[0].length > 400 ? "…" : ""}</p>}
        </div>
      )}
    </div>
  );
}

export default function Sequences() {
  const { projectId, reload: reloadProjects } = useProject();
  const toast = useToast();
  const { data: seqs, reload, loading, setData } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  // `?id=` (ricerca globale, link) sceglie la sequenza appena l'elenco del suo progetto è caricato
  const [params, setParams] = useSearchParams();
  const wanted = params.get("id");
  const [selected, setSelected] = useState<string | null>(wanted);
  const [tab, setTab] = useState<"fasta" | "uniprot">("fasta");
  const [fasta, setFasta] = useState("");
  const [filter, setFilter] = useState("");
  const [toDelete, setToDelete] = useState<Sequence | null>(null);
  useEffect(() => {
    if (!seqs) return;
    if (wanted) {
      if (seqs.some((s) => s.id === wanted)) {
        setSelected(wanted);
        setParams({}, { replace: true });
        return;
      }
      if (loading) return; // l'elenco del progetto giusto sta ancora arrivando
      setParams({}, { replace: true }); // sequenza non trovata (eliminata, link vecchio)
    }
    if (seqs.length && !seqs.some((s) => s.id === selected)) setSelected(seqs[seqs.length - 1].id);
  }, [seqs, selected, wanted, loading, setParams]);
  const patchLabels = (l: EntityLabels) => setData((seqs ?? []).map((s) => (s.id === l.entity_id ? { ...s, tags: l.tags, favorite: l.favorite } : s)));
  const current = seqs?.find((s) => s.id === selected) ?? null;
  // il filtro vale solo finché il suo campo è visibile (sotto la soglia non si potrebbe più cancellare)
  const filterable = !!seqs && seqs.length >= FILTER_FROM;
  const q = filterable ? filter.trim().toLowerCase() : "";
  const shown = seqs?.filter((s) => !q || `${s.name} ${s.source} ${(s.tags ?? []).join(" ")} ${String(s.meta?.accession ?? "")}`.toLowerCase().includes(q)) ?? [];
  const afterChange = () => {
    reload();
    reloadProjects();
  };
  const importFasta = async () => {
    if (!projectId) return;
    try {
      const text = fasta.trim().startsWith(">") ? fasta : `>seq${(seqs?.length ?? 0) + 1}\n${fasta}`;
      const added = await api.importFasta(projectId, text);
      toast.push(`${added.length} sequenz${added.length === 1 ? "a aggiunta" : "e aggiunte"}`);
      setFasta("");
      afterChange();
      setSelected(added[added.length - 1].id);
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <div className="page">
      <PageHead title="Sequenze" lead="Le sequenze bersaglio del progetto. Importale da FASTA o da UniProtKB: le annotazioni (domini, siti, strutture note) aiutano a scegliere i template." />
      <div className="split">
        <div className="stack">
          <Card title="Aggiungi" icon="plus">
            <Tabs value={tab} onChange={setTab} tabs={[{ value: "fasta", label: "FASTA / testo" }, { value: "uniprot", label: "UniProt" }]} />
            {tab === "fasta" ? (
              <div className="stack-sm">
                <textarea className="input" rows={7} value={fasta} onChange={(e) => setFasta(e.target.value)} aria-label="Sequenze in FASTA o testo"
                  placeholder={">nome descrizione\nMKTAYIAKQRQISFVKSHFSRQ...\n\n(più record per importare un multi-FASTA)"} />
                <div className="row">
                  <button className="btn btn-primary" disabled={!fasta.trim()} onClick={importFasta}><Icon name="plus" /> Importa</button>
                  <SequenceFileButton onLoaded={(parsed) => setFasta(recordsToFasta(parsed.records))}>Carica da file</SequenceFileButton>
                </div>
                <span className="small text-2">FASTA, GenBank, UniProt, PIR, Clustal, PDB/mmCIF, testo…</span>
              </div>
            ) : (
              <UniProtImport onAdded={afterChange} />
            )}
          </Card>
          <Card title="Sequenze del progetto" icon="dna" sub={seqs ? `${seqs.length}` : ""}
            right={filterable ? <input className="input input-sm wf-filter" placeholder="Filtra (nome, etichetta…)" aria-label="Filtra le sequenze" value={filter} onChange={(e) => setFilter(e.target.value)} /> : undefined}>
            {loading && !seqs ? <div className="empty"><Spinner label="Caricamento delle sequenze" /></div> : !seqs?.length ? (
              <Empty icon="dna" title="Nessuna sequenza">Importa una sequenza o usa un esempio dalla Panoramica.</Empty>
            ) : !shown.length ? (
              <Empty icon="search" title="Nessuna sequenza corrisponde al filtro" />
            ) : (
              <div className="table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
                <table className="table">
                  <tbody>
                    {shown.map((s) => (
                      <tr key={s.id} className={`clickable ${s.id === selected ? "selected" : ""}`} onClick={() => setSelected(s.id)}>
                        <td style={{ width: 30 }}><FavoriteStar type="sequence" id={s.id} value={!!s.favorite} onChange={patchLabels} size={13} /></td>
                        <td><b>{s.name}</b> <TagChips tags={s.tags} max={3} /><div className="tiny text-2">{s.source} · {relTime(s.created_at)}</div></td>
                        <td className="num nowrap">{fmtInt(s.length)} aa</td>
                        <td style={{ width: 36 }}>
                          <button className="btn btn-ghost btn-icon" onClick={(e) => { e.stopPropagation(); setToDelete(s); }} aria-label={`Elimina ${s.name}`} title="Elimina">
                            <Icon name="trash" size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
        <div>
          {current ? <SequenceDetail seq={current} onLabels={patchLabels} /> : (
            <Card>
              <Empty icon="dna" title="Seleziona o importa una sequenza">Proprietà, disordine, struttura secondaria predetta e annotazioni UniProt compaiono qui.</Empty>
            </Card>
          )}
        </div>
      </div>
      {toDelete && projectId && (
        <Confirm
          text={`Eliminare la sequenza ${toDelete.name}? I job già eseguiti non vengono toccati.`}
          confirmLabel="Elimina"
          onCancel={() => setToDelete(null)}
          onConfirm={() => api.deleteSequence(projectId, toDelete.id).then(() => { setToDelete(null); afterChange(); }).catch(toast.error)}
        />
      )}
    </div>
  );
}
