import { useEffect, useMemo, useRef, useState, type MouseEvent as RMouseEvent } from "react";
import { api } from "../api/client";
import type { AlignResult, StructuralPair } from "../api/types";
import { colorFor } from "../lib/colorscale";
import {
  breaksBefore, comparisonColoring, comparisonOutdated, deviatingSegments, DISTANCE_MAX, DISTANCE_SCALE, pairHighlight, pairIndexOf, pairRows,
  rowScrollDelta, seriesPath, tmVerdict,
  type CompareColorBy, type DeviatingSegment, type PairSort,
} from "../lib/compare";
import { fmtNum } from "../lib/format";
import type { ResidueRef } from "../lib/residueColoring";
import { Viewer3D } from "./Viewer3D";
import { Alert, Card, Segmented, Stat } from "./ui";

/** Metriche globali del confronto (A = riferimento). */
function Metrics({ result }: { result: AlignResult }) {
  const [la, lb] = result.lengths ?? [];
  const gdt = result.gdt ?? [];
  return (
    <div className="stack-sm">
      <div className="grid grid-4">
        <Stat label="TM-score (su A)" value={fmtNum(result.tm_score, 3)} note={tmVerdict(result.tm_score)} />
        <Stat label="TM-score (su B)" value={fmtNum(result.tm_score_b, 3)} note={lb ? `normalizzato sui ${lb} residui di B` : "normalizzato sulla lunghezza di B"} />
        <Stat label="RMSD" value={<>{fmtNum(result.rmsd, 2)}<small> Å</small></>} note={`${result.aligned_length ?? "—"} Cα allineati, dopo la loro sovrapposizione`} />
        <Stat label="RMSD entro 5 Å" value={result.rmsd_close != null ? <>{fmtNum(result.rmsd_close, 2)}<small> Å</small></> : "—"} note={`${result.close_pairs ?? "—"} coppie a meno di 5 Å`} />
      </div>
      {result.gdt_ts != null && (
        <div className="grid grid-4">
          <Stat label="GDT-TS" value={fmtNum(result.gdt_ts, 1)} note="media di P1, P2, P4, P8 (% dei Cα di A)" />
          <Stat label="GDT-HA" value={fmtNum(result.gdt_ha, 1)} note="media di P0,5, P1, P2, P4" />
          <Stat label="lDDT" value={fmtNum(result.lddt ?? null, 3)} note="Cα, raggio 15 Å su A, senza sovrapposizione" />
          <Stat label="Lunghezza allineata" value={result.pairs?.length ?? result.aligned_length ?? "—"} note={la ? `A ${la} · B ${lb} residui${result.stats ? ` · identità ${fmtNum(result.stats.identity)}%` : ""}` : undefined} />
        </div>
      )}
      {gdt.length > 0 && (
        <div className="row small text-2" style={{ gap: 16 }}>
          <span className="muted">Cα di A entro la soglia (sovrapposizione scelta per ogni soglia):</span>
          {gdt.map((g) => <span key={g.cutoff}><b>P{fmtNum(g.cutoff, g.cutoff < 1 ? 1 : 0)}</b> {fmtNum(g.percent, 1)}% <span className="muted">({g.count})</span></span>)}
        </div>
      )}
    </div>
  );
}

/** Distanza e lDDT per posizione (una colonna per coppia, nell'ordine di A); clic = seleziona la coppia. */
function ComparisonProfile({ pairs, selected, onSelect }: { pairs: StructuralPair[]; selected: number | null; onSelect: (i: number) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const breaks = useMemo(() => breaksBefore(pairs), [pairs]);
  const W = 900;
  const pad = { l: 46, r: 12 };
  const top = { t: 14, h: 120 };
  const low = { t: 158, h: 70 };
  const H = low.t + low.h + 22;
  const n = pairs.length;
  const x = (i: number) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
  const dMax = Math.max(DISTANCE_MAX, ...pairs.map((p) => p.distance));
  const yd = (v: number) => top.t + (1 - v / dMax) * top.h;
  const yl = (v: number) => low.t + (1 - v) * low.h;
  const dPath = useMemo(() => seriesPath(pairs.map((p) => p.distance), breaks, x, yd), [pairs, breaks, dMax]); // eslint-disable-line react-hooks/exhaustive-deps
  const lPath = useMemo(() => seriesPath(pairs.map((p) => p.lddt), breaks, x, yl), [pairs, breaks]); // eslint-disable-line react-hooks/exhaustive-deps
  const at = (e: RMouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1));
    return i >= 0 && i < n ? i : null;
  };
  const shown = hover ?? selected;
  const p = shown !== null ? pairs[shown] : null;
  return (
    <div className="stack-sm">
      <div className="row small" style={{ minHeight: 20 }}>
        {p ? (
          <span>
            <b>{p.aa_a}{p.resnum_a}</b> di A ↔ <b>{p.aa_b}{p.resnum_b}</b> di B · distanza <b>{fmtNum(p.distance, 2)} Å</b> · lDDT <b>{fmtNum(p.lddt, 2)}</b>
          </span>
        ) : <span className="muted">Passa sul grafico per i valori; clic per evidenziare la coppia nel 3D e nella tabella. Le interruzioni sono residui di A senza coppia.</span>}
      </div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} style={{ cursor: "pointer" }} role="img" aria-label="Distanza e lDDT per posizione"
        onMouseMove={(e) => setHover(at(e))} onMouseLeave={() => setHover(null)} onClick={(e) => { const i = at(e); if (i !== null) onSelect(i); }}>
        <text x={pad.l} y={10}>Distanza Cα (Å)</text>
        {[0, 2, DISTANCE_MAX, ...(dMax > DISTANCE_MAX + 1 ? [Math.round(dMax)] : [])].map((v) => (
          <g key={`d${v}`}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={yd(v)} y2={yd(v)} />
            <text x={pad.l - 6} y={yd(v) + 3} textAnchor="end">{v}</text>
          </g>
        ))}
        <line x1={pad.l} x2={W - pad.r} y1={yd(DISTANCE_MAX)} y2={yd(DISTANCE_MAX)} stroke="var(--warn)" strokeDasharray="5 4" />
        <text x={W - pad.r} y={yd(DISTANCE_MAX) - 3} textAnchor="end" style={{ fill: "var(--warn)" }}>5 Å</text>
        {pairs.map((q, i) => <circle key={i} cx={x(i)} cy={yd(q.distance)} r={n > 300 ? 1.2 : 2} fill={colorFor(DISTANCE_SCALE, q.distance)} />)}
        <path d={dPath} fill="none" stroke="var(--accent)" strokeWidth={1.2} opacity={0.6} />
        <text x={pad.l} y={low.t - 6}>lDDT per residuo di A</text>
        {[0, 0.5, 1].map((v) => (
          <g key={`l${v}`}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={yl(v)} y2={yl(v)} />
            <text x={pad.l - 6} y={yl(v) + 3} textAnchor="end">{fmtNum(v, v === 0.5 ? 1 : 0)}</text>
          </g>
        ))}
        <path d={lPath} fill="none" stroke="var(--ok)" strokeWidth={1.6} />
        {Array.from({ length: Math.min(10, n) }, (_, k) => {
          const i = Math.round((k / Math.max(1, Math.min(10, n) - 1)) * (n - 1));
          return <text key={k} x={x(i)} y={H - 4} textAnchor="middle">{pairs[i]?.resnum_a}</text>;
        })}
        {selected !== null && <line x1={x(selected)} x2={x(selected)} y1={top.t} y2={low.t + low.h} stroke="var(--warn)" strokeWidth={2} />}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={top.t} y2={low.t + low.h} stroke="var(--text-2)" strokeDasharray="3 3" />}
      </svg>
    </div>
  );
}

const THRESHOLDS = [1, 2, 4];
const MIN_DISTANCES = [0, 1, 2, DISTANCE_MAX];

/** Tabella per coppia allineata: ordinamento, filtro per distanza, clic = evidenzia nel 3D. */
function PairTable({ pairs, selected, onSelect }: { pairs: StructuralPair[]; selected: number | null; onSelect: (i: number) => void }) {
  const [sort, setSort] = useState<PairSort>("position");
  const [minDistance, setMinDistance] = useState(0);
  const rows = useMemo(() => pairRows(pairs, sort, minDistance), [pairs, sort, minDistance]);
  const selRow = useRef<HTMLTableRowElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // solo il contenitore della tabella: una selezione dal 3D, dal grafico o dai tratti non sposta la pagina
    const box = wrap.current;
    const row = selRow.current;
    if (!box || !row) return;
    const header = box.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    const delta = rowScrollDelta(row.getBoundingClientRect(), box.getBoundingClientRect(), header);
    if (delta) box.scrollTop += delta;
  }, [selected]);
  const head = (key: PairSort, label: string, num = false) => (
    <th className={`sortable${num ? " num" : ""}`} onClick={() => setSort(key)} title="Ordina">{label} {sort === key ? "▼" : ""}</th>
  );
  return (
    <div className="stack-sm">
      <div className="row small">
        <label className="check">
          Mostra
          <select className="input" style={{ width: 170, minHeight: 28, padding: "2px 6px" }} value={minDistance} onChange={(e) => setMinDistance(Number(e.target.value))}>
            {MIN_DISTANCES.map((d) => <option key={d} value={d}>{d ? `solo coppie ≥ ${d} Å` : `tutte le coppie (${pairs.length})`}</option>)}
          </select>
        </label>
        <span className="muted">{rows.length} righe · clic su una riga per evidenziare i due residui nel 3D.</span>
      </div>
      <div ref={wrap} className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
        <table className="table">
          <thead><tr>{head("position", "Posizione")}<th>Residuo di A</th><th>Residuo di B</th>{head("distance", "Distanza (Å)", true)}{head("lddt", "lDDT", true)}</tr></thead>
          <tbody>
            {rows.map((i) => {
              const p = pairs[i];
              const sel = i === selected;
              return (
                <tr key={i} ref={sel ? selRow : undefined} className={`clickable${sel ? " selected" : ""}`} onClick={() => onSelect(i)}>
                  <td className="muted">{i + 1}</td>
                  <td className="nowrap"><b>{p.aa_a}</b> {p.chain_a}{p.resnum_a}</td>
                  <td className="nowrap"><b>{p.aa_b}</b> {p.chain_b}{p.resnum_b}{p.aa_a !== p.aa_b && <span className="tiny muted"> (diverso)</span>}</td>
                  <td className="num nowrap"><i style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2, marginRight: 6, background: colorFor(DISTANCE_SCALE, p.distance) }} />{fmtNum(p.distance, 2)}</td>
                  <td className="num">{fmtNum(p.lddt, 2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Confronto strutturale di un job `align` strutturale: metriche, viewer sovrapposto (B colorata per distanza o lDDT, A in
 * grigio chiaro), grafico per posizione, tratti che deviano e tabella per residuo, con selezione nei due sensi.
 */
export function StructuralComparison({ jobId, result }: { jobId: string; result: AlignResult }) {
  const pairs = useMemo(() => result.pairs ?? [], [result.pairs]);
  const [by, setBy] = useState<CompareColorBy>("distance");
  const [selected, setSelected] = useState<number | null>(null);
  const [focus, setFocus] = useState<{ chain: string; resi: number }[]>([]);
  const [centerKey, setCenterKey] = useState(0);
  const [threshold, setThreshold] = useState(2);
  const coloring = useMemo(() => (pairs.length ? comparisonColoring(pairs, by) : null), [pairs, by]);
  const segments = useMemo(() => deviatingSegments(pairs, threshold), [pairs, threshold]);

  const select = (i: number, center = true) => {
    setSelected(i);
    setFocus(pairHighlight(pairs[i]));
    if (center) setCenterKey((k) => k + 1);
  };
  const pick3d = (ref: ResidueRef) => {
    const i = pairIndexOf(pairs, ref);
    if (i >= 0) select(i, false);
  };
  const pickSegment = (s: DeviatingSegment) => {
    setSelected(s.start);
    setFocus(pairs.slice(s.start, s.end + 1).flatMap(pairHighlight));
    setCenterKey((k) => k + 1);
  };
  const [idA, idB] = result.ids;
  return (
    <div className="stack">
      <Metrics result={result} />
      {result.note && <Alert tone="info">{result.note}</Alert>}
      {comparisonOutdated(result) && (
        <Alert tone="info">
          Confronto per residuo non disponibile: risultato di una versione precedente di BioLoom. Rilancia l'allineamento
          (i risultati precedenti in cache vengono ricalcolati) per distanze, GDT e lDDT.
        </Alert>
      )}
      {result.superposed_file && (
        <Card
          title="Sovrapposizione 3D"
          sub={pairs.length ? `A = ${idA} (grigio chiaro), B = ${idB} colorata per ${by === "distance" ? "distanza dal residuo corrispondente" : "lDDT"}` : `${idA} (catena A) e ${idB} (catena B)`}
          right={pairs.length > 0 && <Segmented value={by} onChange={setBy} options={[{ value: "distance", label: "Distanza" }, { value: "lddt", label: "lDDT" }]} />}
        >
          <div className="stack-sm">
            <Viewer3D url={api.jobFileUrl(jobId, result.superposed_file)} scheme="chain" coloring={coloring} highlight={focus} centerKey={centerKey}
              onPickResidue={pairs.length ? pick3d : undefined} />
            {pairs.length > 0 && (
              <div className="row small">
                <button className="btn btn-sm btn-ghost" onClick={() => { setFocus([]); setSelected(null); }}>Nessuna evidenza</button>
                <span className="muted">Coppie evidenziate a bastoncini arancioni · clic su un residuo per selezionare la sua coppia.</span>
              </div>
            )}
          </div>
        </Card>
      )}
      {pairs.length > 0 && (
        <>
          <Card title="Deviazione per posizione" sub="distanza dopo la sovrapposizione del TM-score e lDDT (indipendente dalla sovrapposizione)">
            <div className="stack">
              <ComparisonProfile pairs={pairs} selected={selected} onSelect={(i) => select(i)} />
              <div className="stack-sm">
                <div className="row small">
                  <b>Tratti che deviano</b>
                  <label className="check">
                    almeno 3 coppie consecutive oltre
                    <select className="input" style={{ width: 80, minHeight: 28, padding: "2px 6px" }} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))}>
                      {THRESHOLDS.map((t) => <option key={t} value={t}>{t} Å</option>)}
                    </select>
                  </label>
                </div>
                {segments.length ? (
                  <div className="row">
                    {segments.map((s) => <button key={s.start} className="chip" onClick={() => pickSegment(s)} title="Evidenzia nel 3D">{s.text}</button>)}
                  </div>
                ) : <p className="small muted">Nessun tratto di almeno 3 coppie consecutive oltre {threshold} Å.</p>}
              </div>
            </div>
          </Card>
          <Card title="Coppie allineate" sub={`${pairs.length} coppie · numerazione dei file originali`}>
            <PairTable pairs={pairs} selected={selected} onSelect={(i) => select(i)} />
          </Card>
        </>
      )}
    </div>
  );
}
