import { useMemo, useState, type MouseEvent as RMouseEvent } from "react";
import type { ModelEntry } from "../api/types";
import { fmtNum } from "../lib/format";
import { availableMetrics, MODEL_METRICS, modelPoints, modelsData, scoreRange, type ModelMetric, type ModelMetricDef, type ModelOrder, type ModelPoint } from "../lib/shellView";
import { niceTicks, svgPoint } from "../lib/zoom";
import { ChartFrame } from "./ChartFrame";
import { RefLine, tickDigits, useChartWidth, useTooltip } from "./charts";
import { Segmented } from "./ui";

const ORDERS: { value: ModelOrder; label: string }[] = [
  { value: "table", label: "Per modello" },
  { value: "value", label: "Dal migliore" },
];

/**
 * Forma e colore del punto: il migliore è un rombo più grande, i loop triangoli, gli altri cerchi. Verde, ciano e
 * indaco hanno quasi la stessa luminosità: la forma li distingue anche senza colori (e nel PNG in bianco e nero).
 */
type MarkKind = "best" | "loop" | "model";
const markKind = (p: Pick<ModelPoint, "best" | "loop">): MarkKind => (p.best ? "best" : p.loop ? "loop" : "model");
const MARK_COLOR: Record<MarkKind, string> = { best: "var(--ok)", loop: "var(--accent-2)", model: "var(--accent)" };

/** Rombo e triangolo un po' più grandi del cerchio di raggio `r`, per un'area simile (il rombo di più: è il migliore). */
function markPath(kind: Exclude<MarkKind, "model">, cx: number, cy: number, r: number): string {
  const f = (v: number) => Math.round(v * 10) / 10;
  if (kind === "best") {
    const d = r * 1.45;
    return `M${f(cx)} ${f(cy - d)}L${f(cx + d)} ${f(cy)}L${f(cx)} ${f(cy + d)}L${f(cx - d)} ${f(cy)}Z`;
  }
  const t = r * 1.3;
  return `M${f(cx)} ${f(cy - t)}L${f(cx + t * 0.95)} ${f(cy + t * 0.75)}L${f(cx - t * 0.95)} ${f(cy + t * 0.75)}Z`;
}

function Mark({ kind, cx, cy, r, ...paint }: { kind: MarkKind; cx: number; cy: number; r: number; fill: string; stroke: string; strokeWidth: number }) {
  return kind === "model"
    ? <circle data-mark={kind} cx={cx} cy={cy} r={r} {...paint} />
    : <path data-mark={kind} d={markPath(kind, cx, cy, r)} strokeLinejoin="round" {...paint} />;
}

/** Campione della legenda con la stessa forma del punto. */
function MarkSwatch({ kind }: { kind: MarkKind }) {
  return (
    <svg width="13" height="13" viewBox="-6.5 -6.5 13 13" aria-hidden="true" style={{ marginRight: 6, verticalAlign: -2 }}>
      <Mark kind={kind} cx={0} cy={kind === "loop" ? 0.6 : 0} r={kind === "best" ? 4 : 4.4} fill={MARK_COLOR[kind]} stroke="var(--panel)" strokeWidth={1} />
    </svg>
  );
}

/**
 * Punteggi dei modelli di un job (DOPE, zDOPE, GA341, molpdf, voto): un punto per modello, il migliore un rombo verde,
 * i loop triangoli, quello mostrato nel 3D cerchiato; clic su un punto (o su una riga dei dati) per sceglierlo. In
 * ordine di tabella o dal migliore al peggiore; la vista espansa è più alta, con tutte le etichette dei modelli.
 */
export function ModelScores({ models, selected, onSelect, filename }: {
  models: ModelEntry[];
  selected: string;
  onSelect: (file: string) => void;
  filename: string;
}) {
  const metrics = useMemo(() => availableMetrics(models), [models]);
  const [metric, setMetric] = useState<ModelMetric>("dope");
  const [order, setOrder] = useState<ModelOrder>("table");
  const current = metrics.includes(metric) ? metric : metrics[0];
  const points = useMemo(() => (current ? modelPoints(models, current, order) : []), [models, current, order]);
  if (!current || models.length < 2) return null;
  const def = MODEL_METRICS[current];
  const pick = (file: string) => {
    if (models.some((m) => m.file === file && !m.failure)) onSelect(file);
  };
  const hasLoop = models.some((m) => m.kind === "loop");
  // righe dei dati nell'ordine della tabella dei modelli (modelsData)
  const selectedRow = models.findIndex((m) => m.file === selected);
  return (
    <ChartFrame
      title="Punteggi dei modelli"
      subtitle={`${def.label}: ${def.note}`}
      filename={`${filename}_${current}`}
      controls={
        <>
          <Segmented label="Metrica" value={current} onChange={setMetric} options={metrics.map((m) => ({ value: m, label: MODEL_METRICS[m].label }))} />
          <Segmented label="Ordine" value={order} onChange={setOrder} options={ORDERS} />
        </>
      }
      data={() => modelsData(models)}
      onRowClick={(row) => pick(String(row.file))}
      selectedIndex={selectedRow >= 0 ? selectedRow : undefined}
      hint="Clic su un punto per mostrare quel modello nel 3D"
      legend={
        <div className="legend">
          <span><MarkSwatch kind="best" />migliore (DOPE più basso)</span>
          <span><MarkSwatch kind="model" />altri modelli</span>
          {hasLoop && <span><MarkSwatch kind="loop" />loop</span>}
          <span><i style={{ background: "transparent", boxShadow: "inset 0 0 0 2px var(--text)" }} />mostrato nel 3D: {selected}</span>
          <span>{def.better === "low" ? "più in basso è meglio" : "più in alto è meglio"}</span>
        </div>
      }
    >
      {({ expanded }) => <ScoresSvg expanded={expanded} points={points} def={def} selected={selected} onPick={pick} />}
    </ChartFrame>
  );
}

/** SVG dei punteggi (uno per vista: pagina ed espansa); ordine, metrica e selezione arrivano dal componente esterno. */
function ScoresSvg({ expanded, points, def, selected, onPick }: {
  expanded: boolean;
  points: ModelPoint[];
  def: ModelMetricDef;
  selected: string;
  onPick: (file: string) => void;
}) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const { ref, width: W } = useChartWidth(640);
  const H = expanded ? 460 : 230;
  const pad = { l: expanded ? 70 : 60, r: 18, t: 16, b: 30 };
  const bottom = H - pad.b;
  const n = points.length;
  const values = points.flatMap((p) => (p.value === null ? [] : [p.value]));
  const { min, max } = scoreRange(values, def.references.map((r) => r.value), def.domain);
  const step = (W - pad.l - pad.r) / Math.max(1, n);
  const x = (i: number) => pad.l + (i + 0.5) * step;
  const y = (v: number) => pad.t + (1 - (v - min) / (max - min || 1)) * (bottom - pad.t);
  const ticks = niceTicks(min, max, expanded ? 8 : 4);
  const digits = Math.max(tickDigits(ticks), 0);
  const every = Math.max(1, Math.ceil(26 / step));
  const radius = expanded ? 6 : 5;
  const indexAt = (e: RMouseEvent<SVGSVGElement>) => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    if (p.x < pad.l || p.x > W - pad.r || !n) return null;
    return Math.max(0, Math.min(n - 1, Math.floor((p.x - pad.l) / step)));
  };
  return (
    <div ref={ref}>
      <svg
        className="chart shell-scores"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${def.label} di ${n} modelli`}
        onMouseMove={(e) => {
          const i = indexAt(e);
          if (i === null) {
            setHover(null);
            return tip.hide();
          }
          setHover(i);
          const p = points[i];
          tip.show(e, (
            <>
              <b>Modello {p.n}{p.best ? " · migliore" : ""}</b>
              <div className="mono">{p.file}</div>
              <div>{def.label}: {p.value === null ? (p.failed ? "non riuscito" : "—") : fmtNum(p.value, def.digits)}</div>
            </>
          ));
        }}
        onMouseLeave={() => {
          setHover(null);
          tip.hide();
        }}
        onClick={(e) => {
          const i = indexAt(e);
          if (i !== null) onPick(points[i].file);
        }}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
            <text x={pad.l - 6} y={y(v) + 3.5} textAnchor="end">{fmtNum(v, digits)}</text>
          </g>
        ))}
        {expanded && (
          <text className="axis-title" x={14} y={(pad.t + bottom) / 2} textAnchor="middle" transform={`rotate(-90 14 ${(pad.t + bottom) / 2})`}>{def.label}</text>
        )}
        {hover !== null && <rect x={x(hover) - step / 2} y={pad.t} width={step} height={bottom - pad.t} fill="var(--panel-3)" />}
        <line x1={pad.l} x2={W - pad.r} y1={bottom} y2={bottom} stroke="var(--chart-axis)" />
        {points.map((p, i) => (
          <g key={p.file}>
            {i % every === 0 && (
              <>
                <line x1={x(i)} x2={x(i)} y1={bottom} y2={bottom + 4} stroke="var(--chart-axis)" />
                <text x={x(i)} y={H - 9} textAnchor="middle">{p.n}</text>
              </>
            )}
            {p.value === null ? (
              <text x={x(i)} y={bottom - 6} textAnchor="middle" style={{ fill: "var(--err)" }}>×</text>
            ) : (
              <Mark kind={markKind(p)} cx={x(i)} cy={y(p.value)} r={p.file === selected ? radius + 1.5 : radius} fill={MARK_COLOR[markKind(p)]}
                stroke={p.file === selected ? "var(--text)" : "var(--panel)"} strokeWidth={p.file === selected ? 2.5 : 1.2} />
            )}
          </g>
        ))}
        {/* soglie dopo i punti: l'etichetta con l'alone non resta coperta */}
        {def.references.filter((r) => r.value >= min && r.value <= max).map((r) => (
          <RefLine key={r.value} y={y(r.value)} x0={pad.l} x1={W - pad.r} label={r.label} />
        ))}
      </svg>
      {tip.node}
    </div>
  );
}
