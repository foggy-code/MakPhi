import type { MouseEvent as RMouseEvent, ReactNode } from "react";
import type { ChartData } from "../../lib/exportImage";
import { svgPoint, useDomain, useZoomAxis, type DomainState } from "../../lib/zoom";
import { ChartFrame, ZoomBar } from "../ChartFrame";
import { useChartWidth, useTooltip, ZOOM_HINT } from "./common";
import { inView, integerTicks } from "./linearHelpers";

// ---------------------------------------------------------------------------------- mappa delle feature (UniProt)
export interface Track {
  label: string;
  color: string;
  /** colore di un tratto diverso da quello della traccia (es. eliche e filamenti nella stessa traccia) */
  items: { start: number; end: number; title: string; color?: string }[];
  /** testo sulla traccia senza tratti (es. "nessuna regione") */
  empty?: string;
}

const MIN_SPAN = 10;
/** etichette delle tracce oltre questa lunghezza tagliate (il nome intero nel suggerimento) */
const LABEL_CHARS = 24;

/**
 * Tracce di regioni lungo la sequenza (disordine, domini, struttura secondaria predetta, annotazioni UniProt). Le
 * posizioni si ingrandiscono come i residui dei profili; il clic su un tratto lo ingrandisce.
 */
export function FeatureMap({ length, tracks, title = "Mappa delle regioni", subtitle, filename, titleHidden, className }: {
  length: number;
  tracks: Track[];
  title?: string;
  subtitle?: ReactNode;
  filename?: string;
  /** passate alla cornice (ChartFrame) */
  titleHidden?: boolean;
  className?: string;
}) {
  // la posizione p occupa da p − 0,5 a p + 0,5: i tratti di un solo residuo restano visibili ingranditi
  const zoom = useDomain(0.5, Math.max(1, length) + 0.5, MIN_SPAN);
  const data = (): ChartData => ({
    columns: [
      { key: "traccia", label: "Traccia" },
      { key: "inizio", label: "Inizio", num: true },
      { key: "fine", label: "Fine", num: true },
      { key: "lunghezza", label: "Lunghezza", num: true },
      { key: "descrizione", label: "Descrizione" },
    ],
    rows: tracks.flatMap((t) => t.items.map((it) => ({ traccia: t.label, inizio: it.start, fine: it.end, lunghezza: it.end - it.start + 1, descrizione: it.title }))),
  });
  return (
    <ChartFrame
      title={title}
      subtitle={subtitle}
      filename={filename ?? title}
      controls={<ZoomBar zoom={zoom} format={(lo, hi) => `posizioni ${Math.max(1, Math.ceil(lo - 1e-6))}–${Math.min(length, Math.floor(hi + 1e-6))} (${length} in tutto)`} />}
      hint={ZOOM_HINT}
      data={data}
      titleHidden={titleHidden}
      className={className}
    >
      {({ expanded }) => <FeatureSvg expanded={expanded} length={length} tracks={tracks} zoom={zoom} />}
    </ChartFrame>
  );
}

function FeatureSvg({ expanded, length, tracks, zoom }: { expanded: boolean; length: number; tracks: Track[]; zoom: DomainState }) {
  const tip = useTooltip();
  const { ref, width: W } = useChartWidth(900);
  const label = expanded ? 190 : 150;
  const right = 12;
  const rowH = expanded ? 28 : 20;
  const barH = expanded ? 16 : 12;
  const top = 24;
  const H = top + tracks.length * rowH + 4;
  const axis = useZoomAxis({ zoom, range: [label, W - right], wheel: expanded, area: { y0: 0, y1: H } });
  const { domain } = zoom;
  const ticks = integerTicks(Math.max(1, domain.lo), Math.min(length, domain.hi), Math.max(3, Math.floor((W - label - right) / 90)));
  // tratto sotto il puntatore, cercato sull'SVG: con il puntatore catturato dallo zoom il clic non arriva ai rettangoli
  const itemAt = (e: RMouseEvent<SVGSVGElement>) => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    const row = Math.floor((p.y - top) / rowH);
    const y = top + row * rowH + 1;
    if (row < 0 || row >= tracks.length || p.y < y || p.y > y + barH || p.x < label || p.x > W - right) return null;
    const at = axis.invert(p.x);
    const tolerance = (domain.hi - domain.lo) / Math.max(1, W - label - right); // un pixel: i tratti larghi 2 px
    return tracks[row].items.find((it) => at >= it.start - 0.5 - tolerance && at <= it.end + 0.5 + tolerance) ?? null;
  };
  return (
    <div ref={ref}>
      <svg ref={axis.svgRef} className="chart zoomable" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Mappa delle regioni: ${tracks.length} tracce su ${length} posizioni`}
        {...axis.handlers}
        onClick={(e) => {
          if (axis.consumeClick()) return;
          const it = itemAt(e);
          if (!it) return;
          const margin = Math.max(2, (it.end - it.start + 1) * 0.15);
          zoom.set({ lo: it.start - 0.5 - margin, hi: it.end + 0.5 + margin });
        }}>
        {ticks.map((p) => (
          <g key={p}>
            <line className="grid-line" x1={axis.scale(p)} x2={axis.scale(p)} y1={14} y2={H} />
            <text x={axis.scale(p)} y={10} textAnchor="middle">{p}</text>
          </g>
        ))}
        {tracks.map((t, r) => {
          const y = top + r * rowH + 1;
          const mid = y + barH / 2;
          return (
            <g key={t.label}>
              <text x={label - 8} y={mid + 4} textAnchor="end">
                {t.label.length > LABEL_CHARS && <title>{t.label}</title>}
                {t.label.slice(0, LABEL_CHARS)}
              </text>
              <line x1={label} x2={W - right} y1={mid} y2={mid} stroke="var(--border-strong)" />
              {!t.items.length && t.empty && <text x={label + 6} y={mid - 3} style={{ fill: "var(--text-2)" }}>{t.empty}</text>}
              {t.items.map((it, i) => {
                if (!inView(it, domain)) return null;
                const x0 = axis.scale(Math.max(domain.lo, it.start - 0.5));
                const x1 = axis.scale(Math.min(domain.hi, it.end + 0.5));
                // colori pieni con un bordo sottile (≥ 3:1 sul pannello): i gialli e i tratti di 1–2 residui (2 px)
                // restano visibili anche sul bianco del tema chiaro
                return (
                  <rect
                    key={i}
                    x={x0}
                    y={y}
                    width={Math.max(2, x1 - x0)}
                    height={barH}
                    rx={3}
                    fill={it.color ?? t.color}
                    stroke="var(--chart-axis)"
                    strokeWidth={0.75}
                    style={{ cursor: "zoom-in" }}
                    onMouseMove={(e) => tip.show(e, (
                      <>
                        <b>{it.title || t.label}</b>
                        <div>{it.start}–{it.end} · {it.end - it.start + 1} residui</div>
                        {it.title && <div className="text-2">{t.label}</div>}
                        <div className="text-2">clic per ingrandire il tratto</div>
                      </>
                    ))}
                    onMouseLeave={tip.hide}
                  />
                );
              })}
            </g>
          );
        })}
        {axis.brushRect && <rect className="brush" x={axis.brushRect.x} y={top - 4} width={axis.brushRect.width} height={H - top + 4} />}
      </svg>
      {tip.node}
    </div>
  );
}
