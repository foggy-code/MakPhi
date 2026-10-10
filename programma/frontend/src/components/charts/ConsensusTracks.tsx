import { useId, useMemo, useState, type MouseEvent as RMouseEvent } from "react";
import type { ConsensusDisorder, ConsensusPredictor, ConsensusRegion, ConsensusResult, ResidueRow } from "../../api/types";
import { colorFor, themedColor } from "../../lib/colorscale";
import type { ChartData } from "../../lib/exportImage";
import { fmtNum } from "../../lib/format";
import { CONSENSUS_SCALE } from "../../lib/residueColoring";
import { useDocumentTheme } from "../../lib/theme";
import { svgPoint, useDomain, useZoomAxis, type DomainState } from "../../lib/zoom";
import { ChartFrame, ZoomBar } from "../ChartFrame";
import { useChartWidth, useFollowSelection, useTooltip, ZOOM_HINT } from "./common";
import { firstResnum, inView, manyChains, residueLabel, residueName, residueSpan, residueTicks, runs } from "./linearHelpers";

// ---------------------------------------------------------------------------------- consenso per residuo
// colori per tema (styles.css, --sig-*): ≥ 3:1 sui pannelli anche nel tema chiaro (lib/chartTokens.test.ts)
const SIGNAL_COLOR: Record<string, string> = {
  rama: "var(--sig-rama)", clash: "var(--sig-clash)", dope: "var(--sig-dope)", qmean: "var(--sig-qmean)", prosa: "var(--sig-prosa)",
};
const DISORDER_COLOR = "var(--sig-disorder)";
const PREDICTOR_COLOR = "var(--sig-predictor)"; // F9.3: diverge da una predizione sicura (informativo)
const MIN_SPAN = 10;
/** campione di una regione attesa (disordine predetto): vuota e tratteggiata come nel grafico, non solo un altro colore */
const EXPECTED_SWATCH = { background: "var(--warn-soft)", border: "1.5px dashed var(--warn)" };

type Span = { start: number; end: number };

/** Righe del grafico come tratti consecutivi: pochi rettangoli anche con migliaia di residui (e niente fessure). */
interface Lanes {
  signals: { key: string; label: string; rule: string; flagged: number; spans: Span[] }[];
  counts: { start: number; end: number; value: number }[];
  maxCount: number;
  /** disordine predetto (F5.5): riga informativa, fuori dal conteggio dei metodi */
  disorder: ConsensusDisorder | null;
  /** accordo con una predizione esterna (F9.3): riga informativa, solo con il confronto incluso */
  predictor: ConsensusPredictor | null;
  predictorSpans: Span[];
}

function lanes(consensus: ConsensusResult, n: number): Lanes {
  const per = consensus.per_residue;
  const flagged = (flags: boolean[]) => runs(flags).filter((r) => r.value);
  const predictor = consensus.predictor && consensus.predictor.flags.length === n ? consensus.predictor : null;
  return {
    signals: per.signals.map((s) => ({ ...s, spans: flagged(per.flags.map((f) => f.includes(s.key))) })),
    counts: runs(per.counts).filter((r) => r.value > 0),
    maxCount: Math.max(3, ...per.counts),
    disorder: consensus.disorder && consensus.disorder.flags.length === n ? consensus.disorder : null,
    predictor,
    predictorSpans: predictor ? flagged(predictor.flags) : [],
  };
}

/** Segnalazioni dei metodi sovrapposte sullo stesso asse dei residui, numero di metodi e regioni problematiche. */
export function ConsensusTracks({ residues, consensus, selected, onSelect, onRegion }: {
  residues: ResidueRow[];
  consensus: ConsensusResult;
  selected?: { chain: string; resnum: string } | null;
  onSelect?: (r: ResidueRow) => void;
  onRegion?: (region: ConsensusRegion) => void;
}) {
  const per = consensus.per_residue;
  const n = residues.length;
  const available = n > 0 && per.flags.length === n;
  const zoom = useDomain(-0.5, n - 0.5, MIN_SPAN);
  const tracks = useMemo(() => (available ? lanes(consensus, n) : null), [available, consensus, n]);
  const multi = useMemo(() => manyChains(residues), [residues]);
  const first = useMemo(() => firstResnum(residues) ?? 1, [residues]);
  const selIndex = selected ? residues.findIndex((r) => r.chain === selected.chain && r.resnum === selected.resnum) : -1;
  useFollowSelection(zoom, selIndex);
  const theme = useDocumentTheme();
  if (!tracks) return <p className="muted small">Consenso per residuo non disponibile per questa struttura.</p>;
  const { disorder: dis, predictor: pred } = tracks;
  const data = (): ChartData => ({
    columns: [
      { key: "residuo", label: "Residuo" },
      { key: "metodi", label: "Metodi", num: true },
      ...per.signals.map((s) => ({ key: `s_${s.key}`, label: s.label, num: true })),
      ...(dis ? [{ key: "disordine", label: "Disordine predetto", num: true }] : []),
      ...(pred ? [{ key: "predizione", label: "Diverge dalla predizione", num: true }, { key: "lddt", label: "lDDT (predizione)", num: true }] : []),
    ],
    rows: residues.map((r, i) => ({
      residuo: residueName(r),
      metodi: per.counts[i],
      ...Object.fromEntries(per.signals.map((s) => [`s_${s.key}`, per.flags[i].includes(s.key) ? 1 : 0])),
      ...(dis ? { disordine: dis.flags[i] ? 1 : 0 } : {}),
      ...(pred ? { predizione: pred.flags[i] ? 1 : 0, lddt: pred.lddt[i] } : {}),
    })),
  });
  const legend = (
    <div className="legend">
      {per.signals.map((s) => <span key={s.key} title={s.rule}><i style={{ background: SIGNAL_COLOR[s.key] }} />{s.label}: {s.rule}</span>)}
      {dis && <span title={dis.rule}><i style={{ background: DISORDER_COLOR }} />{dis.label}: {dis.rule}</span>}
      {pred && <span title={pred.rule}><i style={{ background: PREDICTOR_COLOR }} />{pred.label}: {pred.rule}</span>}
      {CONSENSUS_SCALE.bins.filter((b) => b.gt !== undefined).reverse().map((b) => <span key={b.label}><i className="edged" style={{ background: themedColor(b.color, theme) }} />{b.label}</span>)}
      {consensus.regions.some((r) => !r.expected) && <span><i style={{ background: "var(--err)" }} />Regione problematica (piena)</span>}
      {consensus.regions.some((r) => r.expected) && <span><i style={EXPECTED_SWATCH} />Regione in disordine predetto (attesa, tratteggiata)</span>}
    </div>
  );
  return (
    <ChartFrame
      title="Segnalazioni per residuo"
      subtitle="Metodi che segnalano ogni residuo, quanti sono e regioni problematiche"
      filename="consenso_per_residuo"
      controls={<ZoomBar zoom={zoom} format={(lo, hi) => residueSpan(residues, { lo, hi }, multi)} />}
      hint={ZOOM_HINT}
      legend={legend}
      data={data}
      onRowClick={onSelect ? (_row, i) => onSelect(residues[i]) : undefined}
      selectedIndex={selIndex >= 0 ? selIndex : undefined}
    >
      {({ expanded }) => (
        <TracksSvg expanded={expanded} residues={residues} consensus={consensus} tracks={tracks} zoom={zoom} selIndex={selIndex} multi={multi}
          first={first} onSelect={onSelect} onRegion={onRegion} />
      )}
    </ChartFrame>
  );
}

/** SVG delle righe (uno per vista); lo zoom arriva dal componente esterno. */
function TracksSvg({ expanded, residues, consensus, tracks, zoom, selIndex, multi, first, onSelect, onRegion }: {
  expanded: boolean;
  residues: ResidueRow[];
  consensus: ConsensusResult;
  tracks: Lanes;
  zoom: DomainState;
  selIndex: number;
  multi: boolean;
  /** numero del primo residuo, per le tacche tonde dell'asse */
  first: number;
  onSelect?: (r: ResidueRow) => void;
  onRegion?: (region: ConsensusRegion) => void;
}) {
  const tip = useTooltip();
  const theme = useDocumentTheme();
  const [hover, setHover] = useState<number | null>(null);
  const clip = `consensus-clip${useId().replace(/[^\w-]/g, "")}`;
  const { ref, width: W } = useChartWidth(900);
  const per = consensus.per_residue;
  const { disorder: dis, predictor: pred } = tracks;
  const n = residues.length;
  const label = expanded ? 150 : 128;
  const right = 10;
  const rowH = expanded ? 20 : 15;
  const barsH = expanded ? 96 : 44;
  const regionH = expanded ? 14 : 10;
  const top = 4;
  const rowY = (k: number) => top + k * (rowH + 3);
  const disTop = rowY(tracks.signals.length);
  const predTop = rowY(tracks.signals.length + (dis ? 1 : 0));
  const barsTop = rowY(tracks.signals.length + (dis ? 1 : 0) + (pred ? 1 : 0)) + 6;
  const regionsTop = barsTop + barsH + 6;
  const axisY = regionsTop + regionH + 4;
  const H = axisY + 18;
  const axis = useZoomAxis({ zoom, range: [label, W - right], wheel: expanded, area: { y0: top, y1: regionsTop + regionH } });
  const { domain } = zoom;
  const cells = (s: number, e: number) => ({ x: axis.scale(s - 0.5), width: Math.max(1, axis.scale(e + 0.5) - axis.scale(s - 0.5)) });
  const shown = <T extends Span>(list: T[]) => list.filter((r) => inView(r, domain));
  const barY = (c: number) => barsTop + barsH - (c / tracks.maxCount) * barsH;
  const xTicks = residueTicks(domain, Math.max(3, Math.floor((W - label - right) / 80)), n, first);
  // residuo sotto il puntatore e, nella riga delle regioni, la regione: i clic si risolvono qui sull'SVG (dopo un
  // trascinamento il puntatore è catturato dall'SVG e il clic non arriva ai rettangoli)
  const hit = (e: RMouseEvent<SVGSVGElement>) => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    if (p.x < label || p.x > W - right || !n) return null;
    const at = axis.invert(p.x);
    const inRegions = p.y >= regionsTop - 2 && p.y <= regionsTop + regionH + 2;
    return {
      i: Math.max(0, Math.min(n - 1, Math.round(at))),
      region: inRegions ? consensus.regions.find((r) => at >= r.start_index - 0.5 && at <= r.end_index + 0.5) ?? null : null,
    };
  };
  const lane = (y: number, text: string, key: string) => (
    <g key={key}>
      <text x={label - 8} y={y + rowH / 2 + 4} textAnchor="end">{text}</text>
      <rect x={label} y={y} width={Math.max(0, W - label - right)} height={rowH} fill="var(--panel-3)" rx={2} />
    </g>
  );
  const vline = (i: number) => ({ x1: axis.scale(i), x2: axis.scale(i), y1: top, y2: regionsTop + regionH });
  return (
    <div ref={ref}>
      <svg
        ref={axis.svgRef}
        className="chart zoomable"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Segnalazioni per residuo: ${n} residui`}
        {...axis.handlers}
        onMouseMove={(e) => {
          const h = axis.brush ? null : hit(e);
          if (!h) {
            setHover(null);
            return tip.hide();
          }
          const { i, region } = h;
          setHover(i);
          tip.show(e, region ? (
            <>
              <b>{region.text}</b>
              {region.expected && <div className="text-2">in una regione di disordine predetto: attesa poco affidabile</div>}
              {onRegion && <div className="text-2">clic per selezionare la regione</div>}
            </>
          ) : (
            <>
              <b>{residueName(residues[i])}</b>
              <div>{per.flags[i].length ? per.flags[i].map((k) => per.signals.find((s) => s.key === k)?.label ?? k).join(", ") : "nessuna segnalazione"}</div>
              {dis?.flags[i] && <div className="text-2">disordine predetto: atteso poco affidabile (non entra nel consenso)</div>}
              {pred?.lddt[i] != null && <div className="text-2">lDDT rispetto alla predizione {fmtNum(pred.lddt[i], 2)}{pred.plddt[i] != null ? ` · pLDDT ${fmtNum(pred.plddt[i], 0)}` : ""}</div>}
            </>
          ));
        }}
        onMouseLeave={() => {
          setHover(null);
          tip.hide();
        }}
        onClick={(e) => {
          if (axis.consumeClick()) return;
          const h = hit(e);
          if (h?.region && onRegion) onRegion(h.region);
          else if (h) onSelect?.(residues[h.i]);
        }}
      >
        <defs>
          <clipPath id={clip}>
            <rect x={label} y={0} width={Math.max(0, W - label - right)} height={H} />
          </clipPath>
        </defs>
        {tracks.signals.map((s, k) => lane(rowY(k), `${s.label} (${s.flagged})`, s.key))}
        {dis && lane(disTop, "disordine (info)", "disorder")}
        {pred && lane(predTop, "predizione (info)", "predictor")}
        <text x={label - 8} y={barsTop + barsH / 2 + 4} textAnchor="end">metodi</text>
        <line className="grid-line" x1={label} x2={W - right} y1={barY(2)} y2={barY(2)} />
        <line x1={label} x2={W - right} y1={barsTop + barsH} y2={barsTop + barsH} stroke="var(--chart-axis)" />
        <text x={label - 8} y={regionsTop + regionH - 1} textAnchor="end">regioni</text>
        <g clipPath={`url(#${clip})`}>
          {tracks.signals.map((s, k) => shown(s.spans).map((r) => (
            <rect key={`${s.key}${r.start}`} {...cells(r.start, r.end)} y={rowY(k)} height={rowH} fill={SIGNAL_COLOR[s.key]} />
          )))}
          {dis?.regions.filter((r) => inView({ start: r.start_index, end: r.end_index }, domain)).map((r) => (
            <rect key={r.start_index} {...cells(r.start_index, r.end_index)} y={disTop + 3} height={rowH - 6} rx={2} fill={DISORDER_COLOR} />
          ))}
          {shown(tracks.predictorSpans).map((r) => <rect key={r.start} {...cells(r.start, r.end)} y={predTop} height={rowH} fill={PREDICTOR_COLOR} />)}
          {/* colori della scala condivisi con il 3D (e la sua legenda), nel tema corrente: il bordo tiene visibile "1 metodo" */}
          {shown(tracks.counts).map((c) => (
            <rect key={c.start} {...cells(c.start, c.end)} y={barY(c.value)} height={barsTop + barsH - barY(c.value)} fill={themedColor(colorFor(CONSENSUS_SCALE, c.value), theme)}
              stroke="var(--chart-axis)" strokeWidth={0.75} />
          ))}
          {/* regioni problematiche piene, attese (disordine predetto) vuote e tratteggiate: distinte anche senza colori */}
          {consensus.regions.filter((r) => inView({ start: r.start_index, end: r.end_index }, domain)).map((r) => {
            const box = cells(r.start_index, r.end_index);
            const w = Math.max(2, box.width);
            const cursor = { cursor: onRegion ? "pointer" : undefined };
            return r.expected ? (
              // il tratto (1,5) sta dentro il rettangolo: stesso ingombro delle regioni piene
              <rect key={`${r.chain}${r.start}`} x={box.x + 0.75} y={regionsTop + 0.75} width={Math.max(1, w - 1.5)} height={regionH - 1.5} rx={3}
                fill="var(--warn-soft)" stroke="var(--warn)" strokeWidth={1.5} strokeDasharray="4 2" style={cursor} />
            ) : (
              <rect key={`${r.chain}${r.start}`} x={box.x} y={regionsTop} width={w} height={regionH} rx={3} fill="var(--err)" opacity={0.85} style={cursor} />
            );
          })}
          {selIndex >= 0 && <line {...vline(selIndex)} stroke="var(--text)" strokeWidth={1.5} strokeDasharray="3 2" />}
          {hover !== null && <line {...vline(hover)} stroke="var(--text-2)" strokeDasharray="2 3" />}
        </g>
        {xTicks.map((i) => (
          <g key={i}>
            <line x1={axis.scale(i)} x2={axis.scale(i)} y1={axisY - 3} y2={axisY + 1} stroke="var(--chart-axis)" />
            <text x={axis.scale(i)} y={H - 4} textAnchor="middle">{residueLabel(residues[i], multi)}</text>
          </g>
        ))}
        {axis.brushRect && <rect className="brush" x={axis.brushRect.x} y={top} width={axis.brushRect.width} height={regionsTop + regionH - top} />}
      </svg>
      {tip.node}
    </div>
  );
}
