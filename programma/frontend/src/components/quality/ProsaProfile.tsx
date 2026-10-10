import { useId, useMemo, useState, type MouseEvent as RMouseEvent } from "react";
import type { ProsaResult, ResidueRow } from "../../api/types";
import { fmtNum } from "../../lib/format";
import { nearestPoint, positiveRuns, prosaData, prosaPoints, prosaResidues, type ProsaPoint } from "../../lib/qualityView";
import type { ResidueRef } from "../../lib/residueColoring";
import { niceTicks, svgPoint, useDomain, useZoomAxis, type DomainState } from "../../lib/zoom";
import { ChartFrame, ZoomBar } from "../ChartFrame";
import { integerTicks, manyChains, residueLabel, residueName, tickDigits, useChartWidth, useFollowSelection, useTooltip, ZOOM_HINT } from "../charts";

/** ampiezza minima dello zoom (residui) e margine attorno a un tratto scelto */
const MIN_SPAN = 10;
const RUN_MARGIN = 8;

/**
 * Profilo di energia ProSA (finestra 10): i tratti sopra lo zero sono potenzialmente problematici. L'indice del
 * profilo è sequenziale (1…N): se è attribuibile ai residui del rapporto (prosaResidues), assi, suggerimenti e clic
 * usano i residui. Lo zoom è condiviso tra la pagina e la vista espansa.
 */
export function ProsaProfile({ profile, residues, selected, onSelect }: {
  profile: ProsaResult["profile"];
  residues?: ResidueRow[];
  selected?: ResidueRef | null;
  onSelect?: (r: ResidueRow) => void;
}) {
  const pts = useMemo(() => prosaPoints(profile), [profile]);
  const named = useMemo(() => prosaResidues(profile, residues), [profile, residues]);
  const first = pts.length ? pts[0].index : 0;
  const last = pts.length ? pts[pts.length - 1].index : 0;
  // mezzo residuo di margine per lato, come gli altri profili: il primo e l'ultimo punto non finiscono sul bordo del clip
  const zoom = useDomain(first - 0.5, last + 0.5, MIN_SPAN);
  const limits = useMemo(() => {
    let min = -2;
    let max = 1;
    for (const p of pts) {
      min = Math.min(min, p.energy_w10);
      max = Math.max(max, p.energy_w10);
    }
    return { min, max };
  }, [pts]);
  const stretches = useMemo(() => positiveRuns(pts), [pts]);
  const multi = useMemo(() => (named ? manyChains(named) : false), [named]);
  // indice del profilo del residuo scelto (1…N); 0 = nessuno
  const selIndex = named && selected ? named.findIndex((r) => r.chain === selected.chain && r.resnum === selected.resnum) + 1 : 0;
  useFollowSelection(zoom, selIndex > 0 ? selIndex : -1);
  // riga del residuo scelto nella scheda "Dati" (le righe seguono l'ordine del profilo)
  const selRow = selIndex > 0 ? profile.findIndex((p) => p.index === selIndex) : -1;
  if (pts.length < 2) return null;
  const label = (i: number) => (named?.[i - 1] ? residueLabel(named[i - 1], multi) : String(i));
  // stesso formato dei profili per residuo (residueSpan): "residui 12–48 (300 in tutto)"
  const span = (lo: number, hi: number) => `residui ${label(Math.max(first, Math.ceil(lo)))}–${label(Math.min(last, Math.floor(hi)))} (${profile.length} in tutto)`;
  return (
    <div className="stack-sm">
      <ChartFrame
        title="Profilo di energia ProSA (finestra 10)"
        subtitle="Energia media su 10 residui: i tratti sopra lo zero sono potenzialmente problematici (Wiederstein & Sippl 2007)."
        filename="prosa_profilo_energia"
        controls={<ZoomBar zoom={zoom} format={span} />}
        hint={ZOOM_HINT}
        data={() => prosaData(profile, named)}
        onRowClick={named && onSelect ? (_row, i) => {
          const r = named[profile[i].index - 1];
          if (r) onSelect(r);
        } : undefined}
        selectedIndex={selRow >= 0 ? selRow : undefined}
        legend={(
          <div className="legend">
            <span><i style={{ background: "var(--accent)", height: 3, verticalAlign: 3 }} />Energia (finestra 10)</span>
            <span><i style={{ background: "var(--err)", borderRadius: "50%" }} />Sopra lo zero</span>
            <span><i style={{ background: "var(--err-soft)", boxShadow: "inset 0 0 0 1px var(--err)" }} />Zona potenzialmente problematica</span>
            {selIndex > 0 && <span><i style={{ background: "var(--warn)", width: 3 }} />Residuo selezionato</span>}
          </div>
        )}
      >
        {({ expanded }) => (
          <ProsaSvg expanded={expanded} pts={pts} zoom={zoom} min={limits.min} max={limits.max} named={named} label={label}
            selIndex={selIndex} onSelect={onSelect} />
        )}
      </ChartFrame>
      {stretches.length > 0 && (
        <div className="row small">
          <span className="text-2">Tratti sopra lo zero ({stretches.length}):</span>
          {stretches.map((s) => (
            <button key={s.start} type="button" className="chip" title="Ingrandisci il tratto nel grafico"
              onClick={() => zoom.set({ lo: s.start - RUN_MARGIN, hi: s.end + RUN_MARGIN })}>
              {s.start === s.end ? label(s.start) : `${label(s.start)}–${label(s.end)}`}
              <span className="text-2">max {fmtNum(s.max, 2)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** SVG del profilo (uno per vista: pagina ed espansa); lo zoom arriva dal componente esterno. */
function ProsaSvg({ expanded, pts, zoom, min, max, named, label, selIndex, onSelect }: {
  expanded: boolean;
  pts: ProsaPoint[];
  zoom: DomainState;
  min: number;
  max: number;
  named: ResidueRow[] | null;
  label: (index: number) => string;
  selIndex: number;
  onSelect?: (r: ResidueRow) => void;
}) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const clip = `prosa-clip${useId().replace(/[^\w-]/g, "")}`;
  const { ref, width: W } = useChartWidth(560);
  const H = expanded ? 420 : 170;
  const pad = { l: expanded ? 58 : 46, r: 14, t: 12, b: 26 };
  const bottom = H - pad.b;
  const axis = useZoomAxis({ zoom, range: [pad.l, W - pad.r], wheel: expanded, area: { y0: pad.t, y1: bottom } });
  const span = max - min || 1;
  const y = (v: number) => pad.t + (1 - (v - min) / span) * (bottom - pad.t);
  const { lo, hi } = zoom.domain;
  const path = useMemo(() => {
    let d = "";
    let prev: number | null = null;
    for (const p of pts) {
      // un punto oltre i bordi per lato: la linea esce dall'area visibile invece di fermarsi prima
      if (p.index < lo - 1 || p.index > hi + 1) {
        prev = null;
        continue;
      }
      d += `${prev !== null && p.index === prev + 1 ? "L" : "M"}${axis.scale(p.index).toFixed(1)},${y(p.energy_w10).toFixed(1)}`;
      prev = p.index;
    }
    return d;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- axis e y derivano da W, H, min/max, dominio ed expanded, già nelle dipendenze
  }, [pts, lo, hi, W, H, min, max, expanded]);
  const above = pts.filter((p) => p.energy_w10 > 0 && p.index >= lo - 1 && p.index <= hi + 1);
  const yTicks = niceTicks(min, max, expanded ? 8 : 4, [1, 2, 5, 10]);
  const digits = tickDigits(yTicks);
  const xTicks = integerTicks(lo, hi, Math.max(3, Math.floor((W - pad.l - pad.r) / 80)));
  const pointAt = (e: RMouseEvent<SVGSVGElement>) => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    if (p.x < pad.l - 4 || p.x > W - pad.r + 4) return null;
    const k = nearestPoint(pts, axis.invert(p.x));
    return k < 0 ? null : k;
  };
  const vline = (index: number) => ({ x1: axis.scale(index), x2: axis.scale(index), y1: pad.t, y2: bottom });
  const hp = hover === null ? null : pts[hover];
  return (
    <div ref={ref}>
      <svg
        ref={axis.svgRef}
        className="chart zoomable"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Profilo di energia ProSA: ${pts.length} valori, ${above.length} sopra lo zero nell'intervallo visibile`}
        {...axis.handlers}
        onMouseMove={(e) => {
          const k = axis.brush ? null : pointAt(e);
          if (k === null) {
            setHover(null);
            return tip.hide();
          }
          setHover(k);
          const p = pts[k];
          const r = named?.[p.index - 1];
          tip.show(e, (
            <>
              <b>{r ? residueName(r) : `Residuo ${p.index}`}</b>
              <div>Finestra 10: {fmtNum(p.energy_w10, 3)}</div>
              <div className="text-2">Finestra 1: {fmtNum(p.energy, 3)} · indice {p.index}</div>
              {p.energy_w10 > 0 && <div style={{ color: "var(--err)" }}>Sopra lo zero: tratto potenzialmente problematico</div>}
            </>
          ));
        }}
        onMouseLeave={() => {
          setHover(null);
          tip.hide();
        }}
        onClick={(e) => {
          if (axis.consumeClick()) return;
          const k = pointAt(e);
          const r = k === null ? undefined : named?.[pts[k].index - 1];
          if (r) onSelect?.(r);
        }}
      >
        <defs>
          <clipPath id={clip}>
            <rect x={pad.l} y={0} width={Math.max(0, W - pad.l - pad.r)} height={H} />
          </clipPath>
        </defs>
        <rect x={pad.l} y={pad.t} width={Math.max(0, W - pad.l - pad.r)} height={Math.max(0, y(0) - pad.t)} fill="var(--err-soft)" />
        {yTicks.map((v) => (
          <g key={v}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
            <text x={pad.l - 6} y={y(v) + 3.5} textAnchor="end">{fmtNum(v, digits)}</text>
          </g>
        ))}
        {expanded && (
          <text className="axis-title" x={14} y={(pad.t + bottom) / 2} textAnchor="middle" transform={`rotate(-90 14 ${(pad.t + bottom) / 2})`}>
            Energia ProSA (finestra 10)
          </text>
        )}
        <line x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} stroke="var(--err)" strokeDasharray="5 4" />
        <line x1={pad.l} x2={W - pad.r} y1={bottom} y2={bottom} stroke="var(--chart-axis)" />
        {xTicks.map((i) => (
          <g key={i}>
            <line x1={axis.scale(i)} x2={axis.scale(i)} y1={bottom} y2={bottom + 4} stroke="var(--chart-axis)" />
            <text x={axis.scale(i)} y={H - 8} textAnchor="middle">{label(i)}</text>
          </g>
        ))}
        <g clipPath={`url(#${clip})`}>
          <path d={path} fill="none" stroke="var(--accent)" strokeWidth={expanded ? 1.8 : 1.6} strokeLinejoin="round" />
          {above.map((p) => <circle key={p.index} cx={axis.scale(p.index)} cy={y(p.energy_w10)} r={expanded ? 3 : 2.2} fill="var(--err)" />)}
          {selIndex > 0 && <line {...vline(selIndex)} stroke="var(--warn)" strokeWidth={2} />}
          {hp && <line {...vline(hp.index)} stroke="var(--text-2)" strokeDasharray="3 3" />}
          {hp && <circle cx={axis.scale(hp.index)} cy={y(hp.energy_w10)} r={4} fill={hp.energy_w10 > 0 ? "var(--err)" : "var(--accent)"} stroke="var(--panel)" strokeWidth={1.5} />}
        </g>
        {axis.brushRect && <rect className="brush" x={axis.brushRect.x} y={pad.t} width={axis.brushRect.width} height={bottom - pad.t} />}
      </svg>
      {tip.node}
    </div>
  );
}
