import { useEffect, useId, useMemo, useState, type MouseEvent as RMouseEvent, type ReactNode } from "react";
import type { ChartData, DataValue } from "../lib/exportImage";
import { fmtNum } from "../lib/format";
import { barRange, barSpan, labelEvery, type BarSeries } from "../lib/toolsView";
import { niceTicks, svgPoint, useDomain, useZoomAxis, visibleIndices, type DomainState } from "../lib/zoom";
import { ChartFrame, ZoomBar } from "./ChartFrame";
import { RefLine, tickDigits, useChartWidth, useFollowSelection, useTooltip, ZOOM_HINT } from "./charts";
import "../styles/tools.css";

/** oltre queste barre l'asse x si ingrandisce; ampiezza minima dello zoom (barre) */
const ZOOM_FROM = 60;
const MIN_SPAN = 10;

/**
 * Grafico a barre dei pannelli di analisi: una barra per elemento (tasca, mutazione) o per residuo, colore della
 * categoria o del segno, soglie tratteggiate, selezione condivisa con tabelle e 3D. Oltre `ZOOM_FROM` barre l'asse x si
 * ingrandisce (trascinamento, rotella, barra dello zoom), con lo stato condiviso tra la pagina e la vista espansa.
 * `band` evidenzia un tratto (indici delle barre) e, se l'asse si ingrandisce, lo porta in vista. Barre riordinate o
 * sostituite (stesso numero, altre chiavi) riportano lo zoom a tutto l'asse. `titleHidden` e `className` vanno alla
 * cornice (ChartFrame).
 */
export function ValueBars({ title, subtitle, filename, series, data, noun = "elementi", selected, onSelect, describe, legend, controls, band, height = 190, titleHidden, className }: {
  title: string;
  subtitle?: ReactNode;
  filename: string;
  /** barre, nome del valore e soglie: memoizzata da chi la passa */
  series: BarSeries;
  /** tabella dei dati e CSV, una riga per barra nello stesso ordine */
  data: ChartData | (() => ChartData);
  /** nome delle barre al plurale per la barra dello zoom ("residui", "tasche") */
  noun?: string;
  selected?: string | null;
  onSelect?: (index: number) => void;
  /** righe aggiuntive del suggerimento di una barra */
  describe?: (index: number) => ReactNode;
  legend?: ReactNode;
  controls?: ReactNode;
  band?: { lo: number; hi: number } | null;
  height?: number;
  titleHidden?: boolean;
  className?: string;
}) {
  const n = series.items.length;
  const zoomable = n > ZOOM_FROM;
  // ordine degli elementi: cambiarlo (es. "Effetto" invece di "Sequenza") riporta lo zoom a tutto, la metrica no
  const order = useMemo(() => series.items.map((it) => it.key).join("|"), [series.items]);
  const zoom = useDomain(-0.5, Math.max(0.5, n - 0.5), Math.min(n, MIN_SPAN), order);
  const range = useMemo(
    () => barRange(series.items.map((it) => it.value), series.references.map((r) => r.value), { domain: series.domain, symmetric: series.symmetric }),
    [series],
  );
  const selIndex = selected == null ? -1 : series.items.findIndex((it) => it.key === selected);
  useFollowSelection(zoom, selIndex);
  const { set: setDomain } = zoom;
  useEffect(() => {
    if (band && zoomable) {
      const margin = Math.max(4, (band.hi - band.lo) * 0.5);
      setDomain({ lo: band.lo - 0.5 - margin, hi: band.hi + 0.5 + margin });
    }
  }, [band, zoomable, setDomain]);
  if (!n) return null;
  return (
    <ChartFrame
      title={title}
      subtitle={subtitle}
      filename={filename}
      controls={(controls || zoomable) && <>{controls}{zoomable && <ZoomBar zoom={zoom} format={(lo, hi) => barSpan(series.items, { lo, hi }, noun)} />}</>}
      hint={zoomable ? ZOOM_HINT : undefined}
      data={data}
      onRowClick={onSelect ? (_row: Record<string, DataValue>, i: number) => onSelect(i) : undefined}
      selectedIndex={selIndex >= 0 ? selIndex : undefined}
      legend={legend}
      titleHidden={titleHidden}
      className={className}
    >
      {({ expanded }) => (
        <BarsSvg expanded={expanded} series={series} range={range} zoom={zoom} zoomable={zoomable} selIndex={selIndex} band={band ?? null}
          height={expanded ? Math.max(420, height * 2) : height} noun={noun} describe={describe} onSelect={onSelect} />
      )}
    </ChartFrame>
  );
}

/** SVG delle barre (uno per vista: pagina ed espansa); zoom e selezione arrivano dal componente esterno. */
function BarsSvg({ expanded, series, range, zoom, zoomable, selIndex, band, height: H, noun, describe, onSelect }: {
  expanded: boolean;
  series: BarSeries;
  range: { min: number; max: number };
  zoom: DomainState;
  zoomable: boolean;
  selIndex: number;
  band: { lo: number; hi: number } | null;
  height: number;
  noun: string;
  describe?: (index: number) => ReactNode;
  onSelect?: (index: number) => void;
}) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const clip = `bars-clip${useId().replace(/[^\w-]/g, "")}`;
  const { ref, width: W } = useChartWidth(720);
  const pad = { l: expanded ? 66 : 54, r: 14, t: 14, b: 26 };
  const bottom = H - pad.b;
  const axis = useZoomAxis({ zoom, range: [pad.l, W - pad.r], wheel: expanded, enabled: zoomable, area: { y0: pad.t, y1: bottom } });
  const { items, references, digits, label, unit } = series;
  const n = items.length;
  const { min, max } = range;
  const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (bottom - pad.t);
  const y0 = y(Math.max(min, Math.min(max, 0)));
  const step = axis.scale(1) - axis.scale(0);
  // barre separate finché c'è spazio, poi accostate. Larghe almeno 3 px hanno un bordo sottile (i colori chiari restano
  // visibili sul fondo bianco); più strette il bordo coprirebbe il colore: una linea sola ne segue le cime
  const barW = step >= 4 ? step * 0.72 : Math.max(0.6, step);
  const outline = barW >= 3;
  const { lo, hi } = zoom.domain;
  const [from, to] = visibleIndices(zoom.domain, n);
  const bars = useMemo(() => {
    const out: ReactNode[] = [];
    for (let i = from; i <= to; i++) {
      const it = items[i];
      const x = axis.scale(i) - barW / 2;
      if (it.value === null || !Number.isFinite(it.value)) {
        out.push(<rect key={it.key} x={x} y={y0 - 1} width={barW} height={2} fill="var(--coil)" />);
        continue;
      }
      const top = Math.min(y(it.value), y0);
      out.push(
        <rect key={it.key} x={x} y={top} width={barW} height={Math.max(1, Math.abs(y(it.value) - y0))} fill={it.color} fillOpacity={it.faded ? 0.45 : undefined}
          stroke={i === selIndex ? "var(--text)" : outline ? "var(--chart-axis)" : undefined} strokeWidth={i === selIndex ? 1.6 : outline ? 0.6 : undefined} />,
      );
    }
    if (!outline) {
      // profilo a gradini sull'estremo di ogni barra (lontano dallo zero), interrotto dove manca il valore
      let d = "";
      let pen = false;
      for (let i = from; i <= to; i++) {
        const v = items[i].value;
        if (v === null || !Number.isFinite(v)) {
          pen = false;
          continue;
        }
        const x = axis.scale(i) - barW / 2;
        const yv = y(v).toFixed(1);
        d += pen ? `V${yv}H${(x + barW).toFixed(1)}` : `M${x.toFixed(1)},${yv}H${(x + barW).toFixed(1)}`;
        pen = true;
      }
      if (d) out.push(<path key="tops" d={d} fill="none" stroke="var(--chart-axis)" strokeWidth={0.75} />);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- axis, y, y0 e barW derivano da W, H, min/max, dominio ed expanded, già nelle dipendenze
  }, [items, from, to, lo, hi, W, H, min, max, selIndex, expanded]);
  const yTicks = niceTicks(min, max, expanded ? 8 : 4, [1, 2, 5, 10]);
  const yDigits = tickDigits(yTicks);
  const longest = useMemo(() => items.reduce((m, it) => Math.max(m, it.tick.length), 1), [items]);
  const every = labelEvery(step, longest * 6.6 + 10);
  const xLabels: number[] = [];
  for (let i = Math.ceil(from / every) * every; i <= to; i += every) xLabels.push(i);
  const fmt = (v: number | null) => (v === null ? "—" : `${fmtNum(v, digits)}${unit ? ` ${unit}` : ""}`);
  const indexAt = (e: RMouseEvent<SVGSVGElement>) => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    if (p.x < pad.l - 2 || p.x > W - pad.r + 2 || !n) return null;
    return Math.max(0, Math.min(n - 1, Math.round(axis.invert(p.x))));
  };
  const column = (i: number) => ({ x: axis.scale(i - 0.5), y: pad.t, width: Math.max(1, step), height: bottom - pad.t });
  const title = unit ? `${label} (${unit})` : label;
  return (
    <div ref={ref}>
      <svg
        ref={axis.svgRef}
        className={`chart${zoomable ? " zoomable" : ""}${onSelect ? " selectable" : ""}`}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${title}: ${n} ${noun}`}
        {...axis.handlers}
        onMouseMove={(e) => {
          const i = axis.brush ? null : indexAt(e);
          if (i === null) {
            setHover(null);
            return tip.hide();
          }
          setHover(i);
          const it = items[i];
          tip.show(e, (
            <>
              <b>{it.label}</b>
              <div>{label}: {fmt(it.value)}</div>
              {describe?.(i)}
            </>
          ));
        }}
        onMouseLeave={() => {
          setHover(null);
          tip.hide();
        }}
        onClick={(e) => {
          if (axis.consumeClick()) return;
          const i = indexAt(e);
          if (i !== null) onSelect?.(i);
        }}
      >
        <defs>
          <clipPath id={clip}>
            <rect x={pad.l} y={0} width={Math.max(0, W - pad.l - pad.r)} height={H} />
          </clipPath>
        </defs>
        {yTicks.map((v) => (
          <g key={v}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
            <text x={pad.l - 6} y={y(v) + 3.5} textAnchor="end">{fmtNum(v, yDigits)}</text>
          </g>
        ))}
        {expanded && (
          <text className="axis-title" x={14} y={(pad.t + bottom) / 2} textAnchor="middle" transform={`rotate(-90 14 ${(pad.t + bottom) / 2})`}>{title}</text>
        )}
        <g clipPath={`url(#${clip})`}>
          {band && (
            <rect x={axis.scale(band.lo - 0.5)} y={pad.t} width={Math.max(1, axis.scale(band.hi + 0.5) - axis.scale(band.lo - 0.5))} height={bottom - pad.t}
              fill="var(--warn-soft)" />
          )}
          {selIndex >= 0 && <rect {...column(selIndex)} fill="var(--accent-soft)" />}
          {hover !== null && hover !== selIndex && <rect {...column(hover)} fill="var(--panel-3)" />}
          {bars}
        </g>
        <line x1={pad.l} x2={W - pad.r} y1={y0} y2={y0} stroke="var(--chart-axis)" />
        {y0 < bottom - 0.5 && <line x1={pad.l} x2={W - pad.r} y1={bottom} y2={bottom} stroke="var(--chart-grid)" />}
        {/* soglie dopo le barre: l'etichetta con l'alone del pannello resta leggibile anche sopra una barra */}
        {references.map((r) => <RefLine key={`${r.value}${r.label}`} y={y(r.value)} x0={pad.l} x1={W - pad.r} label={r.label} color={r.color} />)}
        {xLabels.map((i) => (
          <g key={i}>
            <line x1={axis.scale(i)} x2={axis.scale(i)} y1={bottom} y2={bottom + 4} stroke="var(--chart-axis)" />
            <text x={axis.scale(i)} y={H - 7} textAnchor="middle">{items[i].tick}</text>
          </g>
        ))}
        {axis.brushRect && <rect className="brush" x={axis.brushRect.x} y={pad.t} width={axis.brushRect.width} height={bottom - pad.t} />}
      </svg>
      {tip.node}
    </div>
  );
}
