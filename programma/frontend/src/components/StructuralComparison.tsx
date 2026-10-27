import { useEffect, useId, useMemo, useState, type MouseEvent as RMouseEvent } from "react";
import { api } from "../api/client";
import type { AlignResult, StructuralPair } from "../api/types";
import { colorFor, legendFor } from "../lib/colorscale";
import {
  breaksBefore, comparisonColoring, comparisonOutdated, deviatingSegments, DISTANCE_MAX, DISTANCE_SCALE, pairHighlight, pairIndexOf, pairRows,
  seriesPath, tmVerdict,
  type CompareColorBy, type DeviatingSegment, type PairSort,
} from "../lib/compare";
import {
  distanceReading, gdtTone, lddtReading, lddtTone, numberingText, pairIdentity, pairResidue, pairSpan, percentOf, residueChange,
  residueText, rmsdTone, segmentDetail, tmReading, tmTone,
} from "../lib/compareMetrics";
import type { ChartData } from "../lib/exportImage";
import { fmtInt, fmtNum, fmtUnit } from "../lib/format";
import type { ResidueRef } from "../lib/residueColoring";
import { TABLE_ROW_HEIGHT, useVirtualRows } from "../lib/virtual";
import { niceTicks, svgPoint, useDomain, useZoomAxis, visibleIndices, type DomainState } from "../lib/zoom";
import { ChartFrame, ZoomBar } from "./ChartFrame";
import { inView, RefLine, residueTicks, useChartWidth, useFollowSelection, useTooltip, ZOOM_HINT } from "./charts";
import { Icon } from "./Icon";
import { MetricNotes } from "./MetricNotes";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, BarCell, Card, DetailGrid, DetailPanel, Disclosure, rowActivate, Segmented, Stat } from "./ui";
import "../styles/compare.css";

/** Soglie del GDT-TS e del GDT-HA (Å). */
const GDT_TS = [1, 2, 4, 8];
const GDT_HA = [0.5, 1, 2, 4];

const cutoffText = (c: number) => fmtNum(c, c < 1 ? 1 : 0);

/** Metriche globali del confronto (A = riferimento): ogni valore con la spiegazione, le soglie e i valori grezzi. */
function Metrics({ result }: { result: AlignResult }) {
  const [la, lb] = result.lengths ?? [];
  const pairs = result.pairs ?? [];
  const gdt = result.gdt ?? [];
  const aligned = pairs.length || result.aligned_length;
  const identity = result.stats?.identity ?? pairIdentity(pairs);
  const closeShare = percentOf(result.close_pairs, result.aligned_length);
  const gdtValues = (cutoffs: number[]) => gdt.filter((g) => cutoffs.includes(g.cutoff))
    .map((g) => ({ label: `P${cutoffText(g.cutoff)} (entro ${cutoffText(g.cutoff)} Å)`, value: `${fmtNum(g.percent, 1)}% · ${g.count} Cα` }));
  const [idA, idB] = result.ids;
  return (
    <Card title="Metriche del confronto" icon="activity" sub={`A = ${idA} (riferimento) · B = ${idB}`}
      right={result.tm_score != null && <Badge tone={tmTone(result.tm_score)}>{tmVerdict(result.tm_score)}</Badge>}>
      <div className="stack-sm">
        <div className="grid grid-4">
          <Stat label="TM-score (su A)" value={fmtNum(result.tm_score, 3)} tone={tmTone(result.tm_score)} verdict={tmReading(result.tm_score)}
            note={la ? `normalizzato sui ${la} residui di A` : "normalizzato sulla lunghezza di A"}
            detail={<MetricNotes metric="tm" values={[
              { label: "Valore", value: fmtNum(result.tm_score, 4) },
              { label: "Lettura", value: tmVerdict(result.tm_score) },
            ]} />} />
          <Stat label="TM-score (su B)" value={fmtNum(result.tm_score_b, 3)} tone={tmTone(result.tm_score_b)} verdict={tmReading(result.tm_score_b)}
            note={lb ? `normalizzato sui ${lb} residui di B` : "normalizzato sulla lunghezza di B"}
            detail={(
              <>
                <p>Lo stesso TM-score diviso per la lunghezza di B: è il più alto dei due quando B è più corta (un dominio o un frammento di A). Soglie come su A: oltre 0,5 stesso ripiegamento.</p>
                <DetailGrid items={[{ label: "Valore", value: fmtNum(result.tm_score_b, 4) }, { label: "Residui di B", value: lb ?? "—" }]} />
              </>
            )} />
          <Stat label="RMSD" value={<>{fmtNum(result.rmsd, 2)}<small> Å</small></>} tone={rmsdTone(result.rmsd)}
            note={`${result.aligned_length ?? "—"} Cα allineati, dopo la loro sovrapposizione`}
            detail={<MetricNotes metric="rmsd" values={[
              { label: "Valore", value: fmtUnit(result.rmsd, 3, "Å") },
              { label: "Cα allineati", value: result.aligned_length ?? "—" },
            ]} />} />
          <Stat label="RMSD entro 5 Å" value={result.rmsd_close != null ? <>{fmtNum(result.rmsd_close, 2)}<small> Å</small></> : "—"}
            note={`${result.close_pairs ?? "—"} coppie a meno di 5 Å`}
            detail={(
              <>
                <p>RMSD delle sole coppie a meno di {DISTANCE_MAX} Å dopo la sovrapposizione del TM-score: misura il nucleo comune senza i tratti che divergono. Molto più basso dell'RMSD globale = poche regioni lontane.</p>
                <DetailGrid items={[
                  { label: "Valore", value: fmtUnit(result.rmsd_close, 3, "Å") },
                  { label: "Coppie vicine", value: `${result.close_pairs ?? "—"}${closeShare !== null ? ` (${fmtNum(closeShare, 0)}% delle allineate)` : ""}` },
                ]} />
              </>
            )} />
        </div>
        {result.gdt_ts != null && (
          <div className="grid grid-4">
            <Stat label="GDT-TS" value={fmtNum(result.gdt_ts, 1)} note="media di P1, P2, P4, P8 (% dei Cα di A)" tone={gdtTone(result.gdt_ts)}
              detail={<MetricNotes metric="gdt" values={gdtValues(GDT_TS)} />} />
            <Stat label="GDT-HA" value={fmtNum(result.gdt_ha, 1)} note="media di P0,5, P1, P2, P4"
              detail={(
                <>
                  <p>Come il GDT-TS con soglie dimezzate (0,5, 1, 2, 4 Å): misura l'accuratezza fine ed è sempre più basso del GDT-TS.</p>
                  <DetailGrid items={gdtValues(GDT_HA)} />
                </>
              )} />
            <Stat label="lDDT" value={fmtNum(result.lddt ?? null, 3)} note="Cα, raggio 15 Å su A, senza sovrapposizione" tone={lddtTone(result.lddt)}
              detail={<MetricNotes metric="lddt" values={[
                { label: "Valore", value: fmtNum(result.lddt ?? null, 4) },
                { label: "Distanze valutate", value: fmtInt(result.lddt_distances) },
              ]} />} />
            <Stat label="Lunghezza allineata" value={aligned ?? "—"}
              note={la ? `A ${la} · B ${lb} residui${result.stats ? ` · identità ${fmtNum(result.stats.identity)}%` : ""}` : undefined}
              detail={(
                <>
                  <p>Coppie di residui che l'allineamento strutturale mette in corrispondenza; la copertura dice quanta parte di ciascuna struttura entra nel confronto.</p>
                  <DetailGrid items={[
                    { label: "Residui di A", value: la ?? "—" },
                    { label: "Residui di B", value: lb ?? "—" },
                    { label: "Copertura di A", value: fmtUnit(percentOf(aligned, la), 0, "%") },
                    { label: "Copertura di B", value: fmtUnit(percentOf(aligned, lb), 0, "%") },
                    { label: "Identità di sequenza", value: fmtUnit(identity, 1, "%") },
                  ]} />
                </>
              )} />
          </div>
        )}
        {gdt.length > 0 && (
          <Disclosure title="Cα di A entro ogni soglia (GDT)" extra={`${gdt.length} soglie`}>
            <div className="stack-sm">
              <p>Per ogni soglia MakPhi cerca la sovrapposizione che porta più Cα di B entro quella distanza dal corrispondente di A.</p>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Soglia</th><th className="num">Cα di A</th><th>Percentuale</th><th>Entra nel</th></tr></thead>
                  <tbody>
                    {gdt.map((g) => (
                      <tr key={g.cutoff}>
                        <td className="nowrap"><b>P{cutoffText(g.cutoff)}</b> · {cutoffText(g.cutoff)} Å</td>
                        <td className="num">{g.count}{la ? ` di ${la}` : ""}</td>
                        <td><BarCell value={g.percent} /></td>
                        <td>{[GDT_TS.includes(g.cutoff) && "GDT-TS", GDT_HA.includes(g.cutoff) && "GDT-HA"].filter(Boolean).join(", ") || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </Disclosure>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------------- profilo per posizione
/** ampiezza minima dello zoom (coppie) e ampiezza sotto la quale anche l'lDDT mostra i punti */
const MIN_SPAN = 10;
const LDDT_DOTS_BELOW = 80;

/** Raggio dei punti della distanza: più grandi quando l'intervallo visibile ha poche coppie. */
function pointRadius(visible: number, expanded: boolean): number {
  const r = visible > 400 ? 1.3 : visible > 150 ? 2 : visible > 60 ? 2.8 : 3.8;
  return expanded ? r + 0.7 : r;
}

function ProfileLegend({ threshold }: { threshold: number }) {
  const scale = legendFor(DISTANCE_SCALE, "Distanza Cα");
  return (
    <div className="legend">
      {scale.kind === "gradient" && (
        <span className="cmp-scale">Distanza Cα: 0 <span className="cmp-scale-bar" style={{ background: scale.css }} /> ≥ {DISTANCE_MAX} Å</span>
      )}
      <span><i className="cmp-line" style={{ background: "var(--accent)" }} />distanza</span>
      <span><i className="cmp-line" style={{ background: "var(--ok)" }} />lDDT</span>
      <span><i className="cmp-band" />tratti che deviano (oltre {threshold} Å)</span>
      <span><i className="cmp-line" style={{ background: "var(--warn)" }} />coppia selezionata</span>
    </div>
  );
}

/**
 * Distanza e lDDT per posizione (una colonna per coppia, nell'ordine di A) nella cornice comune: zoom sulle coppie
 * condiviso con la vista espansa, tabella dei dati (clic = seleziona la coppia), PNG e CSV.
 */
function ComparisonProfile({ pairs, ids, zoom, selected, segments, threshold, onSelect }: {
  pairs: StructuralPair[];
  ids: string[];
  zoom: DomainState;
  selected: number | null;
  segments: DeviatingSegment[];
  threshold: number;
  onSelect: (i: number) => void;
}) {
  const breaks = useMemo(() => breaksBefore(pairs), [pairs]);
  const colors = useMemo(() => pairs.map((p) => colorFor(DISTANCE_SCALE, p.distance)), [pairs]);
  const dMax = useMemo(() => Math.max(DISTANCE_MAX, Math.ceil(Math.max(0, ...pairs.map((p) => p.distance)))), [pairs]);
  const multi = useMemo(() => pairs.some((p) => p.chain_a !== pairs[0].chain_a), [pairs]);
  const [idA, idB] = ids;
  const data = (): ChartData => ({
    columns: [
      { key: "posizione", label: "Posizione", num: true },
      { key: "catena_a", label: "Catena di A" },
      { key: "residuo_a", label: `Residuo di A (${idA})` },
      { key: "catena_b", label: "Catena di B" },
      { key: "residuo_b", label: `Residuo di B (${idB})` },
      { key: "distanza", label: "Distanza Cα (Å)", num: true },
      { key: "lddt", label: "lDDT", num: true },
    ],
    rows: pairs.map((p, i) => ({
      posizione: i + 1, catena_a: p.chain_a, residuo_a: pairResidue(p, "a"), catena_b: p.chain_b, residuo_b: pairResidue(p, "b"),
      distanza: p.distance, lddt: p.lddt,
    })),
  });
  return (
    <ChartFrame
      title="Deviazione per posizione"
      subtitle={`Distanza Cα dopo la sovrapposizione del TM-score e lDDT del residuo di A · coppie vicine sotto ${DISTANCE_MAX} Å · tratti che deviano: almeno 3 coppie oltre ${threshold} Å · lDDT sotto 0,5: il residuo diverge`}
      filename={`deviazione_${idA}_${idB}`}
      controls={<ZoomBar zoom={zoom} format={(lo, hi) => pairSpan(pairs, lo, hi)} />}
      hint={ZOOM_HINT}
      legend={<ProfileLegend threshold={threshold} />}
      data={data}
      onRowClick={(_row, i) => onSelect(i)}
      selectedIndex={selected ?? undefined}
    >
      {({ expanded }) => (
        <ProfileSvg expanded={expanded} pairs={pairs} breaks={breaks} colors={colors} dMax={dMax} multi={multi} zoom={zoom}
          selected={selected} segments={segments} onSelect={onSelect} />
      )}
    </ChartFrame>
  );
}

/** SVG del profilo (uno per vista: pagina ed espansa); zoom e selezione arrivano dal componente esterno. */
function ProfileSvg({ expanded, pairs, breaks, colors, dMax, multi, zoom, selected, segments, onSelect }: {
  expanded: boolean;
  pairs: StructuralPair[];
  breaks: boolean[];
  colors: string[];
  dMax: number;
  multi: boolean;
  zoom: DomainState;
  selected: number | null;
  segments: DeviatingSegment[];
  onSelect: (i: number) => void;
}) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const clip = `cmp-clip${useId().replace(/[^\w-]/g, "")}`;
  const { ref, width: W } = useChartWidth(900);
  const pad = { l: expanded ? 56 : 46, r: 14 };
  const top = { t: 22, h: expanded ? 300 : 118 };
  const low = { t: top.t + top.h + 32, h: expanded ? 150 : 64 };
  const bottom = low.t + low.h;
  const H = bottom + 24;
  const axis = useZoomAxis({ zoom, range: [pad.l, W - pad.r], wheel: expanded, area: { y0: top.t, y1: bottom } });
  const n = pairs.length;
  const yd = (v: number) => top.t + (1 - Math.min(v, dMax) / dMax) * top.h;
  const yl = (v: number) => low.t + (1 - v) * low.h;
  const { domain } = zoom;
  const [from, to] = visibleIndices(domain, n);
  const r = pointRadius(to - from + 1, expanded);
  // serie e punti delle coppie visibili: non cambiano con il passaggio del mouse
  const layer = useMemo(() => {
    const x = (k: number) => axis.scale(from + k);
    const part = pairs.slice(from, to + 1);
    const cut = breaks.slice(from, to + 1);
    const edge = r >= 2.8;
    return (
      <>
        <path d={seriesPath(part.map((p) => p.distance), cut, x, yd)} fill="none" stroke="var(--accent)" strokeWidth={1.2} opacity={0.6} />
        {/* bordo nel colore degli assi (≥ 3:1 sul pannello): anche i punti chiari della scala (≈ 2–3 Å) restano visibili */}
        {part.map((p, k) => (
          <circle key={from + k} cx={x(k)} cy={yd(p.distance)} r={r} fill={colors[from + k]}
            stroke={edge ? "var(--chart-axis)" : undefined} strokeWidth={edge ? 0.6 : undefined} />
        ))}
        <path d={seriesPath(part.map((p) => p.lddt), cut, x, yl)} fill="none" stroke="var(--ok)" strokeWidth={expanded ? 1.8 : 1.6} strokeLinejoin="round" />
        {part.length <= LDDT_DOTS_BELOW && part.map((p, k) => (
          p.lddt === null ? null : <circle key={`l${from + k}`} cx={x(k)} cy={yl(p.lddt)} r={Math.max(1.8, r - 1)} fill="var(--ok)" />
        ))}
      </>
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- axis, yd, yl e r derivano da W, dMax, dominio ed expanded, già nelle dipendenze
  }, [pairs, breaks, colors, from, to, domain.lo, domain.hi, W, expanded, dMax]);
  const dTicks = niceTicks(0, dMax, expanded ? 8 : 4, [1, 2, 5, 10]);
  const lTicks = expanded ? [0, 0.25, 0.5, 0.75, 1] : [0, 0.5, 1];
  const xTicks = residueTicks(domain, Math.max(3, Math.floor((W - pad.l - pad.r) / 80)), n);
  const indexAt = (e: RMouseEvent<SVGSVGElement>) => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    if (p.x < pad.l - 4 || p.x > W - pad.r + 4 || !n) return null;
    return Math.max(0, Math.min(n - 1, Math.round(axis.invert(p.x))));
  };
  const vline = (i: number) => ({ x1: axis.scale(i), x2: axis.scale(i), y1: top.t, y2: bottom });
  const describe = (i: number) => {
    const p = pairs[i];
    const lddt = lddtReading(p.lddt);
    return (
      <>
        <b>{pairResidue(p, "a", multi)} di A ↔ {pairResidue(p, "b")} di B</b>
        <div>Distanza Cα {fmtNum(p.distance, 2)} Å · {distanceReading(p.distance).text}</div>
        <div>lDDT {fmtNum(p.lddt, 2)}{lddt ? ` · ${lddt.text}` : ""}</div>
        <div className="text-2">Coppia {i + 1} di {n} · {residueChange(p.aa_a, p.aa_b).text}</div>
      </>
    );
  };
  const sel = selected !== null ? pairs[selected] : null;
  return (
    <div ref={ref} className="stack-sm">
      <svg
        ref={axis.svgRef}
        className="chart zoomable"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Distanza Cα e lDDT per posizione: ${n} coppie`}
        {...axis.handlers}
        onMouseMove={(e) => {
          const i = axis.brush ? null : indexAt(e);
          if (i === null) {
            setHover(null);
            return tip.hide();
          }
          setHover(i);
          tip.show(e, describe(i));
        }}
        onMouseLeave={() => {
          setHover(null);
          tip.hide();
        }}
        onClick={(e) => {
          if (axis.consumeClick()) return;
          const i = indexAt(e);
          if (i !== null) onSelect(i);
        }}
      >
        <defs>
          <clipPath id={clip}>
            <rect x={pad.l} y={0} width={Math.max(0, W - pad.l - pad.r)} height={H} />
          </clipPath>
        </defs>
        <text className="axis-title" x={pad.l} y={top.t - 9}>Distanza Cα (Å)</text>
        {dTicks.map((v) => (
          <g key={`d${v}`}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={yd(v)} y2={yd(v)} />
            <text x={pad.l - 6} y={yd(v) + 3.5} textAnchor="end">{fmtNum(v, 0)}</text>
          </g>
        ))}
        <text className="axis-title" x={pad.l} y={low.t - 9}>lDDT del residuo di A</text>
        {lTicks.map((v) => (
          <g key={`l${v}`}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={yl(v)} y2={yl(v)} />
            <text x={pad.l - 6} y={yl(v) + 3.5} textAnchor="end">{fmtNum(v, expanded ? 2 : 1)}</text>
          </g>
        ))}
        <line x1={pad.l} x2={W - pad.r} y1={bottom} y2={bottom} stroke="var(--chart-axis)" />
        {xTicks.map((i) => (
          <g key={i}>
            <line x1={axis.scale(i)} x2={axis.scale(i)} y1={bottom} y2={bottom + 4} stroke="var(--chart-axis)" />
            <text x={axis.scale(i)} y={H - 6} textAnchor="middle">{multi ? `${pairs[i].chain_a}${pairs[i].resnum_a}` : pairs[i].resnum_a}</text>
          </g>
        ))}
        <g clipPath={`url(#${clip})`}>
          {segments.filter((s) => inView(s, domain)).map((s) => (
            <rect key={s.start} x={axis.scale(s.start - 0.5)} y={top.t} width={Math.max(1, axis.scale(s.end + 0.5) - axis.scale(s.start - 0.5))} height={top.h}
              fill="var(--warn-soft)" />
          ))}
          {layer}
          {selected !== null && <line {...vline(selected)} stroke="var(--warn)" strokeWidth={2} />}
          {hover !== null && <line {...vline(hover)} stroke="var(--text-2)" strokeDasharray="3 3" />}
          {hover !== null && (
            <circle cx={axis.scale(hover)} cy={yd(pairs[hover].distance)} r={r + 1.5} fill={colors[hover]} stroke="var(--text)" strokeWidth={1.5} />
          )}
        </g>
        {/* soglia delle coppie vicine dopo i dati: l'etichetta con l'alone resta leggibile sopra i punti */}
        <RefLine y={yd(DISTANCE_MAX)} x0={pad.l} x1={W - pad.r} label={`${DISTANCE_MAX} Å`} />
        {axis.brushRect && <rect className="brush" x={axis.brushRect.x} y={top.t} width={axis.brushRect.width} height={bottom - top.t} />}
      </svg>
      {sel && (
        <div className="cmp-readout" data-export-ignore="">
          Coppia selezionata: <b>{pairResidue(sel, "a", multi)}</b> di A ↔ <b>{pairResidue(sel, "b")}</b> di B · distanza <b>{fmtNum(sel.distance, 2)} Å</b> · lDDT <b>{fmtNum(sel.lddt, 2)}</b>
        </div>
      )}
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------------------------- tratti che deviano
const THRESHOLDS = [1, 2, 4];

/** Tratti di almeno 3 coppie oltre la soglia: un chip per tratto, con i dettagli del tratto scelto. */
function DeviatingSegments({ pairs, segments, threshold, onThreshold, active, onPick, onZoom, onClose }: {
  pairs: StructuralPair[];
  segments: DeviatingSegment[];
  threshold: number;
  onThreshold: (t: number) => void;
  /** inizio del tratto scelto */
  active: number | null;
  onPick: (s: DeviatingSegment) => void;
  onZoom: (s: DeviatingSegment) => void;
  onClose: () => void;
}) {
  const current = segments.find((s) => s.start === active) ?? null;
  const detail = current ? segmentDetail(pairs, current) : null;
  return (
    <div className="cmp-segments">
      <div className="row small">
        <b>Tratti che deviano</b>
        <Badge tone={segments.length ? "warn" : "ok"}>{segments.length}</Badge>
        <label className="check">
          almeno 3 coppie consecutive oltre
          <select className="input input-sm" style={{ width: 80 }} value={threshold} onChange={(e) => onThreshold(Number(e.target.value))}>
            {THRESHOLDS.map((t) => <option key={t} value={t}>{t} Å</option>)}
          </select>
        </label>
      </div>
      {segments.length ? (
        <div className="row" role="group" aria-label="Tratti che deviano">
          {segments.map((s) => (
            <button key={s.start} type="button" className="chip" aria-pressed={s.start === active} title="Evidenzia nel 3D e mostra i dettagli" onClick={() => onPick(s)}>
              {s.text}
            </button>
          ))}
        </div>
      ) : <p className="small text-2">Nessun tratto di almeno 3 coppie consecutive oltre {threshold} Å.</p>}
      {current && detail && (
        <DetailPanel
          title={`Tratto ${current.from}–${current.to} di A`}
          label={`Tratto ${current.from}–${current.to}`}
          badges={<Badge tone="warn">fino a {fmtNum(detail.maxDistance, 1)} Å</Badge>}
          actions={(
            <button type="button" className="btn btn-sm" onClick={() => onZoom(current)} title="Ingrandisci il grafico sul tratto">
              <Icon name="zoomIn" size={14} /> Ingrandisci nel grafico
            </button>
          )}
          onClose={onClose}
          closeLabel="Chiudi i dettagli del tratto"
        >
          <DetailGrid items={[
            { label: "Coppie", value: detail.count },
            { label: "Residui di A", value: `${current.from}–${current.to}${detail.chainA ? ` (catena ${detail.chainA})` : ""}` },
            { label: "Residui di B", value: `${detail.fromB}–${detail.toB}${detail.chainB ? ` (catena ${detail.chainB})` : ""}` },
            { label: "Distanza media", value: fmtUnit(detail.meanDistance, 2, "Å") },
            { label: "Distanza massima", value: fmtUnit(detail.maxDistance, 2, "Å") },
            { label: "lDDT medio", value: fmtNum(detail.meanLddt, 2) },
            { label: "Residui diversi", value: `${detail.differing} di ${detail.count}` },
          ]} />
        </DetailPanel>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------- tabella delle coppie
const MIN_DISTANCES = [0, 1, 2, DISTANCE_MAX];

/** Dettagli della coppia selezionata, sotto la tabella: le righe a finestra hanno altezza fissa, niente righe in linea. */
function PairDetail({ pairs, index, onSelect }: { pairs: StructuralPair[]; index: number; onSelect: (i: number) => void }) {
  const p = pairs[index];
  const distance = distanceReading(p.distance);
  const lddt = lddtReading(p.lddt);
  return (
    <DetailPanel
      title={`Coppia ${index + 1} di ${pairs.length}`}
      label="Coppia selezionata"
      badges={<span className="small text-2">{pairResidue(p, "a", true)} di A ↔ {pairResidue(p, "b", true)} di B</span>}
      actions={(
        <>
          <button type="button" className="btn btn-sm btn-ghost" disabled={index <= 0} onClick={() => onSelect(index - 1)} title="Coppia precedente (ordine di A)">
            <span style={{ transform: "rotate(180deg)", display: "inline-grid" }}><Icon name="chevron" size={14} /></span> Precedente
          </button>
          <button type="button" className="btn btn-sm btn-ghost" disabled={index >= pairs.length - 1} onClick={() => onSelect(index + 1)} title="Coppia successiva (ordine di A)">
            Successiva <Icon name="chevron" size={14} />
          </button>
        </>
      )}
    >
      <DetailGrid items={[
        { label: "Residuo di A", value: `${residueText(p.aa_a)} ${p.resnum_a}` },
        { label: "Catena di A", value: p.chain_a || "—" },
        { label: "Residuo di B", value: `${residueText(p.aa_b)} ${p.resnum_b}` },
        { label: "Catena di B", value: p.chain_b || "—" },
        { label: "Numerazione", value: numberingText(p) },
        { label: "Differenza di residuo", value: residueChange(p.aa_a, p.aa_b).text },
        {
          label: "Distanza Cα",
          value: <><i className="cmp-swatch" style={{ background: colorFor(DISTANCE_SCALE, p.distance) }} />{fmtNum(p.distance, 2)} Å <Badge tone={distance.tone}>{distance.text}</Badge></>,
        },
        { label: "lDDT del residuo di A", value: lddt ? <>{fmtNum(p.lddt, 2)} <Badge tone={lddt.tone}>{lddt.text}</Badge></> : "—" },
      ]} />
    </DetailPanel>
  );
}

/** Tabella per coppia allineata: ordinamento, filtro per distanza, clic = evidenzia nel 3D; dettagli della coppia sotto. */
function PairTable({ pairs, selected, onSelect }: { pairs: StructuralPair[]; selected: number | null; onSelect: (i: number) => void }) {
  const [sort, setSort] = useState<PairSort>("position");
  const [minDistance, setMinDistance] = useState(0);
  const rows = useMemo(() => pairRows(pairs, sort, minDistance), [pairs, sort, minDistance]);
  // oltre VIRTUAL_THRESHOLD coppie si disegnano solo le righe visibili (F8b). La coppia scelta si porta in vista
  // scorrendo solo il contenitore della tabella (`reveal`, con `rowScrollDelta` sulla riga vera quando è disegnata):
  // una selezione dal 3D, dal grafico o dai tratti non sposta la pagina
  const virtual = useVirtualRows<HTMLDivElement>({ count: rows.length, rowHeight: TABLE_ROW_HEIGHT, measure: true });
  const rowStyle = virtual.active ? { height: TABLE_ROW_HEIGHT } : undefined;
  const selIndex = selected === null ? -1 : rows.indexOf(selected);
  const { reveal } = virtual;
  useEffect(() => {
    if (selIndex >= 0) reveal(selIndex);
  }, [selected, selIndex, reveal]);
  // l'ordinamento è un pulsante dentro l'intestazione (raggiungibile da tastiera); aria-sort resta sulla colonna
  const head = (key: PairSort, label: string, num = false) => (
    <th className={`sortable${num ? " num" : ""}`} aria-sort={sort === key ? (key === "distance" ? "descending" : "ascending") : undefined}>
      <button type="button" className="th-sort" onClick={() => setSort(key)} title={`Ordina per ${label}`}>
        {label}{sort === key && <span aria-hidden="true">{key === "distance" ? "▼" : "▲"}</span>}
      </button>
    </th>
  );
  return (
    <div className="stack-sm">
      <div className="row small">
        <label className="check">
          Mostra
          <select className="input input-sm" style={{ width: 190 }} value={minDistance} onChange={(e) => setMinDistance(Number(e.target.value))}>
            {MIN_DISTANCES.map((d) => <option key={d} value={d}>{d ? `solo coppie ≥ ${d} Å` : `tutte le coppie (${pairs.length})`}</option>)}
          </select>
        </label>
        <span className="text-2">{rows.length} righe · clic (o Invio) su una riga per evidenziare i due residui nel 3D e vederne i dettagli.</span>
      </div>
      <div ref={virtual.ref} onScroll={virtual.onScroll} className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
        <table className="table">
          <thead><tr>{head("position", "Posizione")}<th>Residuo di A</th><th>Residuo di B</th>{head("distance", "Distanza (Å)", true)}{head("lddt", "lDDT", true)}</tr></thead>
          <tbody>
            {virtual.range.padTop > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padTop }}><td colSpan={5} /></tr>}
            {rows.slice(virtual.range.start, virtual.range.end).map((i, n) => {
              const p = pairs[i];
              const change = residueChange(p.aa_a, p.aa_b).kind;
              return (
                <tr key={i} data-vrow={virtual.range.start + n} style={rowStyle} aria-selected={i === selected}
                  className={`clickable${i === selected ? " selected" : ""}${virtual.active ? " virtual-row" : ""}`} {...rowActivate(() => onSelect(i))}>
                  <td className="muted">{i + 1}</td>
                  <td className="nowrap"><b>{p.aa_a}</b> {p.chain_a}{p.resnum_a}</td>
                  <td className="nowrap">
                    <b>{p.aa_b}</b> {p.chain_b}{p.resnum_b}
                    {change !== "identical" && <> <Badge tone={change === "conservative" ? "info" : "warn"}>{change === "conservative" ? "simile" : "diverso"}</Badge></>}
                  </td>
                  <td className="num nowrap"><i className="cmp-swatch" style={{ background: colorFor(DISTANCE_SCALE, p.distance) }} />{fmtNum(p.distance, 2)}</td>
                  <td className="num">{fmtNum(p.lddt, 2)}</td>
                </tr>
              );
            })}
            {virtual.range.padBottom > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padBottom }}><td colSpan={5} /></tr>}
          </tbody>
        </table>
      </div>
      {selected !== null && pairs[selected]
        ? <PairDetail pairs={pairs} index={selected} onSelect={onSelect} />
        : <p className="small text-2">Seleziona una coppia (nella tabella, nel grafico o nel 3D) per i suoi dettagli.</p>}
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
  const [segment, setSegment] = useState<number | null>(null);
  // zoom del profilo: condiviso tra la pagina e la vista espansa, segue la coppia scelta altrove (3D, tabella). Mezza
  // coppia di margine per lato (come ConsensusTracks e ValueBars): la prima e l'ultima non finiscono sul bordo del clip
  const zoom = useDomain(-0.5, Math.max(0.5, pairs.length - 0.5), MIN_SPAN);
  useFollowSelection(zoom, selected ?? -1);
  const coloring = useMemo(() => (pairs.length ? comparisonColoring(pairs, by) : null), [pairs, by]);
  const segments = useMemo(() => deviatingSegments(pairs, threshold), [pairs, threshold]);

  // chip premuto, pannello del tratto ed evidenza nel 3D vanno insieme: scegliere una coppia spegne il tratto
  const select = (i: number, center = true) => {
    setSelected(i);
    setSegment(null);
    setFocus(pairHighlight(pairs[i]));
    if (center) setCenterKey((k) => k + 1);
  };
  const clearHighlight = () => {
    setFocus([]);
    setSelected(null);
    setSegment(null);
  };
  // a un'altra soglia i tratti cambiano: quello evidenziato non corrisponde più ai chip
  const changeThreshold = (t: number) => {
    setThreshold(t);
    if (segment !== null) clearHighlight();
  };
  const pick3d = (ref: ResidueRef) => {
    const i = pairIndexOf(pairs, ref);
    if (i >= 0) select(i, false);
  };
  const pickSegment = (s: DeviatingSegment) => {
    if (segment === s.start) return clearHighlight();
    setSegment(s.start);
    setSelected(s.start);
    setFocus(pairs.slice(s.start, s.end + 1).flatMap(pairHighlight));
    setCenterKey((k) => k + 1);
  };
  const zoomTo = (s: DeviatingSegment) => {
    const margin = Math.max(3, (s.end - s.start) / 2);
    zoom.set({ lo: s.start - margin, hi: s.end + margin });
  };
  const [idA, idB] = result.ids;
  return (
    <div className="stack">
      <Metrics result={result} />
      {result.note && <Alert tone="info">{result.note}</Alert>}
      {comparisonOutdated(result) && (
        <Alert tone="info">
          Confronto per residuo non disponibile: risultato di una versione precedente di MakPhi. Rilancia l'allineamento
          (i risultati precedenti in cache vengono ricalcolati) per distanze, GDT e lDDT.
        </Alert>
      )}
      {result.superposed_file && (
        <Card
          title="Sovrapposizione 3D"
          icon="cube"
          sub={pairs.length ? `A = ${idA} (grigio chiaro), B = ${idB} colorata per ${by === "distance" ? "distanza dal residuo corrispondente" : "lDDT"}` : `${idA} (catena A) e ${idB} (catena B)`}
          right={pairs.length > 0 && <Segmented label="Colora per" value={by} onChange={setBy} options={[{ value: "distance", label: "Distanza" }, { value: "lddt", label: "lDDT" }]} />}
        >
          <div className="stack-sm">
            <Viewer3D url={api.jobFileUrl(jobId, result.superposed_file)} scheme="chain" coloring={coloring} highlight={focus} centerKey={centerKey}
              onPickResidue={pairs.length ? pick3d : undefined} />
            {pairs.length > 0 && (
              <div className="row small">
                <button type="button" className="btn btn-sm btn-ghost" disabled={!focus.length} onClick={clearHighlight}>
                  <Icon name="x" size={13} /> Nessuna evidenza
                </button>
                <span className="text-2">Coppie evidenziate a bastoncini arancioni · clic su un residuo per selezionare la sua coppia.</span>
              </div>
            )}
          </div>
        </Card>
      )}
      {pairs.length > 0 && (
        <>
          <Card>
            <div className="stack">
              <ComparisonProfile pairs={pairs} ids={result.ids} zoom={zoom} selected={selected} segments={segments} threshold={threshold} onSelect={(i) => select(i)} />
              <DeviatingSegments pairs={pairs} segments={segments} threshold={threshold} onThreshold={changeThreshold} active={segment}
                onPick={pickSegment} onZoom={zoomTo} onClose={clearHighlight} />
            </div>
          </Card>
          <Card title="Coppie allineate" icon="list" sub={`${pairs.length} coppie · numerazione dei file originali`} collapsible storageKey="compare.pairs">
            <PairTable pairs={pairs} selected={selected} onSelect={(i) => select(i)} />
          </Card>
        </>
      )}
    </div>
  );
}
