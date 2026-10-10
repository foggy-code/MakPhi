// Analisi strutturale (F7): scheda "Interazioni" della pagina Qualità e vista di un job `analysis` (/quality?ajob=).
// Tabelle filtrabili di legami idrogeno, ponti salini, disolfuri e interazioni aromatiche (con il dettaglio geometrico
// dell'interazione scelta), interfacce fra catene, conservazione da un allineamento multiplo; il 3D si colora per
// interfaccia o conservazione (API di colorazione di Viewer3D) e un clic su un'interazione evidenzia i due residui.
// La logica sta in lib/interactions.ts, grafici e dettagli in lib/toolsView.ts.
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { AnalysisResult, InterfacePair, JobSummary, StructureSource } from "../api/types";
import { encodeSource } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import {
  analysisColoring, analysisColorOptions, chainFilterOptions, conservationStripRows, endLabel, fastaFromParsed, filterRows, followedAnalysis, highlightFor,
  initialPanelState, INTERACTION_LABEL, interactionCounts, interactionRows, INTERFACE_SCALE, interfaceHighlight, msaRequest, PLAUSIBILITY_TONE,
  residueFilterText, type AnalysisColorBy, type AnalysisPanelMemory, type InteractionKind, type InteractionRow, type MsaMode,
} from "../lib/interactions";
import { resiNumber, type ResidueRef } from "../lib/residueColoring";
import { conservationData, conservationSeries, interactionDetail, interfaceData, interfaceSeries } from "../lib/toolsView";
import { TABLE_ROW_HEIGHT, useVirtualRows } from "../lib/virtual";
import { ResidueStrip } from "./charts";
import { SequenceFileButton } from "./FileLoad";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { DetailPanel } from "./ToolParts";
import { ValueBars } from "./ValueBars";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, Card, DetailGrid, Disclosure, Empty, rowActivate, Segmented, Stat } from "./ui";

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
const residueId = (r: { chain: string; resnum: string }) => `${r.chain}:${r.resnum}`;

/** Tabella delle interazioni, a finestra oltre 200 righe (altezza fissa): il dettaglio va nel pannello sotto. */
function InteractionTable({ rows, selected, onPick, kind }: { rows: InteractionRow[]; selected: string | null; onPick: (r: InteractionRow) => void; kind: InteractionKind }) {
  const virtual = useVirtualRows<HTMLDivElement>({ count: rows.length, rowHeight: TABLE_ROW_HEIGHT, measure: true });
  if (!rows.length) return <p className="small text-2">Nessuna interazione con questi filtri.</p>;
  const rowStyle = virtual.active ? { height: TABLE_ROW_HEIGHT } : undefined;
  const [first, second] = kind === "hbonds" ? ["Donatore", "Accettore"] : kind === "salt_bridges" ? ["Catione", "Anione"] : kind === "aromatic" ? ["Residuo 1 (catione o anello)", "Anello"] : ["Cisteina", "Cisteina"];
  return (
    <div ref={virtual.ref} onScroll={virtual.onScroll} className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
      <table className="table">
        <thead><tr><th>{first}</th><th>{second}</th><th className="num">Distanza (Å)</th><th>Tipo</th><th>Geometria</th></tr></thead>
        <tbody>
          {virtual.range.padTop > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padTop }}><td colSpan={5} /></tr>}
          {rows.slice(virtual.range.start, virtual.range.end).map((r, n) => (
            <tr key={r.key} data-vrow={virtual.range.start + n} style={rowStyle} aria-selected={r.key === selected}
              className={`clickable${r.key === selected ? " selected" : ""}${virtual.active ? " virtual-row" : ""}`} {...rowActivate(() => onPick(r))}
              title="Evidenzia i due residui nel 3D e mostra la geometria">
              <td className="nowrap mono">{endLabel(r.a)}</td>
              <td className="nowrap mono">{endLabel(r.b)}</td>
              <td className="num">{fmtNum(r.distance, 2)}</td>
              <td className="nowrap">{r.type}{r.interChain && <> <Badge tone="info">fra catene</Badge></>}</td>
              <td className="small">{r.detail}</td>
            </tr>
          ))}
          {virtual.range.padBottom > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padBottom }}><td colSpan={5} /></tr>}
        </tbody>
      </table>
    </div>
  );
}

/** Una coppia di catene in contatto. Memo con callback stabili e `picked` solo se il residuo è della coppia: scrivere
 *  nel filtro o scegliere un residuo altrove non ridisegna le altre coppie (con complessi grandi sono decine). */
const InterfaceCard = memo(function InterfaceCard({ pair, picked, onHighlight, onResidue }: {
  pair: InterfacePair;
  picked: string | null;
  onHighlight: (pair: InterfacePair) => void;
  onResidue: (ref: ResidueRef, center: boolean) => void;
}) {
  const p = pair.plausibility;
  const i = pair.interactions;
  const series = useMemo(() => interfaceSeries(pair), [pair]);
  const name = pair.chains.join("–");
  return (
    <div className="stack-sm" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <div className="row" style={{ gap: 14, alignItems: "center" }}>
        <b>Catene {pair.chains[0]} – {pair.chains[1]}</b>
        <Badge tone={PLAUSIBILITY_TONE[p.level]} title={p.limits}>plausibilità {p.level}</Badge>
        <button type="button" className="btn btn-sm" onClick={() => onHighlight(pair)}><Icon name="cube" size={13} /> Evidenzia l'interfaccia</button>
      </div>
      <div className="grid grid-4">
        <Stat label="Area d'interfaccia" value={<>{fmtNum(pair.area, 0)}<small> Å²</small></>} note={`area sepolta totale ${fmtNum(pair.buried, 0)} Å² (÷ 2)`} tone={PLAUSIBILITY_TONE[p.level]}
          detail={<span>Sotto {fmtNum(p.thresholds.low, 0)} Å² plausibilità bassa, fino a {fmtNum(p.thresholds.high, 0)} Å² incerta, oltre compatibile con un'interfaccia biologica.</span>} />
        <Stat label="Residui" value={pair.chains.map((c) => pair.counts[c] ?? 0).join(" + ")} note={`ΔSASA > 1 Å² · ${pair.chains.join(" / ")}`} />
        <Stat label="Interazioni fra le catene" value={i.hbonds + i.salt_bridges + i.disulfides + i.pi_stacking + i.cation_pi}
          note={`${i.hbonds} legami H, ${i.salt_bridges} ponti salini, ${i.disulfides} disolfuri, ${i.pi_stacking + i.cation_pi} aromatiche`} />
        <Stat label="Area apolare" value={pair.apolar_fraction != null ? <>{fmtNum(pair.apolar_fraction * 100, 0)}<small>%</small></> : "—"} note={`residui: ${pair.composition.counts.apolari} apolari, ${pair.composition.counts.polari} polari, ${pair.composition.counts.carichi} carichi`}
          detail={<span>Composizione dei residui d'interfaccia: {Object.entries(pair.composition.percent).map(([k, v]) => `${k} ${fmtNum(v, 0)}%`).join(", ")}.</span>} />
      </div>
      <p className="small text-2">{p.text}. {p.limits}</p>
      {pair.chains.map((c) => (
        <div key={c} className="row small" style={{ gap: 4 }}>
          <span className="text-2" style={{ minWidth: 70 }}>Catena {c}:</span>
          {(pair.residues[c] ?? []).map((r) => (
            <button key={r.resnum} type="button" className="chip" title={`ΔSASA ${fmtNum(r.delta, 1)} Å² (apolare ${fmtNum(r.apolar_delta, 1)} Å²)`} onClick={() => onResidue({ chain: c, resnum: r.resnum }, true)}>
              {r.resname} {r.resnum}
            </button>
          ))}
        </div>
      ))}
      {series.items.length > 0 && (
        // il grafico si monta alla prima apertura; il titolo resta per il PNG e la vista espansa (qui lo dice la sezione)
        <Disclosure lazy title="Superficie sepolta per residuo (ΔSASA)" extra={`${series.items.length} residui`}>
          <ValueBars
            titleHidden
            className="flat"
            title={`ΔSASA dei residui d'interfaccia · catene ${name}`}
            subtitle="Superficie accessibile persa nel complesso, per residuo; colori come nel 3D (interfaccia)"
            filename={`interfaccia_${name}`}
            series={series}
            data={() => interfaceData(pair)}
            noun="residui"
            selected={picked}
            onSelect={(k) => {
              const [chain, resnum] = series.items[k].key.split(":");
              onResidue({ chain, resnum }, true);
            }}
            legend={
              <div className="legend">
                {INTERFACE_SCALE.bins.filter((b) => b.gt !== undefined).map((b) => <span key={b.label}><i style={{ background: b.color }} />{b.label}</span>)}
              </div>
            }
          />
        </Disclosure>
      )}
    </div>
  );
});

/** Risultato dell'analisi: 3D colorato, tabelle con il dettaglio dell'interazione scelta, interfacce, conservazione e
 *  criteri. */
export function AnalysisView({ result, url }: { result: AnalysisResult; url: string | null }) {
  const [kind, setKind] = useState<InteractionKind>("hbonds");
  const [chain, setChain] = useState("all");
  const [residue, setResidue] = useState("");
  const [colorBy, setColorBy] = useState<AnalysisColorBy>("none");
  const [focus, setFocus] = useState<Focus>([]);
  const [centerKey, setCenterKey] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [picked, setPicked] = useState<ResidueRef | null>(null);
  const counts = interactionCounts(result);
  const all = useMemo(() => interactionRows(result, kind), [result, kind]);
  const rows = useMemo(() => filterRows(all, chain, residue), [all, chain, residue]);
  const options = analysisColorOptions(result);
  const shownBy = options.find((o) => o.value === colorBy)?.available ? colorBy : "none";
  const coloring = useMemo(() => analysisColoring(result, shownBy), [result, shownBy]);
  const chains = result.structure.chains_detail.map((c) => c.chain);
  const s = result.interactions.summary;
  const iface = result.interfaces;
  const cons = result.conservation;
  const consSeries = useMemo(() => (cons ? conservationSeries(cons) : null), [cons]);
  const current = selected ? all.find((r) => r.key === selected) ?? null : null;
  const detail = useMemo(() => (selected ? interactionDetail(result, kind, selected) : null), [result, kind, selected]);

  // solo setter: callback stabili per le coppie d'interfaccia (memo)
  const show = useCallback((target: Focus) => {
    setFocus(target);
    setCenterKey((k) => k + 1);
  }, []);
  const pickRow = (r: InteractionRow) => {
    setSelected(r.key);
    show(highlightFor(r));
  };
  /** Un residuo (dal 3D, da un grafico, da un'interfaccia) diventa il filtro delle tabelle. */
  const pickResidue = useCallback((ref: ResidueRef, center: boolean) => {
    setResidue(residueFilterText(ref));
    setPicked({ chain: ref.chain, resnum: ref.resnum });
    const target = [{ chain: ref.chain, resi: resiNumber(ref.resnum) }];
    if (center) show(target);
    else setFocus(target);
  }, [show]);
  const highlightPair = useCallback((p: InterfacePair) => {
    setColorBy("interface");
    show(interfaceHighlight(p));
  }, [show]);

  return (
    <div className="stack">
      {result.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Legami idrogeno" value={s.hbonds}
          note={`${s.hbonds_backbone} scheletro, ${s.hbonds_mixed} misti, ${s.hbonds_sidechain} catene laterali · idrogeni ${result.interactions.hydrogens === "explicit" ? "del file" : "posizionati"}`}
          detail={s.chains > 1 ? <span>{s.inter_chain.hbonds} fra catene diverse.</span> : undefined} />
        <Stat label="Ponti salini" value={s.salt_bridges} note="N–O ≤ 4,0 Å (Barlow & Thornton 1983)"
          detail={s.chains > 1 ? <span>{s.inter_chain.salt_bridges} fra catene diverse.</span> : undefined} />
        <Stat label="Ponti disolfuro" value={s.disulfides} note="SG–SG 2,0 ± 0,2 Å"
          detail={s.chains > 1 ? <span>{s.inter_chain.disulfides} fra catene diverse.</span> : undefined} />
        <Stat label="Aromatiche" value={s.pi_stacking + s.cation_pi} note={`${s.pi_stacking} π-stacking, ${s.cation_pi} catione-π`}
          detail={s.chains > 1 ? <span>{s.inter_chain.aromatic} fra catene diverse.</span> : undefined} />
      </div>
      {url ? (
        <div className="stack-sm">
          <div className="tools-bar small">
            <label className="check">
              Colora per
              <select className="input input-sm" style={{ width: 240 }} value={shownBy} onChange={(e) => setColorBy(e.target.value as AnalysisColorBy)}>
                {options.map((o) => <option key={o.value} value={o.value} disabled={!o.available}>{o.label}{!o.available && o.note ? ` (${o.note})` : ""}</option>)}
              </select>
            </label>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setFocus([]); setSelected(null); setPicked(null); }}>Nessuna evidenza</button>
            <span className="text-2">Clic su un'interazione: i due residui a bastoncini arancioni · clic nel 3D: filtra le tabelle per quel residuo.</span>
          </div>
          <Viewer3D url={url} format={url.endsWith(".cif") ? "cif" : "pdb"} highlight={focus} centerKey={centerKey} coloring={coloring} onPickResidue={(r) => pickResidue(r, false)} height={380} />
          {coloring?.key === "conservation" && (
            <ResidueStrip residues={conservationStripRows(cons)} coloring={coloring} selected={picked} onSelect={(r) => pickResidue(r, true)} />
          )}
        </div>
      ) : <p className="small text-2">Struttura non disponibile per la visualizzazione 3D.</p>}

      <div className="stack-sm">
        <div className="tools-bar small">
          <Segmented label="Tipo di interazione" value={kind} onChange={(k) => { setKind(k); setSelected(null); }} options={KINDS.map((k) => ({ value: k, label: `${INTERACTION_LABEL[k]} (${counts[k]})` }))} />
          <select className="input input-sm" style={{ width: 200 }} value={chain} onChange={(e) => setChain(e.target.value)} aria-label="Filtro per catena">
            {chainFilterOptions(chains).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <input className="input input-sm" style={{ width: 200 }} value={residue} onChange={(e) => setResidue(e.target.value)} placeholder="Residuo: 27, K27, LYS27, A:27" aria-label="Filtro per residuo" />
          {residue && <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setResidue(""); setPicked(null); }}>Tutti i residui</button>}
          <span className="text-2">{rows.length} di {all.length}</span>
        </div>
        <InteractionTable key={kind} rows={rows} selected={selected} onPick={pickRow} kind={kind} />
        {current && detail && (
          <DetailPanel title={`${endLabel(current.a, false)} – ${endLabel(current.b, false)}`} label="Dettaglio dell'interazione"
            badges={<><Badge tone="accent">{current.type}</Badge>{current.interChain && <Badge tone="info">fra catene</Badge>}</>}
            actions={
              <button type="button" className="btn btn-sm" onClick={() => show(highlightFor(current))} title="Evidenzia e centra i due residui nella vista 3D">
                <Icon name="cube" size={13} /> Mostra nel 3D
              </button>
            }
            onClose={() => setSelected(null)}>
            <DetailGrid items={detail} />
          </DetailPanel>
        )}
      </div>

      {iface.chains.length > 1 && (
        <Card icon="layers" title="Interfacce fra catene" sub={`${iface.method} · residui con ΔSASA > ${fmtNum(iface.min_delta, 0)} Å²`}>
          {iface.pairs.length ? (
            <div className="stack">
              {iface.pairs.map((p) => (
                <InterfaceCard key={p.chains.join("-")} pair={p} picked={picked && p.chains.includes(picked.chain) ? residueId(picked) : null}
                  onHighlight={highlightPair} onResidue={pickResidue} />
              ))}
            </div>
          ) : <p className="small text-2">Nessuna coppia di catene si tocca: nessuna interfaccia.</p>}
          {iface.note && <p className="small text-2" style={{ marginTop: 8 }}>{iface.note}</p>}
        </Card>
      )}

      {cons && consSeries && (
        <Card icon="activity" title="Conservazione" sub={cons.method}>
          <div className="stack">
            <div className="stack-sm small">
              <div>{cons.sequences} sequenze, {cons.columns} colonne · {cons.source}.</div>
              {cons.chains.map((c) => (
                <div key={c.chain}>Catena <b>{c.chain}</b>: riga <span className="mono">{c.row}</span> (identità {fmtNum(c.identity, 1)}%, copertura {fmtNum(c.coverage, 1)}%) · {c.scored} di {c.residues} residui con punteggio, media {fmtNum(c.mean, 2)}</div>
              ))}
              {cons.unmatched.map((u) => (
                <div key={u.chain} className="text-2">Catena <b>{u.chain}</b> senza conservazione: la riga più simile ({u.best_row}) ha identità {fmtNum(u.identity, 1)}% e copertura {fmtNum(u.coverage, 1)}%.</div>
              ))}
            </div>
            {cons.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
            <ValueBars
              title="Conservazione per residuo"
              subtitle="Punteggio 0–1 (1 = conservato) · clic su una barra: il residuo nel 3D e nelle tabelle"
              filename={`conservazione_${result.label}`}
              series={consSeries}
              data={() => conservationData(cons)}
              noun="residui"
              selected={picked ? residueId(picked) : null}
              onSelect={(k) => pickResidue(cons.residues[k], true)}
              describe={(k) => {
                const r = cons.residues[k];
                return (
                  <div className="text-2">
                    {r.column == null ? "non allineato alla riga" : `colonna ${r.column}${r.row_aa && r.row_aa !== r.aa ? ` (${r.row_aa} nella riga)` : ""}`}
                    {r.gap_fraction != null && ` · gap ${fmtNum(r.gap_fraction * 100, 0)}%`}
                  </div>
                );
              }}
              legend={
                <div className="legend">
                  <span><i style={{ background: "var(--accent)" }} />Punteggio di conservazione</span>
                  <span><i style={{ background: "var(--coil)", height: 3, verticalAlign: 3 }} />Senza punteggio (gap o non allineato)</span>
                </div>
              }
            />
            <p className="small text-2">Punteggio 0–1 = (1 − entropia di Shannon normalizzata) × (1 − frazione di gap), con i pesi delle sequenze di Henikoff; colonne con più del {Math.round(cons.gap_cutoff * 100)}% di gap senza punteggio (Capra & Singh 2007). Nel 3D i colori della tavolozza di ConSurf su scala assoluta.</p>
          </div>
        </Card>
      )}

      <Disclosure title="Criteri e fonti">
        <ul className="tools-list">
          <li><b>Legami idrogeno</b>: D…A ≤ 3,5 Å e D…A–AA ≥ 90°; con l'idrogeno H…A ≤ 2,5 Å e D–H…A ≥ 120° (Baker & Hubbard 1984; angoli come HBPLUS, McDonald & Thornton 1994). Idrogeni del file se presenti, altrimenti posizionati dove la geometria è fissa (N–H dello scheletro come DSSP, NH degli anelli, NH₂ delle ammidi e di Arg); per OH e NH₃⁺, che ruotano, AA–D…A ≥ 90°.</li>
          <li><b>Ponti salini</b>: un O carico di Asp/Glu entro 4,0 Å da un N di Lys/Arg/His (Barlow & Thornton 1983); l'istidina è carica solo se protonata.</li>
          <li><b>Aromatiche</b>: π-stacking con centroidi ≤ 5,5 Å, spostamento ≤ 2,0 Å, parallelo (≤ 30°) o a T (≥ 60°); catione-π con NZ di Lys o CZ di Arg ≤ 6,0 Å dal centroide (McGaughey et al. 1998; Gallivan & Dougherty 1999; parametri di PLIP, Salentin et al. 2015).</li>
          <li><b>Ponti disolfuro</b>: SG–SG 2,0 ± 0,2 Å; χ3 di solito vicino a ±90° (Richardson 1981).</li>
          <li><b>Interfacce</b>: ΔSASA &gt; 1 Å² fra catena isolata e coppia di catene (Shrake & Rupley, sonda 1,4 Å); area = area sepolta / 2 come PISA. Plausibilità dalla sola area: &lt; 400 Å² bassa (contatti cristallini, Janin et al. 2008), 400–600 incerta, ≥ 600 compatibile (Lo Conte et al. 1999).</li>
          <li>Si considerano i soli amminoacidi: acque, ligandi e ioni sono esclusi.</li>
        </ul>
      </Disclosure>
    </div>
  );
}

/** Scheda "Interazioni" per una struttura: mostra l'analisi più recente (o quella indicata) e la rilancia.
 *  `memory`/`onMemory`: stato conservato dalla pagina (job seguito e scelte dell'allineamento), perché il pannello si
 *  smonta cambiando scheda; un'analisi in coda o in corso si riprende anche da `/api/analysis/latest`. */
export function StructuralAnalysisPanel({ source, url, initialJob, memory, onMemory }: {
  source: StructureSource;
  url: string | null;
  initialJob?: string | null;
  memory?: AnalysisPanelMemory | null;
  onMemory?: (memory: AnalysisPanelMemory) => void;
}) {
  const { projectId } = useProject();
  const toast = useToast();
  const sourceKey = encodeSource(source) ?? "";
  const restored = useRef(initialPanelState(sourceKey, memory, initialJob));
  const [jobId, setJobId] = useState<string | null>(restored.current.jobId);
  const [msaMode, setMsaMode] = useState<MsaMode>(restored.current.msaMode);
  const [msaJob, setMsaJob] = useState(restored.current.msaJob);
  const [msaText, setMsaText] = useState(restored.current.msaText);
  const [row, setRow] = useState(restored.current.row);
  const { data: alignJobs } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "align", status: "completed", limit: 30 }), [projectId]);
  const { job, summary, cancel } = useJob<AnalysisResult>(jobId);
  useEffect(() => {
    onMemory?.({ sourceKey, jobId, msaMode, msaJob, msaText, row });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- si avvisa solo il cambio; onMemory può cambiare a ogni disegno del genitore
  }, [sourceKey, jobId, msaMode, msaJob, msaText, row]);

  useEffect(() => {
    if (initialJob) return;
    let stop = false;
    // la stessa struttura di prima (ritorno alla scheda): il job seguito; un'altra: la sua analisi, non quella della
    // precedente
    const kept = restored.current.sourceKey === sourceKey ? restored.current.jobId : null;
    restored.current = { ...restored.current, sourceKey: "" };
    setJobId(kept);
    api.latestAnalysis(source)
      .then((r) => { if (!stop) setJobId((cur) => followedAnalysis(cur, r)); })
      .catch(() => undefined);
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sourceKey riassume source: il job seguito si sceglie solo cambiando struttura
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
        <div className="tools-bar small">
          <span className="text-2" style={{ fontWeight: 600 }}>Conservazione</span>
          <Segmented label="Conservazione da" value={msaMode} onChange={setMsaMode} options={[
            { value: "none", label: "Nessuna" },
            { value: "job", label: "Da un allineamento del progetto" },
            { value: "text", label: "Da sequenze o file" },
          ]} />
        </div>
        {msaMode === "job" && (
          alignJobs?.length ? (
            <select className="input" style={{ maxWidth: "100%" }} value={msaJob} onChange={(e) => setMsaJob(e.target.value)} aria-label="Allineamento del progetto">
              <option value="">— scegli un allineamento —</option>
              {alignJobs.map((j) => <option key={j.id} value={j.id}>{j.title} · {relTime(j.finished_at)}</option>)}
            </select>
          ) : <p className="small text-2">Nessun allineamento completato nel progetto: calcolalo nella pagina Allineamenti (modalità multiplo) oppure incolla le sequenze.</p>
        )}
        {msaMode === "text" && (
          <div className="stack-sm">
            <textarea className="input mono" rows={6} value={msaText} onChange={(e) => setMsaText(e.target.value)} aria-label="Allineamento multiplo"
              placeholder={">1UBQ\nMQIFVKTLTG...\n>omologo\nMQIFVKTLTG...\n(allineamento FASTA, Clustal, Stockholm…; sequenze di lunghezza diversa vengono allineate)"} />
            <div className="row"><SequenceFileButton onLoaded={(p) => setMsaText(fastaFromParsed(p))}>Carica allineamento da file</SequenceFileButton></div>
          </div>
        )}
        {msaMode !== "none" && (
          <input className="input" style={{ maxWidth: "100%" }} value={row} onChange={(e) => setRow(e.target.value)} aria-label="Riga della sequenza della struttura"
            placeholder="Riga della sequenza della struttura (facoltativa: altrimenti la più simile a ogni catena)" />
        )}
        <div className="tools-bar small">
          <button type="button" className="btn btn-primary btn-sm" disabled={running || !!req.error} onClick={run}>
            <Icon name="play" size={13} /> {result ? "Ricalcola l'analisi" : "Analizza interazioni"}
          </button>
          {req.error && <span className="text-2">{req.error}</span>}
          {result && job && <span className="text-2">Analisi calcolata {relTime(job.finished_at)}{result.conservation ? ", con la conservazione" : ""}.</span>}
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
    <Card icon="layers" title={`Analisi strutturale${job ? ` · ${job.title.replace(/^Analisi strutturale · /, "")}` : ""}`}>
      {error ? <Alert tone="err">Analisi non trovata: {error.message}</Alert>
        : source ? <StructuralAnalysisPanel source={source} url={sourceFileUrl(source)} initialJob={jobId} />
          : job ? <Alert tone="warn">Il job {jobId} non è un'analisi strutturale.</Alert> : null}
    </Card>
  );
}
