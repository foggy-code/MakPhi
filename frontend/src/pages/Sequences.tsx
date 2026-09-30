import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { InterProDomain, Sequence, SequenceAnalysis, UniProtEntry, UniProtHit } from "../api/types";
import { FeatureMap, SSStrip, StackBar, type Track } from "../components/charts";
import { SequenceFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { Alert, Badge, Card, Confirm, CopyButton, Empty, PageHead, Spinner, Stat, Tabs } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtInt, fmtNum, relTime, wrapSequence } from "../lib/format";
import { recordsToFasta } from "../lib/files";
import { useAsync } from "../lib/hooks";

const FEATURE_GROUPS: { label: string; types: string[]; color: string }[] = [
  { label: "Catena / peptide", types: ["Chain", "Peptide", "Propeptide", "Signal", "Transit peptide"], color: "#60a5fa" },
  { label: "Domini e regioni", types: ["Domain", "Region", "Repeat", "Zinc finger", "Motif", "Coiled coil"], color: "#a78bfa" },
  { label: "Siti", types: ["Active site", "Binding site", "Site", "Metal binding"], color: "#f87171" },
  { label: "Eliche (sperimentali)", types: ["Helix"], color: "var(--helix)" },
  { label: "Filamenti β", types: ["Beta strand"], color: "var(--strand)" },
  { label: "Ponti disolfuro", types: ["Disulfide bond"], color: "#facc15" },
  { label: "Modifiche", types: ["Modified residue", "Glycosylation", "Lipidation", "Cross-link"], color: "#34d399" },
];

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
    tracks.push({ label: "InterPro", color: "#22d3ee", items: domains.flatMap((d) => d.locations.map((l) => ({ start: l.start, end: l.end, title: `${d.name} (${d.accession}, ${d.type})` }))) });
  }
  const pdb = entry.pdb.map((p) => ({ ...p, range: parseChains(p.chains) })).filter((p) => p.range).slice(0, 12);
  pdb.forEach((p) => tracks.push({ label: `PDB ${p.id}${p.resolution ? ` ${p.resolution} Å` : ""}`, color: "#94a3b8", items: [{ start: p.range!.start, end: p.range!.end, title: `${p.id} ${p.method} ${p.chains}` }] }));
  return tracks;
}

function SequenceDetail({ seq }: { seq: Sequence }) {
  const navigate = useNavigate();
  const { data: analysis, error } = useAsync<SequenceAnalysis>(() => api.analyzeSequence(seq.sequence), [seq.id]);
  const accession = (seq.meta?.accession as string) || "";
  const { data: entry } = useAsync<UniProtEntry | null>(() => (accession ? api.uniprot(accession) : Promise.resolve(null)), [accession]);
  const { data: domains } = useAsync<InterProDomain[] | null>(() => (accession ? api.uniprotDomains(accession).catch(() => []) : Promise.resolve(null)), [accession]);
  const p = analysis?.properties;
  const pred = analysis?.secondary_prediction;
  return (
    <div className="stack">
      <Card
        title={seq.name}
        sub={`${seq.length} residui · ${seq.source}${accession ? ` · ${accession}` : ""}`}
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
        <pre className="seq-block">{wrapSequence(seq.sequence, 60)}</pre>
      </Card>
      {error && <Alert tone="err">{error.message}</Alert>}
      {p && (
        <div className="grid grid-4">
          <Stat label="Massa" value={<>{fmtNum(p.molecular_weight / 1000, 2)}<small> kDa</small></>} />
          <Stat label="Punto isoelettrico" value={fmtNum(p.isoelectric_point, 2)} />
          <Stat label="GRAVY" value={fmtNum(p.gravy, 3)} note={p.gravy > 0 ? "idrofobica" : "idrofilica"} />
          <Stat label="Indice di instabilità" value={fmtNum(p.instability_index, 1)} note={p.instability_index > 40 ? "instabile in vitro (> 40)" : "stabile (≤ 40)"} />
        </div>
      )}
      {pred && (
        <Card title="Struttura secondaria predetta" sub={pred.method}>
          <div className="stack-sm">
            <SSStrip ss3={pred.prediction} />
            <p className="tiny muted">Predizione euristica dalla sola sequenza: utile come orientamento, non come dato. Per la struttura reale usa la DSSP sul template o sul modello.</p>
          </div>
        </Card>
      )}
      {entry && (
        <Card title="Annotazioni UniProt" sub={`${entry.protein_name} · ${entry.organism}`} right={<a className="btn btn-sm btn-ghost" href={`https://www.uniprot.org/uniprotkb/${entry.accession}`} target="_blank" rel="noreferrer">UniProt <Icon name="external" size={13} /></a>}>
          <div className="stack">
            <FeatureMap length={entry.length} tracks={uniprotTracks(entry, domains ?? null)} />
            {entry.comments.FUNCTION && <p className="small text-2"><b>Funzione.</b> {entry.comments.FUNCTION.join(" ")}</p>}
            {entry.pdb.length > 0 && (
              <div className="small">
                <b>Strutture sperimentali ({entry.pdb.length}):</b>{" "}
                {entry.pdb.slice(0, 20).map((r) => (
                  <Badge key={r.id} tone="accent" title={`${r.method} ${r.chains}`}>{r.id}{r.resolution ? ` · ${r.resolution} Å` : ""}</Badge>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}
      {p && (
        <Card title="Composizione amminoacidica">
          <StackBar
            parts={[
              { label: "Idrofobici", value: pct(p.composition, "AILMFWV", seq.length), color: "#5b8def" },
              { label: "Carichi +", value: pct(p.composition, "KRH", seq.length), color: "#ef5350" },
              { label: "Carichi −", value: pct(p.composition, "DE", seq.length), color: "#c35bd6" },
              { label: "Polari", value: pct(p.composition, "NQSTY", seq.length), color: "#34c759" },
              { label: "G/P/C", value: pct(p.composition, "GPC", seq.length), color: "#f5a05a" },
            ]}
          />
        </Card>
      )}
    </div>
  );
}

function pct(comp: Record<string, number>, letters: string, len: number) {
  return (letters.split("").reduce((s, l) => s + (comp[l] ?? 0), 0) / Math.max(1, len)) * 100;
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
        <input className="input" style={{ flex: 1 }} placeholder="Accession (P02144) oppure nome/gene (es. myoglobin human)" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && q.trim() && search()} />
        <button className="btn btn-primary" disabled={!q.trim() || busy} onClick={search}>{busy ? <Spinner /> : <Icon name="search" />} Cerca</button>
      </div>
      {hits && (
        <div className="table-wrap" style={{ maxHeight: 280, overflowY: "auto" }}>
          <table className="table">
            <thead><tr><th>Accession</th><th>Proteina</th><th>Organismo</th><th className="num">aa</th></tr></thead>
            <tbody>
              {hits.map((h) => (
                <tr key={h.accession} className="clickable" onClick={() => api.uniprot(h.accession).then(setEntry).catch(toast.error)}>
                  <td className="nowrap">{h.accession} {h.reviewed && <Badge tone="ok" title="Swiss-Prot (revisionato)">SP</Badge>}</td>
                  <td>{h.protein_name}<div className="tiny muted">{h.genes.join(", ")}</div></td>
                  <td className="small">{h.organism}</td>
                  <td className="num">{h.length}</td>
                </tr>
              ))}
              {!hits.length && <tr><td colSpan={4} className="muted">Nessun risultato.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {entry && (
        <div className="card flat stack-sm">
          <div className="row">
            <b>{entry.protein_name}</b>
            <Badge>{entry.accession}</Badge>
            <span className="small muted">{entry.organism} · {entry.length} aa · {entry.pdb.length} strutture PDB</span>
            <button className="btn btn-primary btn-sm right" onClick={add}><Icon name="plus" size={14} /> Aggiungi al progetto</button>
          </div>
          {entry.comments.FUNCTION && <p className="small text-2">{entry.comments.FUNCTION[0].slice(0, 400)}</p>}
        </div>
      )}
    </div>
  );
}

export default function Sequences() {
  const { projectId, reload: reloadProjects } = useProject();
  const toast = useToast();
  const { data: seqs, reload, loading } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"fasta" | "uniprot">("fasta");
  const [fasta, setFasta] = useState("");
  const [toDelete, setToDelete] = useState<Sequence | null>(null);
  useEffect(() => {
    if (seqs?.length && !seqs.some((s) => s.id === selected)) setSelected(seqs[seqs.length - 1].id);
  }, [seqs, selected]);
  const current = seqs?.find((s) => s.id === selected) ?? null;
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
          <Card title="Aggiungi">
            <Tabs value={tab} onChange={setTab} tabs={[{ value: "fasta", label: "FASTA / testo" }, { value: "uniprot", label: "UniProt" }]} />
            {tab === "fasta" ? (
              <div className="stack-sm">
                <textarea className="input" rows={7} value={fasta} onChange={(e) => setFasta(e.target.value)} placeholder={">nome descrizione\nMKTAYIAKQRQISFVKSHFSRQ...\n\n(più record per importare un multi-FASTA)"} />
                <div className="row">
                  <button className="btn btn-primary" disabled={!fasta.trim()} onClick={importFasta}><Icon name="plus" /> Importa</button>
                  <SequenceFileButton onLoaded={(parsed) => setFasta(recordsToFasta(parsed.records))}>Carica da file</SequenceFileButton>
                  <span className="tiny muted">FASTA, GenBank, UniProt, PIR, Clustal, PDB/mmCIF, testo…</span>
                </div>
              </div>
            ) : (
              <UniProtImport onAdded={afterChange} />
            )}
          </Card>
          <Card title="Sequenze del progetto" sub={seqs ? `${seqs.length}` : ""}>
            {loading && !seqs ? <Spinner /> : !seqs?.length ? (
              <Empty icon="dna" title="Nessuna sequenza">Importa una sequenza o usa un esempio dalla Panoramica.</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <tbody>
                    {seqs.map((s) => (
                      <tr key={s.id} className={`clickable ${s.id === selected ? "selected" : ""}`} onClick={() => setSelected(s.id)}>
                        <td><b>{s.name}</b><div className="tiny muted">{s.source} · {relTime(s.created_at)}</div></td>
                        <td className="num nowrap">{fmtInt(s.length)} aa</td>
                        <td style={{ width: 36 }}>
                          <button className="btn btn-ghost btn-icon" onClick={(e) => { e.stopPropagation(); setToDelete(s); }} aria-label="Elimina">
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
        <div>{current ? <SequenceDetail seq={current} /> : <Card><Empty icon="dna" title="Seleziona o importa una sequenza" /></Card>}</div>
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
