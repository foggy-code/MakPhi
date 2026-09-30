// Analisi strutturale (F7): scheda "Interazioni" della pagina Qualità e vista di un job `analysis` (/quality?ajob=).
// Tabelle filtrabili di legami idrogeno, ponti salini, disolfuri e interazioni aromatiche, interfacce fra catene,
// conservazione da un allineamento multiplo; il 3D si colora per interfaccia o conservazione (API di colorazione di
// Viewer3D) e un clic su un'interazione evidenzia i due residui. La logica sta in lib/interactions.ts.
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AnalysisResult, InterfacePair, JobSummary, StructureSource } from "../api/types";
import { encodeSource } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import {
  analysisColoring, analysisColorOptions, chainFilterOptions, conservationStripRows, endLabel, fastaFromParsed, filterRows, highlightFor, INTERACTION_LABEL,
  interactionCounts, interactionRows, interfaceHighlight, msaRequest, PLAUSIBILITY_TONE, residueFilterText, type AnalysisColorBy, type InteractionKind,
  type InteractionRow, type MsaMode,
} from "../lib/interactions";
import { resiNumber, type ResidueRef } from "../lib/residueColoring";
import { ResidueStrip } from "./charts";
import { SequenceFileButton } from "./FileLoad";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, Card, Empty, Segmented, Stat } from "./ui";

type Focus = { chain: string; resi: number }[];

/** File della struttura per il viewer (come nella pagina Qualità). */
export function sourceFileUrl(source: StructureSource | null | undefined): string | null {
  if (!source) return null;
  if (source.type === "template" && source.id) return api.templateFileUrl(source.id);
  if (source.type === "job" && source.job_id && source.file) return api.jobFileUrl(source.job_id, source.file);
  if (source.type === "upload" && source.file) return api.uploadFileUrl(source.file);
  return null;
}

const KINDS: InteractionKind[] = ["hbonds", "salt_bridges", "disulfides", "aromatic"];

function InteractionTable({ rows, selected, onPick, kind }: { rows: InteractionRow[]; selected: string | null; onPick: (r: InteractionRow) => void; kind: InteractionKind }) {
  if (!rows.length) return <p className="small muted">Nessuna interazione con questi filtri.</p>;
  const [first, second] = kind === "hbonds" ? ["Donatore", "Accettore"] : kind === "salt_bridges" ? ["Catione", "Anione"] : kind === "aromatic" ? ["Residuo 1 (catione o anello)", "Anello"] : ["Cisteina", "Cisteina"];
  return (
    <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
      <table className="table">
        <thead><tr><th>{first}</th><th>{second}</th><th className="num">Distanza (Å)</th><th>Tipo</th><th>Geometria</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className={`clickable${r.key === selected ? " selected" : ""}`} onClick={() => onPick(r)} title="Evidenzia i due residui nel 3D">
              <td className="nowrap mono">{endLabel(r.a)}</td>
              <td className="nowrap mono">{endLabel(r.b)}</td>
              <td className="num">{fmtNum(r.distance, 2)}</td>
              <td className="nowrap">{r.type}{r.interChain && <> <Badge tone="info">fra catene</Badge></>}</td>
              <td className="small">{r.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InterfaceCard({ pair, onHighlight, onResidue }: { pair: InterfacePair; onHighlight: () => void; onResidue: (chain: string, resnum: string) => void }) {
  const p = pair.plausibility;
  const i = pair.interactions;
  return (
    <div className="stack-sm" style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      <div className="row" style={{ gap: 14, alignItems: "center" }}>
        <b>Catene {pair.chains[0]} – {pair.chains[1]}</b>
        <Badge tone={PLAUSIBILITY_TONE[p.level]} title={p.limits}>plausibilità {p.level}</Badge>
        <button className="btn btn-sm" onClick={onHighlight}>Evidenzia l'interfaccia</button>
      </div>
      <div className="grid grid-4">
        <Stat label="Area d'interfaccia" value={<>{fmtNum(pair.area, 0)}<small> Å²</small></>} note={`area sepolta totale ${fmtNum(pair.buried, 0)} Å² (÷ 2)`} />
        <Stat label="Residui" value={pair.chains.map((c) => pair.counts[c] ?? 0).join(" + ")} note={`ΔSASA > 1 Å² · ${pair.chains.join(" / ")}`} />
        <Stat label="Interazioni fra le catene" value={i.hbonds + i.salt_bridges + i.disulfides + i.pi_stacking + i.cation_pi}
          note={`${i.hbonds} legami H, ${i.salt_bridges} ponti salini, ${i.disulfides} disolfuri, ${i.pi_stacking + i.cation_pi} aromatiche`} />
        <Stat label="Area apolare" value={pair.apolar_fraction != null ? <>{fmtNum(pair.apolar_fraction * 100, 0)}<small>%</small></> : "—"} note={`residui: ${pair.composition.counts.apolari} apolari, ${pair.composition.counts.polari} polari, ${pair.composition.counts.carichi} carichi`} />
      </div>
      <p className="small text-2">{p.text}. <span className="muted">{p.limits}</span></p>
      {pair.chains.map((c) => (
        <div key={c} className="row small" style={{ gap: 4 }}>
          <span className="muted" style={{ minWidth: 70 }}>Catena {c}:</span>
          {(pair.residues[c] ?? []).map((r) => (
            <button key={r.resnum} className="chip" title={`ΔSASA ${fmtNum(r.delta, 1)} Å²`} onClick={() => onResidue(c, r.resnum)}>{r.resname} {r.resnum}</button>
          ))}
        </div>
      ))}
    </div>
  );
}

/** Risultato dell'analisi: 3D colorato, tabelle, interfacce, conservazione e criteri. */
export function AnalysisView({ result, url }: { result: AnalysisResult; url: string | null }) {
  const [kind, setKind] = useState<InteractionKind>("hbonds");
  const [chain, setChain] = useState("all");
  const [residue, setResidue] = useState("");
  const [colorBy, setColorBy] = useState<AnalysisColorBy>("none");
  const [focus, setFocus] = useState<Focus>([]);
  const [centerKey, setCenterKey] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const counts = interactionCounts(result);
  const rows = useMemo(() => filterRows(interactionRows(result, kind), chain, residue), [result, kind, chain, residue]);
  const options = analysisColorOptions(result);
  const shownBy = options.find((o) => o.value === colorBy)?.available ? colorBy : "none";
  const coloring = useMemo(() => analysisColoring(result, shownBy), [result, shownBy]);
  const chains = result.structure.chains_detail.map((c) => c.chain);
  const s = result.interactions.summary;
  const iface = result.interfaces;
  const cons = result.conservation;

  const show = (target: Focus) => {
    setFocus(target);
    setCenterKey((k) => k + 1);
  };
  const pickRow = (r: InteractionRow) => {
    setSelected(r.key);
    show(highlightFor(r));
  };
  /** Dal 3D: il residuo diventa il filtro delle tabelle, senza spostare la vista. */
  const pick3d = (ref: ResidueRef) => {
    setResidue(residueFilterText(ref));
    setFocus([{ chain: ref.chain, resi: resiNumber(ref.resnum) }]);
  };

  return (
    <div className="stack">
      {result.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Legami idrogeno" value={s.hbonds} note={`${s.hbonds_backbone} scheletro, ${s.hbonds_mixed} misti, ${s.hbonds_sidechain} catene laterali · idrogeni ${result.interactions.hydrogens === "explicit" ? "del file" : "posizionati"}`} />
        <Stat label="Ponti salini" value={s.salt_bridges} note="N–O ≤ 4,0 Å (Barlow & Thornton 1983)" />
        <Stat label="Ponti disolfuro" value={s.disulfides} note="SG–SG 2,0 ± 0,2 Å" />
        <Stat label="Aromatiche" value={s.pi_stacking + s.cation_pi} note={`${s.pi_stacking} π-stacking, ${s.cation_pi} catione-π`} />
      </div>
      {url ? (
        <div className="stack-sm">
          <div className="row small">
            <label className="check">
              Colora per
              <select className="input" style={{ width: 240, minHeight: 28, padding: "2px 6px" }} value={shownBy} onChange={(e) => setColorBy(e.target.value as AnalysisColorBy)}>
                {options.map((o) => <option key={o.value} value={o.value} disabled={!o.available}>{o.label}{!o.available && o.note ? ` (${o.note})` : ""}</option>)}
              </select>
            </label>
            <button className="btn btn-sm btn-ghost" onClick={() => { setFocus([]); setSelected(null); }}>Nessuna evidenza</button>
            <span className="muted">Clic su un'interazione: i due residui a bastoncini arancioni · clic nel 3D: filtra le tabelle per quel residuo.</span>
          </div>
          <Viewer3D url={url} format={url.endsWith(".cif") ? "cif" : "pdb"} highlight={focus} centerKey={centerKey} coloring={coloring} onPickResidue={pick3d} height={380} />
          {coloring?.key === "conservation" && (
            <ResidueStrip residues={conservationStripRows(cons)} coloring={coloring} onSelect={(r) => { setResidue(residueFilterText(r)); show([{ chain: r.chain, resi: resiNumber(r.resnum) }]); }} />
          )}
        </div>
      ) : <p className="small muted">Struttura non disponibile per la visualizzazione 3D.</p>}

      <div className="stack-sm">
        <div className="row small" style={{ gap: 10 }}>
          <Segmented value={kind} onChange={(k) => { setKind(k); setSelected(null); }} options={KINDS.map((k) => ({ value: k, label: `${INTERACTION_LABEL[k]} (${counts[k]})` }))} />
          <select className="input" style={{ width: 200, minHeight: 28, padding: "2px 6px" }} value={chain} onChange={(e) => setChain(e.target.value)} aria-label="Filtro per catena">
            {chainFilterOptions(chains).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <input className="input" style={{ width: 200, minHeight: 28, padding: "2px 8px" }} value={residue} onChange={(e) => setResidue(e.target.value)} placeholder="Residuo: 27, K27, LYS27, A:27" aria-label="Filtro per residuo" />
          {residue && <button className="btn btn-sm btn-ghost" onClick={() => setResidue("")}>Tutti i residui</button>}
        </div>
        <InteractionTable rows={rows} selected={selected} onPick={pickRow} kind={kind} />
      </div>

      {iface.chains.length > 1 && (
        <Card title="Interfacce fra catene" sub={`${iface.method} · residui con ΔSASA > ${fmtNum(iface.min_delta, 0)} Å²`}>
          {iface.pairs.length ? (
            <div className="stack">
              {iface.pairs.map((p) => (
                <InterfaceCard key={p.chains.join("-")} pair={p} onHighlight={() => { setColorBy("interface"); show(interfaceHighlight(p)); }}
                  onResidue={(c, n) => { setResidue(`${c}:${n}`); show([{ chain: c, resi: resiNumber(n) }]); }} />
              ))}
            </div>
          ) : <p className="small muted">Nessuna coppia di catene si tocca: nessuna interfaccia.</p>}
        </Card>
      )}

      {cons && (
        <Card title="Conservazione" sub={cons.method}>
          <div className="stack-sm small">
            <div>{cons.sequences} sequenze, {cons.columns} colonne · {cons.source}.</div>
            {cons.chains.map((c) => (
              <div key={c.chain}>Catena <b>{c.chain}</b>: riga <span className="mono">{c.row}</span> (identità {fmtNum(c.identity, 1)}%, copertura {fmtNum(c.coverage, 1)}%) · {c.scored} di {c.residues} residui con punteggio, media {fmtNum(c.mean, 2)}</div>
            ))}
            {cons.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
            <p className="tiny muted">Punteggio 0–1 = (1 − entropia di Shannon normalizzata) × (1 − frazione di gap), con i pesi delle sequenze di Henikoff; colonne con più del {Math.round(cons.gap_cutoff * 100)}% di gap senza punteggio (Capra & Singh 2007). Colori della tavolozza di ConSurf su scala assoluta.</p>
          </div>
        </Card>
      )}

      <details className="small text-2">
        <summary>Criteri e fonti</summary>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          <li><b>Legami idrogeno</b>: D…A ≤ 3,5 Å e D…A–AA ≥ 90°; con l'idrogeno H…A ≤ 2,5 Å e D–H…A ≥ 120° (Baker & Hubbard 1984; angoli come HBPLUS, McDonald & Thornton 1994). Idrogeni del file se presenti, altrimenti posizionati dove la geometria è fissa (N–H dello scheletro come DSSP, NH degli anelli, NH₂ delle ammidi e di Arg); per OH e NH₃⁺, che ruotano, AA–D…A ≥ 90°.</li>
          <li><b>Ponti salini</b>: un O carico di Asp/Glu entro 4,0 Å da un N di Lys/Arg/His (Barlow & Thornton 1983); l'istidina è carica solo se protonata.</li>
          <li><b>Aromatiche</b>: π-stacking con centroidi ≤ 5,5 Å, spostamento ≤ 2,0 Å, parallelo (≤ 30°) o a T (≥ 60°); catione-π con NZ di Lys o CZ di Arg ≤ 6,0 Å dal centroide (McGaughey et al. 1998; Gallivan & Dougherty 1999; parametri di PLIP, Salentin et al. 2015).</li>
          <li><b>Ponti disolfuro</b>: SG–SG 2,0 ± 0,2 Å; χ3 di solito vicino a ±90° (Richardson 1981).</li>
          <li><b>Interfacce</b>: ΔSASA &gt; 1 Å² fra catena isolata e coppia di catene (Shrake & Rupley, sonda 1,4 Å); area = area sepolta / 2 come PISA. Plausibilità dalla sola area: &lt; 400 Å² bassa (contatti cristallini, Janin et al. 2008), 400–600 incerta, ≥ 600 compatibile (Lo Conte et al. 1999).</li>
          <li>Si considerano i soli amminoacidi: acque, ligandi e ioni sono esclusi.</li>
        </ul>
      </details>
    </div>
  );
}

/** Scheda "Interazioni" per una struttura: mostra l'analisi più recente (o quella indicata) e la rilancia. */
export function StructuralAnalysisPanel({ source, url, initialJob }: { source: StructureSource; url: string | null; initialJob?: string | null }) {
  const { projectId } = useProject();
  const toast = useToast();
  const sourceKey = encodeSource(source) ?? "";
  const [jobId, setJobId] = useState<string | null>(initialJob ?? null);
  const [msaMode, setMsaMode] = useState<MsaMode>("none");
  const [msaJob, setMsaJob] = useState("");
  const [msaText, setMsaText] = useState("");
  const [row, setRow] = useState("");
  const { data: alignJobs } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "align", status: "completed", limit: 30 }), [projectId]);
  const { job, summary, cancel } = useJob<AnalysisResult>(jobId);

  useEffect(() => {
    if (initialJob) return;
    let stop = false;
    setJobId(null); // un'altra struttura: la sua analisi più recente, non quella della precedente
    api.latestAnalysis(source)
      .then((r) => { if (!stop && r.job_id) setJobId((cur) => cur ?? r.job_id); })
      .catch(() => undefined);
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, initialJob]);

  const result = job?.status === "completed" ? job.result : null;
  const running = !!summary && ["queued", "running", "cancelling"].includes(summary.status);
  const req = msaRequest(msaMode, msaJob, msaText, row);
  const run = () =>
    api.analysis({ project_id: projectId ?? undefined, source, msa: req.msa ?? undefined })
      .then((j) => setJobId(j.id))
      .catch(toast.error);

  return (
    <div className="stack">
      <div className="stack-sm">
        <div className="row small" style={{ gap: 10 }}>
          <span>Conservazione</span>
          <Segmented value={msaMode} onChange={setMsaMode} options={[
            { value: "none", label: "Nessuna" },
            { value: "job", label: "Da un allineamento del progetto" },
            { value: "text", label: "Da sequenze o file" },
          ]} />
        </div>
        {msaMode === "job" && (
          alignJobs?.length ? (
            <select className="input" style={{ maxWidth: "100%" }} value={msaJob} onChange={(e) => setMsaJob(e.target.value)}>
              <option value="">— scegli un allineamento —</option>
              {alignJobs.map((j) => <option key={j.id} value={j.id}>{j.title} · {relTime(j.finished_at)}</option>)}
            </select>
          ) : <p className="small muted">Nessun allineamento completato nel progetto: calcolalo nella pagina Allineamenti (modalità multiplo) oppure incolla le sequenze.</p>
        )}
        {msaMode === "text" && (
          <div className="stack-sm">
            <textarea className="input mono" rows={6} value={msaText} onChange={(e) => setMsaText(e.target.value)} placeholder={">1UBQ\nMQIFVKTLTG...\n>omologo\nMQIFVKTLTG...\n(allineamento FASTA, Clustal, Stockholm…; sequenze di lunghezza diversa vengono allineate)"} />
            <div className="row"><SequenceFileButton onLoaded={(p) => setMsaText(fastaFromParsed(p))}>Carica allineamento da file</SequenceFileButton></div>
          </div>
        )}
        {msaMode !== "none" && (
          <input className="input" style={{ maxWidth: "100%" }} value={row} onChange={(e) => setRow(e.target.value)} placeholder="Riga della sequenza della struttura (facoltativa: altrimenti la più simile a ogni catena)" />
        )}
        <div className="row small" style={{ gap: 10 }}>
          <button className="btn btn-primary btn-sm" disabled={running || !!req.error} onClick={run}>
            <Icon name="play" size={13} /> {result ? "Ricalcola l'analisi" : "Analizza interazioni"}
          </button>
          {req.error && <span className="muted">{req.error}</span>}
          {result && job && <span className="muted">Analisi calcolata {relTime(job.finished_at)}{result.conservation ? ", con la conservazione" : ""}.</span>}
        </div>
      </div>
      {jobId && !result && <JobPanel summary={summary} onCancel={cancel} compact />}
      {result ? <AnalysisView result={result} url={url} /> : !jobId && (
        <Empty icon="layers" title="Analisi strutturale non ancora calcolata">
          Legami idrogeno, ponti salini, ponti disolfuro, interazioni aromatiche, interfacce fra catene e, con un allineamento multiplo, la conservazione mappata sulla struttura.
        </Empty>
      )}
    </div>
  );
}

/** Vista di un job `analysis` dal suo link (/quality?ajob=<id>), senza un'analisi di qualità. */
export function AnalysisJobCard({ jobId }: { jobId: string }) {
  // solo i parametri (la struttura): stato e risultato li segue il pannello
  const { data: job, error } = useAsync(() => api.job<AnalysisResult>(jobId), [jobId]);
  const source = job?.kind === "analysis" ? ((job.params?.source ?? null) as StructureSource | null) : null;
  return (
    <Card title={`Analisi strutturale${job ? ` · ${job.title.replace(/^Analisi strutturale · /, "")}` : ""}`}>
      {error ? <Alert tone="err">Analisi non trovata: {error.message}</Alert>
        : source ? <StructuralAnalysisPanel source={source} url={sourceFileUrl(source)} initialJob={jobId} />
          : job ? <Alert tone="warn">Il job {jobId} non è un'analisi strutturale.</Alert> : null}
    </Card>
  );
}
