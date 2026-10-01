// Tasche (fpocket) ed elettrostatica (PDB2PQR + APBS) di una struttura (F7b): schede "Tasche" ed "Elettrostatica"
// della pagina Qualità e vista di un job dal suo link (/quality?pjob=, /quality?ejob=). Ogni scheda mostra il calcolo
// più recente sulla struttura (`/api/<tipo>/latest`) e lo lancia o rilancia; la logica è in lib/pockets.ts,
// lib/electrostatics.ts e lib/toolJobs.ts.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { ElectrostaticsResidue, ElectrostaticsResult, Pocket, PocketsResult, StructureSource, SystemInfo } from "../api/types";
import { encodeSource } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { chargeBreakdown, chargeText, EXTREME_MIN_POINTS, extremeResidues, potentialColoring, potentialStripRows, potentialText } from "../lib/electrostatics";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { followedAnalysis } from "../lib/interactions";
import { DRUGGABLE, druggabilityLabel, isDruggable, pocketColor, pocketColoring, pocketHighlight, pocketOfResidue, pocketSpheres, residueList } from "../lib/pockets";
import { resiNumber, type ResidueRef } from "../lib/residueColoring";
import { missingTools, missingToolsText, rememberedJob, type ToolJobMemory } from "../lib/toolJobs";
import { ResidueStrip } from "./charts";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { sourceFileUrl } from "./StructuralAnalysis";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, BarCell, Card, Empty, Field, Stat } from "./ui";

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, initialJob]);
  const result = job?.status === "completed" ? (job.result as R) : null;
  const running = !!summary && ["queued", "running", "cancelling"].includes(summary.status);
  const missing = missingToolsText(missingTools(info, TOOLS[kind].keys), TOOLS[kind].purpose);
  return { jobId, setJobId, job, summary, cancel, result, running, missing };
}

// ---------------------------------------------------------------------------------- tasche
function PocketTable({ pockets, selected, onPick }: { pockets: Pocket[]; selected: number | null; onPick: (p: Pocket) => void }) {
  return (
    <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
      <table className="table">
        <thead>
          <tr>
            <th>#</th><th>Tasca</th><th title={`Schmidtke & Barril 2010: ≥ ${DRUGGABLE} probabilmente druggabile`}>Druggability</th>
            <th className="num" title="Punteggio di fpocket (ordine delle tasche del programma)">Punteggio</th>
            <th className="num">Volume (Å³)</th><th className="num">Sfere α</th><th className="num" title="Punteggio di idrofobicità di fpocket">Idrofob.</th>
            <th>Residui</th>
          </tr>
        </thead>
        <tbody>
          {pockets.map((p) => (
            <tr key={p.id} className={`clickable${p.id === selected ? " selected" : ""}`} onClick={() => onPick(p)} title="Evidenzia la tasca nel 3D">
              <td className="nowrap"><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: pocketColor(p.rank), marginRight: 6 }} />{p.rank}</td>
              <td className="nowrap">Tasca {p.id} {isDruggable(p) && <Badge tone="ok">druggabile</Badge>}</td>
              <td style={{ minWidth: 140 }}><BarCell value={(p.druggability ?? 0) * 100} tone={isDruggable(p) ? "var(--ok)" : "var(--text-2)"} label={fmtNum(p.druggability ?? null, 3)} /></td>
              <td className="num">{fmtNum(p.score ?? null, 3)}</td>
              <td className="num">{fmtNum(p.volume ?? null, 0)}</td>
              <td className="num">{p.alpha_spheres ?? "—"}</td>
              <td className="num">{fmtNum(p.hydrophobicity_score ?? null, 1)}</td>
              <td className="small" style={{ minWidth: 280 }}>{p.residues.length} · {residueList(p, 8)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Risultato di fpocket: 3D con le tasche colorate, sfere alfa e centri; tabella ordinata per druggability. */
export function PocketsView({ result, jobId, url }: { result: PocketsResult; jobId: string; url: string | null }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [focus, setFocus] = useState<Focus>([]);
  const [centerKey, setCenterKey] = useState(0);
  const coloring = useMemo(() => pocketColoring(result, selected), [result, selected]);
  const spheres = useMemo(() => pocketSpheres(result, selected), [result, selected]);
  const s = result.summary;
  const chosen = result.pockets.find((p) => p.id === selected) ?? null;
  const pick = (p: Pocket) => {
    setSelected(p.id);
    setFocus(pocketHighlight(p));
    setCenterKey((k) => k + 1);
  };
  /** Dal 3D: la tasca del residuo (la prima per druggability), senza spostare la vista. */
  const pick3d = (ref: ResidueRef) => {
    const p = pocketOfResidue(result, ref);
    if (p) {
      setSelected(p.id);
      setFocus(pocketHighlight(p));
    } else setFocus([{ chain: ref.chain, resi: resiNumber(ref.resnum) }]);
  };
  return (
    <div className="stack">
      {result.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Tasche" value={s.count} note={`fpocket, ${result.params.options}`} />
        <Stat label="Probabilmente druggabili" value={s.druggable} note={`druggability ≥ ${fmtNum(s.threshold, 1)} (Schmidtke & Barril 2010)`} />
        <Stat label="Druggability migliore" value={s.best ? fmtNum(s.best.druggability, 3) : "—"} note={s.best ? `tasca ${s.best.id} · ${s.best.residues} residui` : undefined} />
        <Stat label="Volume della migliore" value={s.best?.volume != null ? <>{fmtNum(s.best.volume, 0)}<small> Å³</small></> : "—"} note="stima Monte Carlo di fpocket" />
      </div>
      {!result.pockets.length ? <Alert tone="info">fpocket non ha trovato tasche con almeno 15 sfere alfa.</Alert> : (
        <>
          {url ? (
            <div className="stack-sm">
              <div className="row small">
                {selected != null && <button className="btn btn-sm" onClick={() => { setSelected(null); setFocus([]); }}>Mostra tutte le tasche</button>}
                <span className="muted">
                  {chosen ? `Tasca ${chosen.id}: ${druggabilityLabel(chosen)}, residui a bastoncini arancioni.` : `Le prime ${Math.min(8, result.pockets.length)} tasche colorate per druggability.`}
                  {" "}Sferette: centri delle sfere alfa; sfera bianca: centro della tasca. Clic su un residuo: la sua tasca.
                </span>
              </div>
              <Viewer3D url={url} format={url.endsWith(".cif") ? "cif" : "pdb"} coloring={coloring} spheres={spheres} highlight={focus} centerKey={centerKey} onPickResidue={pick3d} height={400} />
            </div>
          ) : <p className="small muted">Struttura non disponibile per la visualizzazione 3D.</p>}
          <PocketTable pockets={result.pockets} selected={selected} onPick={pick} />
          {chosen && (
            <div className="small text-2">
              <b>Tasca {chosen.id}</b>: {chosen.residues.length} residui ({residueList(chosen, 40)}); SASA {fmtNum(chosen.sasa_total ?? null, 0)} Å² di cui polare {fmtNum(chosen.sasa_polar ?? null, 0)} Å²; polarità {chosen.polarity_score ?? "—"}, carica {chosen.charge_score ?? "—"}, atomi polari {fmtNum(chosen.polar_atoms_pct ?? null, 0)}%.
            </div>
          )}
        </>
      )}
      <div className="row small" style={{ gap: 8 }}>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.pdb, true)} title="PDB di fpocket: proteina e sfere alfa (residui STP)"><Icon name="download" size={13} /> PDB con le sfere</a>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, "structure_out/structure_info.txt", true)}><Icon name="download" size={13} /> Descrittori (info.txt)</a>
      </div>
      <details className="small text-2">
        <summary>Metodo e fonti</summary>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          <li><b>fpocket 4</b> (Le Guilloux, Schmidtke & Tufféry, BMC Bioinformatics 10:168, 2009): sfere alfa (vuote, tangenti a 4 atomi) con raggio 3,4–6,2 Å raggruppate in tasche di almeno 15 sfere; parametri predefiniti. Volume stimato con Monte Carlo (piccole differenze fra esecuzioni).</li>
          <li><b>Druggability</b> 0–1 (Schmidtke & Barril, J. Med. Chem. 53:5858, 2010): regressione logistica sui descrittori della tasca; ≥ 0,5 probabilmente in grado di legare una piccola molecola simile a un farmaco. È un indicatore statistico, non una previsione di affinità.</li>
          <li>Il calcolo usa una copia della struttura con la sola proteina: ligandi, ioni e acque sono esclusi, quindi i siti che occupano compaiono come tasche vuote.</li>
        </ul>
      </details>
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
      <div className="row small" style={{ gap: 10 }}>
        <button className="btn btn-primary btn-sm" disabled={t.running || !!t.missing} onClick={run}>
          <Icon name="play" size={13} /> {t.result ? "Ricalcola le tasche" : "Cerca le tasche (fpocket)"}
        </button>
        {t.result && t.job && <span className="muted">Calcolate {relTime(t.job.finished_at)}.</span>}
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
      {!rows.length ? <p className="small muted">Nessuno.</p> : (
        <div className="row" style={{ gap: 4 }}>
          {rows.map((r) => (
            <button key={`${r.chain}${r.resnum}`} className="chip" onClick={() => onPick(r)} title={potentialText(r.potential)}>
              {r.resname} {r.chain}{r.resnum} <span className="muted">{fmtNum(r.potential, 1)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Risultato del calcolo elettrostatico: 3D e striscia colorati per potenziale, residui estremi, carica, file. */
export function ElectrostaticsView({ result, jobId, url }: { result: ElectrostaticsResult; jobId: string; url: string | null }) {
  const [focus, setFocus] = useState<Focus>([]);
  const [selected, setSelected] = useState<ResidueRef | null>(null);
  const [centerKey, setCenterKey] = useState(0);
  const coloring = useMemo(() => potentialColoring(result), [result]);
  const rows = useMemo(() => potentialStripRows(result), [result]);
  const extremes = useMemo(() => extremeResidues(result), [result]);
  const s = result.summary;
  const p = result.parameters;
  const pick = (r: ResidueRef) => {
    setSelected({ chain: r.chain, resnum: r.resnum });
    setFocus([{ chain: r.chain, resi: resiNumber(r.resnum) }]);
    setCenterKey((k) => k + 1);
  };
  return (
    <div className="stack">
      {result.warnings.map((w, k) => <Alert key={k} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label={`Carica netta a pH ${result.ph.toLocaleString("it-IT")}`} value={chargeText(result.net_charge)} note={chargeBreakdown(result)} />
        <Stat label="Potenziale di superficie" value={<span style={{ fontSize: 18 }}>{fmtNum(s.min, 1)} … {fmtNum(s.max, 1)}<small> kT/e</small></span>} note={`media ${fmtNum(s.mean, 2)} · ${s.residues} residui esposti`} />
        <Stat label="Residui marcati" value={<>{s.positive}<small> +</small> / {s.negative}<small> −</small></>} note={`|potenziale| > ${fmtNum(s.threshold, 0)} kT/e`} />
        <Stat label="Griglia di APBS" value={<span style={{ fontSize: 18 }}>{result.grid.dime.join(" × ")}</span>} note={`passo ${result.grid.spacing.map((v) => fmtNum(v, 2)).join("/")} Å · ${fmtNum(result.grid.memory_mb, 0)} MB stimati`} />
      </div>
      {url && coloring ? (
        <div className="stack-sm">
          <div className="row small">
            <button className="btn btn-sm btn-ghost" onClick={() => { setFocus([]); setSelected(null); }}>Nessuna evidenza</button>
            <span className="muted">Rosso negativo, blu positivo, saturato a ±5 kT/e; grigio: residui sepolti. Clic su un residuo per evidenziarlo.</span>
          </div>
          <Viewer3D url={url} format={url.endsWith(".cif") ? "cif" : "pdb"} coloring={coloring} highlight={focus} centerKey={centerKey} onPickResidue={(r) => { setSelected(r); setFocus([{ chain: r.chain, resi: resiNumber(r.resnum) }]); }} height={400} />
          <ResidueStrip residues={rows} coloring={coloring} selected={selected} onSelect={pick} />
        </div>
      ) : <p className="small muted">Struttura non disponibile per la visualizzazione 3D.</p>}
      <div className="grid grid-2">
        <ExtremeList title="Più positivi" rows={extremes.positive} onPick={pick} />
        <ExtremeList title="Più negativi" rows={extremes.negative} onPick={pick} />
      </div>
      <p className="tiny muted">Fra i residui più carichi solo quelli con almeno {EXTREME_MIN_POINTS} punti di superficie: il valore di un residuo quasi sepolto è poco stabile (resta comunque nel 3D e nella striscia).</p>
      <div className="row small" style={{ gap: 8 }}>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.map.file, true)} title="Mappa OpenDX del potenziale (kT/e): PyMOL, Chimera, VMD"><Icon name="download" size={13} /> Mappa del potenziale (.dx)</a>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, "structure.pqr", true)} title="Cariche e raggi di PDB2PQR"><Icon name="download" size={13} /> PQR</a>
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, "apbs.in", true)} title="Input di APBS per ripetere il calcolo"><Icon name="download" size={13} /> Input APBS</a>
      </div>
      <details className="small text-2">
        <summary>Parametri e fonti</summary>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          <li><b>PDB2PQR 3</b> (Jurrus et al., Protein Sci. 27:112, 2018) con campo di forza <b>{result.forcefield}</b>{result.forcefield === "PARSE" ? " (Sitkoff, Sharp & Honig 1994: parametrizzato per Poisson-Boltzmann)" : " (AMBER ff99 di PDB2PQR)"}; protonazione a pH {result.ph.toLocaleString("it-IT")} con <b>PROPKA 3</b> (Olsson et al. 2011). Carica dei residui: somma delle cariche atomiche, estremità della catena comprese nel primo e nell'ultimo residuo.</li>
          <li><b>APBS</b> (Baker et al., PNAS 98:10037, 2001): Poisson-Boltzmann linearizzata, mg-auto con griglie dimensionate come psize.py; dielettrico {String(p.pdie)} (proteina) e {String(p.sdie)} (solvente), forza ionica {fmtNum(Number(p.ionic_strength) * 1000, 0)} mM (ioni ±1 di raggio {String(p.ion_radius)} Å), {String(p.temperature)} K, superficie molecolare liscia con sonda {String(p.srad)} Å.</li>
          <li><b>Potenziale per residuo</b>: interpolazione della mappa sui punti della superficie accessibile al solvente (sonda {fmtNum(result.sampling.probe, 1)} Å, {result.sampling.points_per_atom} punti per atomo, Shrake & Rupley 1973), media per atomo e poi sugli atomi esposti del residuo. 1 kT/e = {fmtNum(result.scale.mv_per_unit, 2)} mV a 298,15 K.</li>
        </ul>
      </details>
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
      <div className="row small" style={{ gap: 10, alignItems: "end" }}>
        <Field label="Campo di forza">
          <select className="input" style={{ width: 230 }} value={forcefield} onChange={(e) => setForcefield(e.target.value as "PARSE" | "AMBER")}>
            <option value="PARSE">PARSE (per Poisson-Boltzmann)</option>
            <option value="AMBER">AMBER</option>
          </select>
        </Field>
        <Field label="pH (PROPKA)">
          <input className="input" style={{ width: 90 }} type="number" min={0} max={14} step={0.1} value={ph} onChange={(e) => setPh(Number(e.target.value))} />
        </Field>
        <button className="btn btn-primary btn-sm" disabled={t.running || !!t.missing || !!phError} onClick={run}>
          <Icon name="play" size={13} /> {t.result ? "Ricalcola" : "Calcola il potenziale (APBS)"}
        </button>
        {phError && <span className="muted">{phError}</span>}
        {t.result && t.job && <span className="muted">Calcolato {relTime(t.job.finished_at)} ({t.result.forcefield}, pH {t.result.ph.toLocaleString("it-IT")}).</span>}
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
    <Card title={`${title}${job ? ` · ${job.title.replace(/^[^·]+· /, "")}` : ""}`}>
      {error ? <Alert tone="err">Calcolo non trovato: {error.message}</Alert>
        : source ? (kind === "pockets"
          ? <PocketsPanel source={source} url={sourceFileUrl(source)} initialJob={jobId} />
          : <ElectrostaticsPanel source={source} url={sourceFileUrl(source)} initialJob={jobId} />)
          : job ? <Alert tone="warn">Il job {jobId} non è di questo tipo.</Alert> : null}
    </Card>
  );
}
