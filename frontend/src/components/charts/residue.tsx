import { useId, useMemo, useRef, useState, type MouseEvent as RMouseEvent } from "react";
import type { ResidueRow } from "../../api/types";
import { colorFor } from "../../lib/colorscale";
import type { ChartData } from "../../lib/exportImage";
import { fmtNum } from "../../lib/format";
import { RAMA_SHORT_LABEL, type RamaRegion } from "../../lib/ramachandran";
import { describeResidue, hasMissing, type ResidueColoring } from "../../lib/residueColoring";
import { residueName, sameResidue, SS3_LABEL } from "../../lib/residues";
import { niceTicks, svgPoint, useDomain, useZoomAxis, visibleIndices, type DomainState } from "../../lib/zoom";
import { ChartFrame, ZoomBar } from "../ChartFrame";
import { RefLine, useChartWidth, useFollowSelection, useTooltip, ZOOM_HINT } from "./common";
import { firstResnum, inView, manyChains, residueLabel, residueSpan, residueTicks, runs, tickDigits } from "./linearHelpers";
import { ScaleLegend } from "./ScaleLegend";

// ---------------------------------------------------------------------------------- profili per residuo
const SS_COLOR: Record<string, string> = { H: "var(--helix)", E: "var(--strand)", C: "var(--coil)" };

export function SSStrip({ ss3 }: { ss3: string }) {
  const segs: { c: string; n: number }[] = [];
  for (const c of ss3) {
    const last = segs[segs.length - 1];
    if (last && last.c === c) last.n++;
    else segs.push({ c, n: 1 });
  }
  return (
    <div className="ss-strip" title="Struttura secondaria: elica (rosa), filamento β (giallo), coil (grigio)">
      {segs.map((s, i) => (
        <div key={i} style={{ flex: s.n, background: SS_COLOR[s.c] ?? "var(--coil)", opacity: s.c === "C" ? 0.45 : 1 }} />
      ))}
    </div>
  );
}

type Metric = "dope" | "bfactor" | "rsa" | "plddt";
const METRIC_INFO: Record<Metric, { label: string; short: string; unit: string; hint: string }> = {
  dope: { label: "Energia DOPE per residuo", short: "DOPE", unit: "", hint: "Media mobile calcolata da MODELLER (finestra 15): valori più alti (meno negativi) indicano regioni potenzialmente mal modellate." },
  bfactor: { label: "B-factor medio per residuo", short: "B-factor", unit: "Å²", hint: "Nei modelli MODELLER il B-factor non ha significato sperimentale." },
  rsa: { label: "Accessibilità relativa al solvente", short: "RSA", unit: "", hint: "RSA < 0,2 = residuo sepolto." },
  plddt: { label: "pLDDT per residuo (modello predetto)", short: "pLDDT", unit: "", hint: "Confidenza del predittore (AlphaFold, ESMFold) nella colonna B-factor: > 90 molto alta, 70–90 alta, 50–70 bassa, < 50 molto bassa." },
};

/** ampiezza minima dello zoom (residui) e ampiezza sotto la quale la linea mostra anche i punti */
const MIN_SPAN = 10;
const DOTS_BELOW = 80;

interface ProfileProps {
  residues: ResidueRow[];
  metric?: Metric;
  /** serie personalizzata (stesso ordine dei residui): sostituisce la metrica */
  values?: (number | null)[];
  label?: string;
  hint?: string;
  height?: number;
  /** residuo selezionato (linea verticale) */
  selected?: { chain: string; resnum: string } | null;
  /** linea orizzontale di riferimento (es. soglia) */
  threshold?: { value: number; label: string } | null;
  onSelect?: (r: ResidueRow) => void;
  /** nome dei file PNG e CSV (predefinito: dalla metrica o dall'etichetta) */
  filename?: string;
}

/**
 * Profilo di un valore lungo la sequenza, con la struttura secondaria allineata sopra. L'asse dei residui si ingrandisce
 * (trascinamento, rotella, barra dello zoom) ed è condiviso tra la pagina e la vista espansa; la tabella dei dati
 * seleziona il residuo. Senza valori resta il nome del grafico con il motivo (per DOPE: come ottenerlo).
 */
export function ResidueProfile({ residues, metric = "dope", values: custom, label, hint, height = 200, selected, threshold, onSelect, filename }: ProfileProps) {
  const n = residues.length;
  // ogni residuo occupa da i − 0,5 a i + 0,5: il primo e l'ultimo punto non cadono sul bordo del ritaglio
  const zoom = useDomain(-0.5, Math.max(0.5, n - 0.5), MIN_SPAN);
  const values = useMemo(() => custom ?? residues.map((r) => (metric === "plddt" ? r.bfactor : r[metric])), [custom, residues, metric]);
  const reference = threshold?.value;
  const limits = useMemo(() => {
    const lim = values.filter((v): v is number => v !== null && v !== undefined);
    if (!lim.length) return null;
    if (reference !== undefined) lim.push(reference);
    return { min: Math.min(...lim), max: Math.max(...lim) };
  }, [values, reference]);
  const ss = useMemo(() => runs(residues.map((r) => r.ss3)), [residues]);
  const multi = useMemo(() => manyChains(residues), [residues]);
  const first = useMemo(() => firstResnum(residues) ?? 1, [residues]);
  const selIndex = residues.findIndex((r) => sameResidue(selected, r));
  useFollowSelection(zoom, selIndex);
  const info = { ...METRIC_INFO[metric], ...(label ? { label, short: label, unit: "" } : {}), ...(hint !== undefined ? { hint } : {}) };
  if (!limits) {
    return (
      <p className="muted small" role="note">
        <b className="text-2">{info.label}</b>:{" "}
        {metric === "dope" && !custom
          ? "non calcolato. Attiva «Calcola il profilo DOPE (MODELLER)» e rilancia l'analisi."
          : "dato non disponibile per questa struttura."}
      </p>
    );
  }
  const data = (): ChartData => ({
    columns: [
      { key: "residuo", label: "Residuo" },
      { key: "catena", label: "Catena" },
      { key: "numero", label: "Numero" },
      { key: "nome", label: "Nome" },
      { key: "dssp", label: "DSSP" },
      { key: "ss", label: "Struttura secondaria" },
      { key: "valore", label: info.unit ? `${info.short} (${info.unit})` : info.short, num: true },
    ],
    rows: residues.map((r, i) => ({
      residuo: residueName(r), catena: r.chain, numero: r.resnum, nome: r.resname, dssp: r.ss, ss: SS3_LABEL[r.ss3] ?? r.ss3, valore: values[i] ?? null,
    })),
  });
  return (
    <ChartFrame
      title={info.label}
      subtitle={info.hint || undefined}
      filename={filename ?? `profilo_${label ?? metric}`}
      controls={<ZoomBar zoom={zoom} format={(lo, hi) => residueSpan(residues, { lo, hi }, multi)} />}
      hint={ZOOM_HINT}
      data={data}
      onRowClick={onSelect ? (_row, i) => onSelect(residues[i]) : undefined}
      selectedIndex={selIndex >= 0 ? selIndex : undefined}
    >
      {({ expanded }) => (
        <ProfileSvg expanded={expanded} title={info.label} residues={residues} values={values} ss={ss} zoom={zoom} min={limits.min} max={limits.max}
          height={expanded ? Math.max(420, height * 2) : height} threshold={threshold ?? null} selIndex={selIndex} multi={multi}
          first={first} short={info.short} unit={info.unit} onSelect={onSelect} />
      )}
    </ChartFrame>
  );
}

/** SVG del profilo (uno per vista: pagina ed espansa); lo zoom arriva dal componente esterno. */
function ProfileSvg({ expanded, title, residues, values, ss, zoom, min, max, height: H, threshold, selIndex, multi, first, short, unit, onSelect }: {
  expanded: boolean;
  title: string;
  residues: ResidueRow[];
  values: (number | null)[];
  ss: { start: number; end: number; value: string }[];
  zoom: DomainState;
  min: number;
  max: number;
  height: number;
  threshold: { value: number; label: string } | null;
  selIndex: number;
  multi: boolean;
  /** numero del primo residuo, per le tacche tonde dell'asse */
  first: number;
  short: string;
  unit: string;
  onSelect?: (r: ResidueRow) => void;
}) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const clip = `profile-clip${useId().replace(/[^\w-]/g, "")}`;
  const { ref, width: W } = useChartWidth(900);
  const pad = { l: expanded ? 64 : 52, r: 14, t: 22, b: 24 };
  const strip = { y: 4, h: expanded ? 10 : 8 };
  const bottom = H - pad.b;
  const axis = useZoomAxis({ zoom, range: [pad.l, W - pad.r], wheel: expanded, area: { y0: strip.y, y1: bottom } });
  const n = residues.length;
  const span = max - min || 1;
  const y = (v: number) => pad.t + (1 - (v - min) / span) * (bottom - pad.t);
  const { domain } = zoom;
  const [from, to] = visibleIndices(domain, n);
  const path = useMemo(() => {
    let d = "";
    let pen = false;
    for (let i = from; i <= to; i++) {
      const v = values[i];
      if (v === null || v === undefined) {
        pen = false;
        continue;
      }
      d += `${pen ? "L" : "M"}${axis.scale(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    }
    return d;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values, from, to, domain.lo, domain.hi, W, H, min, max, expanded]);
  const yTicks = niceTicks(min, max, expanded ? 8 : 4, [1, 2, 5, 10]);
  const digits = tickDigits(yTicks);
  const xTicks = residueTicks(domain, Math.max(3, Math.floor((W - pad.l - pad.r) / 80)), n, first);
  const dots = domain.hi - domain.lo < DOTS_BELOW;
  const indexAt = (e: RMouseEvent<SVGSVGElement>) => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    if (p.x < pad.l - 4 || p.x > W - pad.r + 4 || !n) return null;
    return Math.max(0, Math.min(n - 1, Math.round(axis.invert(p.x))));
  };
  const vline = (i: number) => ({ x1: axis.scale(i), x2: axis.scale(i), y1: pad.t, y2: bottom });
  const hv = hover === null ? null : values[hover];
  return (
    <div ref={ref}>
      <svg
        ref={axis.svgRef}
        className="chart zoomable"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${title}: ${n} residui`}
        {...axis.handlers}
        onMouseMove={(e) => {
          const i = axis.brush ? null : indexAt(e);
          if (i === null) {
            setHover(null);
            return tip.hide();
          }
          setHover(i);
          const r = residues[i];
          tip.show(e, (
            <>
              <b>{residueName(r)}</b>
              <div>{short}: {fmtNum(values[i], 3)}{unit ? ` ${unit}` : ""}</div>
              <div className="text-2">DSSP {r.ss || "—"} · Ramachandran {r.rama ? RAMA_SHORT_LABEL[r.rama as RamaRegion] ?? r.rama : "—"}</div>
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
          if (i !== null) onSelect?.(residues[i]);
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
            <text x={pad.l - 6} y={y(v) + 3.5} textAnchor="end">{fmtNum(v, digits)}</text>
          </g>
        ))}
        {expanded && (
          <text className="axis-title" x={14} y={(pad.t + bottom) / 2} textAnchor="middle" transform={`rotate(-90 14 ${(pad.t + bottom) / 2})`}>
            {unit ? `${short} (${unit})` : short}
          </text>
        )}
        <text x={pad.l - 6} y={strip.y + strip.h - 1} textAnchor="end">SS</text>
        <line x1={pad.l} x2={W - pad.r} y1={bottom} y2={bottom} stroke="var(--chart-axis)" />
        {xTicks.map((i) => (
          <g key={i}>
            <line x1={axis.scale(i)} x2={axis.scale(i)} y1={bottom} y2={bottom + 4} stroke="var(--chart-axis)" />
            <text x={axis.scale(i)} y={H - 7} textAnchor="middle">{residueLabel(residues[i], multi)}</text>
          </g>
        ))}
        <g clipPath={`url(#${clip})`}>
          {ss.filter((s) => inView(s, domain)).map((s) => (
            <rect key={s.start} x={axis.scale(s.start - 0.5)} y={strip.y} width={Math.max(1, axis.scale(s.end + 0.5) - axis.scale(s.start - 0.5))} height={strip.h}
              fill={SS_COLOR[s.value] ?? "var(--coil)"} opacity={s.value === "C" ? 0.45 : 1} />
          ))}
          <path d={path} fill="none" stroke="var(--accent)" strokeWidth={expanded ? 1.8 : 1.6} strokeLinejoin="round" />
          {dots && Array.from({ length: to - from + 1 }, (_, k) => {
            const v = values[from + k];
            return v === null || v === undefined ? null : <circle key={from + k} cx={axis.scale(from + k)} cy={y(v)} r={expanded ? 3 : 2.4} fill="var(--accent)" />;
          })}
          {selIndex >= 0 && <line {...vline(selIndex)} stroke="var(--warn)" strokeWidth={2} />}
          {hover !== null && <line {...vline(hover)} stroke="var(--text-2)" strokeDasharray="3 3" />}
          {hover !== null && hv !== null && hv !== undefined && (
            <circle cx={axis.scale(hover)} cy={y(hv)} r={4} fill="var(--accent)" stroke="var(--panel)" strokeWidth={1.5} />
          )}
        </g>
        {/* soglia dopo i dati: l'etichetta con l'alone resta leggibile sopra la linea del profilo */}
        {threshold && <RefLine y={y(threshold.value)} x0={pad.l} x1={W - pad.r} label={threshold.label} />}
        {axis.brushRect && <rect className="brush" x={axis.brushRect.x} y={pad.t} width={axis.brushRect.width} height={bottom - pad.t} />}
      </svg>
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------------------------- striscia colorata per residuo
/**
 * Striscia con un colore per residuo (la colorazione del 3D), per scegliere un residuo con il mouse. Le celle si
 * disegnano una volta per colorazione: il suggerimento e il clic si risolvono sul contenitore (`data-i`), la selezione
 * è un riquadro sopra le celle, così il movimento del mouse non ridisegna migliaia di celle. `legend` aggiunge sotto la
 * legenda della scala (dove accanto non c'è già quella del visualizzatore 3D).
 */
export function ResidueStrip({ residues, coloring, selected, onSelect, legend = false }: {
  residues: ResidueRow[];
  coloring: ResidueColoring;
  selected?: { chain: string; resnum: string } | null;
  onSelect?: (r: ResidueRow) => void;
  legend?: boolean;
}) {
  const tip = useTooltip();
  // residuo del suggerimento e celle con cui è stato scritto (con un'altra colorazione il testo va riscritto)
  const last = useRef<{ i: number; cells: unknown } | null>(null);
  const n = residues.length;
  const cells = useMemo(() => {
    const byKey = new Map(coloring.values.map((v) => [`${v.chain}:${v.resnum}`, v]));
    return residues.map((r, i) => {
      const v = byKey.get(`${r.chain}:${r.resnum}`);
      return <div key={`${r.chain}${r.resnum}`} data-i={i} style={{ background: v?.color ?? colorFor(coloring.scale, v?.value ?? null) }} />;
    });
  }, [residues, coloring]);
  const selIndex = residues.findIndex((r) => sameResidue(selected, r));
  const indexOf = (e: RMouseEvent<HTMLDivElement>) => {
    const i = Number((e.target as HTMLElement).dataset?.i);
    return Number.isInteger(i) && i >= 0 && i < n ? i : -1;
  };
  return (
    <div>
      <div
        className="res-strip"
        onMouseMove={(e) => {
          const i = indexOf(e);
          if (i < 0) return;
          // stesso residuo: il suggerimento segue il puntatore senza ridisegnare la striscia
          if (last.current?.i === i && last.current.cells === cells) return tip.move(e);
          last.current = { i, cells };
          const r = residues[i];
          tip.show(e, <><b>{residueName(r)}</b><div>{describeResidue(coloring, r)}</div></>);
        }}
        onMouseLeave={() => {
          last.current = null;
          tip.hide();
        }}
        onClick={(e) => {
          const i = indexOf(e);
          if (i >= 0) onSelect?.(residues[i]);
        }}
      >
        {cells}
        {selIndex >= 0 && <div className="res-strip-sel" style={{ left: `${(selIndex / n) * 100}%`, width: `${100 / n}%` }} />}
      </div>
      {tip.node}
      {legend && <ScaleLegend scale={coloring.scale} title={coloring.title} noValue={hasMissing(coloring)} extra={coloring.legendExtra} />}
    </div>
  );
}
