// Tasche (fpocket) ed elettrostatica (PDB2PQR + APBS) di una struttura (F7b): schede "Tasche" ed "Elettrostatica"
// della pagina Qualità e vista di un job dal suo link (/quality?pjob=, /quality?ejob=). Ogni scheda mostra il calcolo
// più recente sulla struttura (`/api/<tipo>/latest`) e lo lancia o rilancia; la logica è in lib/pockets.ts,
// lib/electrostatics.ts, lib/toolJobs.ts e, per grafici e dettagli, lib/toolsView.ts.
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { ElectrostaticsResidue, ElectrostaticsResult, Pocket, PocketsResult, StructureSource, SystemInfo } from "../api/types";
import { encodeSource } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import {
  chargeBreakdown, chargeText, EXTREME_MIN_POINTS, extremeResidues, MV_PER_KT_E, potentialColoring, potentialStripRows, potentialText,
} from "../lib/electrostatics";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { followedAnalysis } from "../lib/interactions";
import { DRUGGABLE, druggabilityLabel, isDruggable, pocketColor, pocketColoring, pocketHighlight, pocketOfResidue, pocketSpheres, residueList } from "../lib/pockets";
import { resiNumber, type ResidueRef } from "../lib/residueColoring";
import { missingTools, missingToolsText, rememberedJob, type ToolJobMemory } from "../lib/toolJobs";
import { pocketDetail, pocketsData, pocketSeries, POCKET_METRICS, potentialData, potentialDetail, potentialSeries, type PocketMetric } from "../lib/toolsView";
import { ResidueStrip } from "./charts";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { sourceFileUrl } from "./StructuralAnalysis";
import { DetailPanel, ExpandAll, useRevealRow } from "./ToolParts";
import { ValueBars } from "./ValueBars";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, BarCell, Card, DetailGrid, Disclosure, Empty, ExpandButton, Field, rowActivate, Segmented, Stat, useExpanded } from "./ui";

type ToolKind = "pockets" | "electrostatics";
type Focus = { chain: string; resi: number }[];

const TOOLS: Record<ToolKind, { keys: string[]; purpose: string }> = {
  pockets: { keys: ["fpocket"], purpose: "Ricerca delle tasche" },
  electrostatics: { keys: ["pdb2pqr", "apbs"], purpose: "Calcolo elettrostatico" },
};

/** Job seguito da una scheda: quello del link o conservato dalla pagina, poi quello in corso o il più recente. */
function useToolJob<R>(kind: ToolKind, source: StructureSource, initialJob?: string | null, memory?: ToolJobMemory | null, onMemory?: (m: ToolJobMemory) => void) {
  const sourceKey = encodeSource(source) ?? "";
  const start = useRef(rememberedJob(sourceKey, memory, initialJob));
  const [jobId, setJobId] = useState<string | null>(start.current);
  const { job, summary, cancel } = useJob<R>(jobId);
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  useEffect(() => {
    onMemory?.({ sourceKey, jobId });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- si avvisa solo il cambio; onMemory può cambiare a ogni disegno del genitore
  }, [sourceKey, jobId]);
  useEffect(() => {
    if (initialJob) return;
    let stop = false;
    const kept = rememberedJob(sourceKey, memory, null);
    setJobId(kept);
    api.latestToolJob(kind, source)
      .then((r) => { if (!stop) setJobId((cur) => followedAnalysis(cur, r)); })
      .catch(() => undefined);
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo cambiando struttura (sourceKey riassume source); kind è fisso, memory si legge in quel momento
  }, [sourceKey, initialJob]);
  const result = job?.status === "completed" ? (job.result as R) : null;
  const running = !!summary && ["queued", "running", "cancelling"].includes(summary.status);
  const missing = missingToolsText(missingTools(info, TOOLS[kind].keys), TOOLS[kind].purpose);
  return { jobId, setJobId, job, summary, cancel, result, running, missing };
}

// ---------------------------------------------------------------------------------- tasche
const POCKET_COLUMNS = 9;

/** Dettaglio di una tasca: tutti i descrittori di fpocket e i residui che la rivestono (clic: il residuo nel 3D). */
function PocketDetail({ pocket, onResidue }: { pocket: Pocket; onResidue: (r: Pocket["residues"][number]) => void }) {
  return (
    <div className="stack-sm">
      <DetailGrid items={pocketDetail(pocket)} />
      <h4>Residui che la rivestono ({pocket.residues.length})</h4>
      <div className="tools-chips">
        {pocket.residues.map((r) => (
          <button key={`${r.chain}${r.resnum}`} type="button" className="chip" title="Evidenzia il residuo nel 3D" onClick={() => onResidue(r)}>
            {r.resname} {r.chain}{r.resnum}
          </button>
        ))}
      </div>
    </div>
  );
}

function PocketTable({ pockets, selected, open, onPick, onResidue, allOpen, onExpandAll, onCollapseAll }: {
  pockets: Pocket[];
  selected: number | null;
  open: { has: (id: number) => boolean; toggle: (id: number) => void };
  onPick: (p: Pocket) => void;
  onResidue: (r: Pocket["residues"][number]) => void;
  allOpen: boolean;
  onExpandAll: () => void;
  onCollapseAll: () => void;
}) {
  const ref = useRevealRow<HTMLDivElement>(selected == null ? null : String(selected));
  return (
    <div className="stack-sm">
      <div className="tools-bar">
        <span className="small text-2">{pockets.length} tasche per druggability · clic su una riga: la tasca nel 3D e tutti i descrittori</span>
        {pockets.length > 1 && <div className="right"><ExpandAll allOpen={allOpen} onExpand={onExpandAll} onCollapse={onCollapseAll} /></div>}
      </div>
      <div ref={ref} className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 34 }} aria-label="Dettagli" />
              <th>#</th><th>Tasca</th><th title={`Schmidtke & Barril 2010: ≥ ${DRUGGABLE} probabilmente druggabile`}>Druggability</th>
              <th className="num" title="Punteggio di fpocket (ordine delle tasche del programma)">Punteggio</th>
              <th className="num">Volume (Å³)</th><th className="num">Sfere α</th><th className="num" title="Punteggio di idrofobicità di fpocket">Idrofob.</th>
              <th>Residui</th>
            </tr>
          </thead>
          <tbody>
            {pockets.map((p) => {
              const isOpen = open.has(p.id);
              return (
                <Fragment key={p.id}>
                  <tr data-row-key={String(p.id)} className={`clickable${p.id === selected ? " selected" : isOpen ? " row-open" : ""}`}
                    aria-selected={p.id === selected} {...rowActivate(() => onPick(p))} title="Evidenzia la tasca nel 3D">
                    <td><ExpandButton open={isOpen} onClick={() => open.toggle(p.id)} label={`descrittori della tasca ${p.id}`} /></td>
                    <td className="nowrap"><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: pocketColor(p.rank), marginRight: 6 }} />{p.rank}</td>
                    <td className="nowrap">Tasca {p.id} {isDruggable(p) && <Badge tone="ok">druggabile</Badge>}</td>
                    <td style={{ minWidth: 140 }}><BarCell value={(p.druggability ?? 0) * 100} tone={isDruggable(p) ? "var(--ok)" : "var(--text-2)"} label={fmtNum(p.druggability ?? null, 3)} /></td>
                    <td className="num">{fmtNum(p.score ?? null, 3)}</td>
                    <td className="num">{fmtNum(p.volume ?? null, 0)}</td>
                    <td className="num">{p.alpha_spheres ?? "—"}</td>
                    <td className="num">{fmtNum(p.hydrophobicity_score ?? null, 1)}</td>
                    <td className="small" style={{ minWidth: 280 }}>{p.residues.length} · {residueList(p, 8)}</td>
                  </tr>
                  {isOpen && (
                    <tr className="row-detail">
                      <td colSpan={POCKET_COLUMNS}><PocketDetail pocket={p} onResidue={onResidue} /></td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Risultato di fpocket: 3D con le tasche colorate, sfere alfa e centri; grafico dei descrittori; tabella ordinata per
 *  druggability con le righe espandibili. La tasca scelta (riga, barra, residuo nel 3D) è la stessa ovunque. */
export function PocketsView({ result, jobId, url }: { result: PocketsResult; jobId: string; url: string | null }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [focus, setFocus] = useState<Focus>([]);
  const [centerKey, setCenterKey] = useState(0);
  const [metric, setMetric] = useState<PocketMetric>("druggability");
  const open = useExpanded<number>();
  const coloring = useMemo(() => pocketColoring(result, selected), [result, selected]);
  const spheres = useMemo(() => pocketSpheres(result, selected), [result, selected]);
  const series = useMemo(() => pocketSeries(result.pockets, metric), [result, metric]);
  const s = result.summary;
  const chosen = result.pockets.find((p) => p.id === selected) ?? null;
  const show = (p: Pocket, center: boolean) => {
    setSelected(p.id);
    setFocus(pocketHighlight(p));
    if (center) setCenterKey((k) => k + 1);
  };
  /** Dalla riga o dal grafico: la tasca nel 3D e la riga aperta (riscegliere ricentra senza chiuderla: si chiude con
   *  la freccia o "Chiudi tutte"). */
  const reveal = (p: Pocket) => {
    show(p, true);
    open.open(p.id);
  };
  /** Dal 3D: la tasca del residuo (la prima per druggability), senza spostare la vista. */
  const pick3d = (ref: ResidueRef) => {
    const p = pocketOfResidue(result, ref);
    if (p) {
      show(p, false);
      open.open(p.id);
    } else setFocus([{ chain: ref.chain, resi: resiNumber(ref.resnum) }]);
  };
  const showResidue = (r: { chain: string; resnum: string }) => {
    setFocus([{ chain: r.chain, resi: resiNumber(r.resnum) }]);
    setCenterKey((k) => k + 1);
  };
  return (
    <div className="stack">
      {result.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Tasche" value={s.count} note={`fpocket, ${result.params.options}`}
          detail={<span>{result.params.program}; tasche di almeno 15 sfere alfa sulla sola proteina ({result.structure.residues} residui, {result.structure.chains} catene).</span>} />
        <Stat label="Probabilmente druggabili" value={s.druggable} note={`druggability ≥ ${fmtNum(s.threshold, 1)} (Schmidtke & Barril 2010)`} tone={s.druggable ? "ok" : undefined} />
        <Stat label="Druggability migliore" value={s.best ? fmtNum(s.best.druggability, 3) : "—"} note={s.best ? `tasca ${s.best.id} · ${s.best.residues} residui` : undefined} />
        <Stat label="Volume della migliore" value={s.best?.volume != null ? <>{fmtNum(s.best.volume, 0)}<small> Å³</small></> : "—"} note="stima Monte Carlo di fpocket" />
      </div>
      {!result.pockets.length ? <Alert tone="info">fpocket non ha trovato tasche con almeno 15 sfere alfa.</Alert> : (
        <>
          {url ? (
            <div className="stack-sm">
              <div className="tools-bar small">
                {selected != null && (
                  <button type="button" className="btn btn-sm" onClick={() => { setSelected(null); setFocus([]); }}>
                    <Icon name="layers" size={13} /> Mostra tutte le tasche
                  </button>
                )}
                <span className="text-2">
                  {chosen ? `Tasca ${chosen.id}: ${druggabilityLabel(chosen)}, residui a bastoncini arancioni.` : `Le prime ${Math.min(8, result.pockets.length)} tasche colorate per druggability.`}
                  {" "}Sferette: centri delle sfere alfa; sfera bianca: centro della tasca. Clic su un residuo: la sua tasca.
                </span>
              </div>
              <Viewer3D url={url} format={url.endsWith(".cif") ? "cif" : "pdb"} coloring={coloring} spheres={spheres} highlight={focus} centerKey={centerKey} onPickResidue={pick3d} height={400} />
            </div>
          ) : <p className="small text-2">Struttura non disponibile per la visualizzazione 3D.</p>}
          <ValueBars
            title="Descrittori delle tasche"
            subtitle={`${series.label}${series.unit ? ` (${series.unit})` : ""} per tasca, nell'ordine della tabella; colori come nel 3D · clic su una barra per la tasca`}
            filename={`tasche_${result.label}_${metric}`}
            series={series}
            data={() => pocketsData(result.pockets)}
            noun="tasche"
            selected={selected == null ? null : String(selected)}
            onSelect={(i) => reveal(result.pockets[i])}
            describe={(i) => {
              const p = result.pockets[i];
              return (
                <>
                  <div>Druggability {fmtNum(p.druggability ?? null, 3)} · volume {fmtNum(p.volume ?? null, 0)} Å³</div>
                  <div className="text-2">{p.residues.length} residui: {residueList(p, 6)}</div>
                </>
              );
            }}
            controls={<Segmented label="Metrica" value={metric} onChange={setMetric} options={POCKET_METRICS} />}
          />
          <PocketTable pockets={result.pockets} selected={selected} open={open} onPick={reveal} onResidue={showResidue}
            allOpen={open.size === result.pockets.length} onExpandAll={() => open.expandAll(result.pockets.map((p) => p.id))} onCollapseAll={open.collapseAll} />
        </>
      )}
      <div className="row small" style={{ gap: 8 }}>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.pdb, true)} title="PDB di fpocket: proteina e sfere alfa (residui STP)"><Icon name="download" size={13} /> PDB con le sfere</a>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, "structure_out/structure_info.txt", true)}><Icon name="download" size={13} /> Descrittori (info.txt)</a>
      </div>
      <Disclosure title="Metodo e fonti">
        <ul className="tools-list">
          <li><b>fpocket 4</b> (Le Guilloux, Schmidtke & Tufféry, BMC Bioinformatics 10:168, 2009): sfere alfa (vuote, tangenti a 4 atomi) con raggio 3,4–6,2 Å raggruppate in tasche di almeno 15 sfere; parametri predefiniti. Volume stimato con Monte Carlo (piccole differenze fra esecuzioni).</li>
          <li><b>Druggability</b> 0–1 (Schmidtke & Barril, J. Med. Chem. 53:5858, 2010): regressione logistica sui descrittori della tasca; ≥ 0,5 probabilmente in grado di legare una piccola molecola simile a un farmaco. È un indicatore statistico, non una previsione di affinità.</li>
          <li>Il calcolo usa una copia della struttura con la sola proteina: ligandi, ioni e acque sono esclusi, quindi i siti che occupano compaiono come tasche vuote.</li>
        </ul>
      </Disclosure>
    </div>
  );
}

/** Scheda "Tasche": il calcolo più recente sulla struttura (o quello del link) e il pulsante per lanciarlo. */
export function PocketsPanel({ source, url, initialJob, memory, onMemory }: { source: StructureSource; url: string | null; initialJob?: string | null; memory?: ToolJobMemory | null; onMemory?: (m: ToolJobMemory) => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const t = useToolJob<PocketsResult>("pockets", source, initialJob, memory, onMemory);
  const run = () => api.pockets({ project_id: projectId ?? undefined, source }).then((j) => t.setJobId(j.id)).catch(toast.error);
  return (
    <div className="stack">
      {t.missing && <Alert tone="warn">{t.missing}</Alert>}
      <div className="tools-bar small">
        <button type="button" className="btn btn-primary btn-sm" disabled={t.running || !!t.missing} onClick={run}>
          <Icon name="play" size={13} /> {t.result ? "Ricalcola le tasche" : "Cerca le tasche (fpocket)"}
        </button>
        {t.result && t.job && <span className="text-2">Calcolate {relTime(t.job.finished_at)}.</span>}
      </div>
      {t.jobId && !t.result && <JobPanel summary={t.summary} onCancel={t.cancel} compact />}
      {t.result && t.jobId ? <PocketsView result={t.result} jobId={t.jobId} url={url} /> : !t.jobId && (
        <Empty icon="search" title="Tasche non ancora calcolate">
          fpocket cerca le cavità della superficie, le ordina per druggability e indica i residui che le rivestono.
        </Empty>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------- elettrostatica
function ExtremeList({ title, rows, onPick }: { title: string; rows: ElectrostaticsResidue[]; onPick: (r: ElectrostaticsResidue) => void }) {
  return (
    <div className="stack-sm">
      <h4>{title}</h4>
      {!rows.length ? <p className="small text-2">Nessuno.</p> : (
        <div className="tools-chips">
          {rows.map((r) => (
            <button key={`${r.chain}${r.resnum}`} type="button" className="chip" onClick={() => onPick(r)} title={potentialText(r.potential)}>
              {r.resname} {r.chain}{r.resnum} <span className="text-2">{fmtNum(r.potential, 1)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const residueId = (r: { chain: string; resnum: string }) => `${r.chain}:${r.resnum}`;

/** Risultato del calcolo elettrostatico: 3D, striscia e grafico del potenziale per residuo, residuo scelto, residui
 *  estremi e carichi, file. La selezione (3D, striscia, grafico, elenchi) è condivisa. */
export function ElectrostaticsView({ result, jobId, url }: { result: ElectrostaticsResult; jobId: string; url: string | null }) {
  const [focus, setFocus] = useState<Focus>([]);
  const [selected, setSelected] = useState<ResidueRef | null>(null);
  const [centerKey, setCenterKey] = useState(0);
  const coloring = useMemo(() => potentialColoring(result), [result]);
  const rows = useMemo(() => potentialStripRows(result), [result]);
  const extremes = useMemo(() => extremeResidues(result), [result]);
  const series = useMemo(() => potentialSeries(result), [result]);
  const byId = useMemo(() => new Map(result.residues.map((r) => [residueId(r), r])), [result]);
  const mv = result.scale?.mv_per_unit ?? MV_PER_KT_E;
  const s = result.summary;
  const p = result.parameters;
  const g = result.grid;
  const current = selected ? byId.get(residueId(selected)) ?? null : null;
  const pick = (r: ResidueRef, center = true) => {
    setSelected({ chain: r.chain, resnum: r.resnum });
    setFocus([{ chain: r.chain, resi: resiNumber(r.resnum) }]);
    if (center) setCenterKey((k) => k + 1);
  };
  return (
    <div className="stack">
      {result.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label={`Carica netta a pH ${result.ph.toLocaleString("it-IT")}`} value={chargeText(result.net_charge)} note={chargeBreakdown(result)}
          detail={<span>Residui carichi (|carica| ≥ 0,5 nel PQR, estremità comprese nel primo e nell'ultimo residuo): {result.charges.positive} positivi e {result.charges.negative} negativi.</span>} />
        <Stat label="Potenziale di superficie" value={<span style={{ fontSize: 18 }}>{fmtNum(s.min, 1)} … {fmtNum(s.max, 1)}<small> kT/e</small></span>} note={`media ${fmtNum(s.mean, 2)} · ${s.residues} residui esposti`}
          detail={<span>Mappa: da {fmtNum(result.map.min, 1)} a {fmtNum(result.map.max, 1)} kT/e su {result.map.points.toLocaleString("it-IT")} punti; 1 kT/e = {fmtNum(mv, 2)} mV.</span>} />
        <Stat label="Residui marcati" value={<>{s.positive}<small> +</small> / {s.negative}<small> −</small></>} note={`|potenziale| > ${fmtNum(s.threshold, 0)} kT/e`} />
        <Stat label="Griglia di APBS" value={<span style={{ fontSize: 18 }}>{g.dime.join(" × ")}</span>} note={`passo ${g.spacing.map((v) => fmtNum(v, 2)).join("/")} Å · ${fmtNum(g.memory_mb, 0)} MB stimati`}
          detail={
            <DetailGrid items={[
              { label: "Griglia grossolana", value: `${g.cglen.map((v) => fmtNum(v, 1)).join(" × ")} Å` },
              { label: "Griglia fine", value: `${g.fglen.map((v) => fmtNum(v, 1)).join(" × ")} Å` },
              { label: "Centro", value: `${g.center.map((v) => fmtNum(v, 1)).join(", ")} Å` },
              { label: "Memoria", value: `${fmtNum(g.memory_mb, 0)} di ${fmtNum(g.ceiling_mb, 0)} MB${g.coarsened ? " · griglia diradata per stare nel limite" : ""}` },
            ]} />
          } />
      </div>
      {url && coloring ? (
        <div className="stack-sm">
          <div className="tools-bar small">
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setFocus([]); setSelected(null); }}>Nessuna evidenza</button>
            <span className="text-2">Rosso negativo, blu positivo, saturato a ±5 kT/e; grigio: residui sepolti. Clic su un residuo per evidenziarlo.</span>
          </div>
          <Viewer3D url={url} format={url.endsWith(".cif") ? "cif" : "pdb"} coloring={coloring} highlight={focus} centerKey={centerKey} onPickResidue={(r) => pick(r, false)} height={400} />
          <ResidueStrip residues={rows} coloring={coloring} selected={selected} onSelect={(r) => pick(r)} />
        </div>
      ) : <p className="small text-2">Struttura non disponibile per la visualizzazione 3D.</p>}
      <ValueBars
        title="Potenziale per residuo"
        subtitle={`Media sugli atomi esposti (kT/e, ${result.forcefield}, pH ${result.ph.toLocaleString("it-IT")}) · clic su una barra per il residuo`}
        filename={`potenziale_${result.label}`}
        series={series}
        data={() => potentialData(result)}
        noun="residui"
        selected={selected ? residueId(selected) : null}
        onSelect={(i) => pick(result.residues[i])}
        describe={(i) => {
          const r = result.residues[i];
          return (
            <>
              <div>{potentialText(r.potential, mv)}</div>
              {r.charge != null && Math.abs(r.charge) >= 0.5 && <div>Carica {chargeText(r.charge)}</div>}
              <div className="text-2">{r.exposed_atoms} atomi esposti · {r.points} punti</div>
            </>
          );
        }}
        legend={
          <div className="legend">
            <span><i style={{ background: "var(--err)" }} />Negativo</span>
            <span><i style={{ background: "var(--info)" }} />Positivo</span>
            <span><i style={{ background: "var(--err)", opacity: 0.45, marginRight: 2 }} /><i style={{ background: "var(--info)", opacity: 0.45 }} />Entro ±{fmtNum(s.threshold, 0)} kT/e (non marcato)</span>
            <span><i style={{ background: "var(--coil)", height: 3, verticalAlign: 3 }} />Sepolto (nessun valore)</span>
          </div>
        }
      />
      {current && (
        <DetailPanel title={`${current.resname} ${current.chain}${current.resnum}`} label={`Potenziale di ${current.resname} ${current.chain}${current.resnum}`}
          badges={current.potential != null && Math.abs(current.potential) > s.threshold
            ? <Badge tone={current.potential < 0 ? "err" : "info"}>{current.potential < 0 ? "negativo marcato" : "positivo marcato"}</Badge> : undefined}
          actions={
            <button type="button" className="btn btn-sm" onClick={() => pick(current)} title="Evidenzia e centra il residuo nella vista 3D">
              <Icon name="cube" size={13} /> Mostra nel 3D
            </button>
          }
          onClose={() => { setSelected(null); setFocus([]); }}>
          <DetailGrid items={potentialDetail(current, mv, s.threshold)} />
        </DetailPanel>
      )}
      <div className="grid grid-2">
        <ExtremeList title="Più positivi" rows={extremes.positive} onPick={pick} />
        <ExtremeList title="Più negativi" rows={extremes.negative} onPick={pick} />
      </div>
      <p className="small text-2">Fra i residui più carichi solo quelli con almeno {EXTREME_MIN_POINTS} punti di superficie: il valore di un residuo quasi sepolto è poco stabile (resta comunque nel 3D, nella striscia e nel grafico).</p>
      {result.charges.charged.length > 0 && (
        <Disclosure title="Residui carichi" extra={`${result.charges.charged.length} residui`}>
          <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
            <table className="table">
              <thead><tr><th>Residuo</th><th className="num">Carica</th><th className="num">Potenziale (kT/e)</th><th className="num">mV</th><th className="num">Atomi esposti</th></tr></thead>
              <tbody>
                {result.charges.charged.map((c) => {
                  const r = byId.get(residueId(c));
                  const sel = !!selected && residueId(selected) === residueId(c);
                  return (
                    <tr key={residueId(c)} className={`clickable${sel ? " selected" : ""}`} aria-selected={sel} {...rowActivate(() => pick(c))} title="Evidenzia il residuo nel 3D">
                      <td className="nowrap"><b>{c.resname}</b> {c.chain}{c.resnum}</td>
                      <td className="num">{chargeText(c.charge)}</td>
                      <td className="num">{fmtNum(r?.potential ?? null, 2)}</td>
                      <td className="num">{r?.potential != null ? fmtNum(r.potential * mv, 0) : "—"}</td>
                      <td className="num">{r?.exposed_atoms ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Disclosure>
      )}
      <div className="row small" style={{ gap: 8 }}>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.map.file, true)} title="Mappa OpenDX del potenziale (kT/e): PyMOL, Chimera, VMD"><Icon name="download" size={13} /> Mappa del potenziale (.dx)</a>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, "structure.pqr", true)} title="Cariche e raggi di PDB2PQR"><Icon name="download" size={13} /> PQR</a>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, "apbs.in", true)} title="Input di APBS per ripetere il calcolo"><Icon name="download" size={13} /> Input APBS</a>
      </div>
      <Disclosure title="Parametri e fonti">
        <ul className="tools-list">
          <li><b>PDB2PQR 3</b> (Jurrus et al., Protein Sci. 27:112, 2018) con campo di forza <b>{result.forcefield}</b>{result.forcefield === "PARSE" ? " (Sitkoff, Sharp & Honig 1994: parametrizzato per Poisson-Boltzmann)" : " (AMBER ff99 di PDB2PQR)"}; protonazione a pH {result.ph.toLocaleString("it-IT")} con <b>PROPKA 3</b> (Olsson et al. 2011). Carica dei residui: somma delle cariche atomiche, estremità della catena comprese nel primo e nell'ultimo residuo.</li>
          <li><b>APBS</b> (Baker et al., PNAS 98:10037, 2001): Poisson-Boltzmann linearizzata, mg-auto con griglie dimensionate come psize.py; dielettrico {String(p.pdie)} (proteina) e {String(p.sdie)} (solvente), forza ionica {fmtNum(Number(p.ionic_strength) * 1000, 0)} mM (ioni ±1 di raggio {String(p.ion_radius)} Å), {String(p.temperature)} K, superficie molecolare liscia con sonda {String(p.srad)} Å.</li>
          <li><b>Potenziale per residuo</b>: interpolazione della mappa sui punti della superficie accessibile al solvente (sonda {fmtNum(result.sampling.probe, 1)} Å, {result.sampling.points_per_atom} punti per atomo, Shrake & Rupley 1973), media per atomo e poi sugli atomi esposti del residuo. 1 kT/e = {fmtNum(mv, 2)} mV a 298,15 K.</li>
          {Object.keys(result.timings ?? {}).length > 0 && <li>Tempi: {Object.entries(result.timings).map(([k, v]) => `${k} ${fmtNum(v, 1)} s`).join(", ")}.</li>}
        </ul>
      </Disclosure>
    </div>
  );
}

/** Scheda "Elettrostatica": opzioni (campo di forza, pH), calcolo più recente e lancio. */
export function ElectrostaticsPanel({ source, url, initialJob, memory, onMemory }: { source: StructureSource; url: string | null; initialJob?: string | null; memory?: ToolJobMemory | null; onMemory?: (m: ToolJobMemory) => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const t = useToolJob<ElectrostaticsResult>("electrostatics", source, initialJob, memory, onMemory);
  const [forcefield, setForcefield] = useState<"PARSE" | "AMBER">("PARSE");
  const [ph, setPh] = useState(7);
  const phError = !Number.isFinite(ph) || ph < 0 || ph > 14 ? "pH fra 0 e 14" : null;
  const run = () => api.electrostatics({ project_id: projectId ?? undefined, source, forcefield, ph }).then((j) => t.setJobId(j.id)).catch(toast.error);
  return (
    <div className="stack">
      {t.missing && <Alert tone="warn">{t.missing}</Alert>}
      <div className="tools-bar small" style={{ alignItems: "end" }}>
        <Field label="Campo di forza">
          <select className="input" style={{ width: 230 }} value={forcefield} onChange={(e) => setForcefield(e.target.value as "PARSE" | "AMBER")}>
            <option value="PARSE">PARSE (per Poisson-Boltzmann)</option>
            <option value="AMBER">AMBER</option>
          </select>
        </Field>
        <Field label="pH (PROPKA)">
          <input className="input" style={{ width: 90 }} type="number" min={0} max={14} step={0.1} value={ph} onChange={(e) => setPh(Number(e.target.value))} />
        </Field>
        <button type="button" className="btn btn-primary btn-sm" disabled={t.running || !!t.missing || !!phError} onClick={run}>
          <Icon name="play" size={13} /> {t.result ? "Ricalcola" : "Calcola il potenziale (APBS)"}
        </button>
        {phError && <span style={{ color: "var(--warn)" }}>{phError}</span>}
        {t.result && t.job && <span className="text-2">Calcolato {relTime(t.job.finished_at)} ({t.result.forcefield}, pH {t.result.ph.toLocaleString("it-IT")}).</span>}
      </div>
      {t.jobId && !t.result && <JobPanel summary={t.summary} onCancel={t.cancel} compact />}
      {t.result && t.jobId ? <ElectrostaticsView result={t.result} jobId={t.jobId} url={url} /> : !t.jobId && (
        <Empty icon="activity" title="Potenziale elettrostatico non ancora calcolato">
          PDB2PQR assegna cariche e raggi a pH 7 (PROPKA), APBS risolve l'equazione di Poisson-Boltzmann; il potenziale sulla superficie colora i residui.
        </Empty>
      )}
    </div>
  );
}

/** Vista di un job `pockets` o `electrostatics` dal suo link (/quality?pjob=, ?ejob=), senza un'analisi di qualità. */
export function ToolJobCard({ kind, jobId }: { kind: ToolKind; jobId: string }) {
  const { data: job, error } = useAsync(() => api.job(jobId), [jobId]);
  const source = job?.kind === kind ? ((job.params?.source ?? null) as StructureSource | null) : null;
  const title = kind === "pockets" ? "Tasche" : "Elettrostatica";
  return (
    <Card icon={kind === "pockets" ? "search" : "activity"} title={`${title}${job ? ` · ${job.title.replace(/^[^·]+· /, "")}` : ""}`}>
      {error ? <Alert tone="err">Calcolo non trovato: {error.message}</Alert>
        : source ? (kind === "pockets"
          ? <PocketsPanel source={source} url={sourceFileUrl(source)} initialJob={jobId} />
          : <ElectrostaticsPanel source={source} url={sourceFileUrl(source)} initialJob={jobId} />)
          : job ? <Alert tone="warn">Il job {jobId} non è di questo tipo.</Alert> : null}
    </Card>
  );
}
