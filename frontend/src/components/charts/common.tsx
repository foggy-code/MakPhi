import { useEffect, useId, useLayoutEffect, useRef, useState, type MouseEvent as RMouseEvent, type ReactNode } from "react";
import { fmtNum } from "../../lib/format";
import type { DomainState } from "../../lib/zoom";

// ---------------------------------------------------------------------------------- tooltip
const GAP = { x: 14, y: 12 };
const MARGIN = 8;

/** Porta il suggerimento accanto al puntatore; vicino ai bordi della finestra passa a sinistra e/o sopra. */
function place(el: HTMLElement, x: number, y: number) {
  const { width, height } = el.getBoundingClientRect();
  const right = x + GAP.x + width > window.innerWidth - MARGIN;
  const below = y + GAP.y + height > window.innerHeight - MARGIN;
  el.style.left = `${right ? Math.max(MARGIN, x - GAP.x - width) : x + GAP.x}px`;
  el.style.top = `${below ? Math.max(MARGIN, y - GAP.y - height) : y + GAP.y}px`;
}

/**
 * Suggerimento che segue il puntatore. `show` cambia contenuto e posizione (un render); `move` sposta solo il nodo,
 * senza render: per i grafici fitti (matrici) si chiama `show` quando cambia la cella e `move` negli altri casi.
 */
export function useTooltip() {
  const [tip, setTip] = useState<{ x: number; y: number; content: ReactNode } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (tip && ref.current) place(ref.current, tip.x, tip.y);
  }, [tip]);
  const node = tip ? (
    <div ref={ref} className="tooltip" data-export-ignore="" style={{ left: tip.x + GAP.x, top: tip.y + GAP.y }}>
      {tip.content}
    </div>
  ) : null;
  return {
    show: (e: RMouseEvent, content: ReactNode) => setTip({ x: e.clientX, y: e.clientY, content }),
    move: (e: RMouseEvent) => {
      if (ref.current) place(ref.current, e.clientX, e.clientY);
    },
    hide: () => setTip(null),
    node,
  };
}

// ---------------------------------------------------------------------------------- grafici lineari ingrandibili
export const ZOOM_HINT = "Trascina sul grafico per ingrandire un tratto · Ctrl + rotella per lo zoom (nella vista espansa basta la rotella)";

/**
 * Larghezza in pixel del contenitore del grafico: l'SVG usa un viewBox largo quanto lo spazio disponibile, così il
 * testo resta a 11 px a ogni larghezza. Senza misura (markup statico, prima del primo layout) vale `fallback`.
 */
export function useChartWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = Math.floor(el.clientWidth);
      if (w > 0) setWidth(w);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, width: Math.max(320, width) };
}

/**
 * Quando cambia la selezione (dal 3D, da una tabella) e il residuo è fuori dall'intervallo ingrandito, la vista si
 * sposta su di lui con la stessa ampiezza. Spostare la vista a mano non la riporta indietro.
 */
export function useFollowSelection(zoom: DomainState, index: number) {
  const { domain, full, set } = zoom;
  useEffect(() => {
    if (index < 0 || full || (index >= domain.lo && index <= domain.hi)) return;
    const half = (domain.hi - domain.lo) / 2;
    set({ lo: index - half, hi: index + half });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index]);
}

// ---------------------------------------------------------------------------------- parti comuni degli SVG
/** id univoco per un clipPath (uno per istanza: pagina e vista espansa disegnano lo stesso grafico due volte). */
export function useClipId(prefix: string) {
  return `${prefix}-clip${useId().replace(/[^\w-]/g, "")}`;
}

/** Linee orizzontali della griglia con i valori a sinistra dell'area dei dati. */
export function YGrid({ ticks, y, x0, x1, digits }: { ticks: number[]; y: (v: number) => number; x0: number; x1: number; digits: number }) {
  return (
    <>
      {ticks.map((v) => (
        <g key={v}>
          <line className="grid-line" x1={x0} x2={x1} y1={y(v)} y2={y(v)} />
          <text x={x0 - 6} y={y(v) + 3.5} textAnchor="end">{fmtNum(v, digits)}</text>
        </g>
      ))}
    </>
  );
}

/** Titolo dell'asse y, verticale, centrato tra y0 e y1. */
export function YAxisTitle({ y0, y1, children }: { y0: number; y1: number; children: ReactNode }) {
  const c = (y0 + y1) / 2;
  return <text className="axis-title" x={14} y={c} textAnchor="middle" transform={`rotate(-90 14 ${c})`}>{children}</text>;
}

/**
 * Soglia di riferimento: linea tratteggiata e, se c'è, l'etichetta a destra con l'alone del pannello (`.ref-label`),
 * leggibile anche sopra le serie. Va disegnata dopo i dati.
 */
export function RefLine({ y, x0, x1, label, color = "var(--warn)" }: { y: number; x0: number; x1: number; label?: ReactNode; color?: string }) {
  return (
    <g>
      <line x1={x0} x2={x1} y1={y} y2={y} stroke={color} strokeDasharray="5 4" />
      {label && <text className="ref-label" x={x1} y={y - 4} textAnchor="end" style={{ fill: color }}>{label}</text>}
    </g>
  );
}
