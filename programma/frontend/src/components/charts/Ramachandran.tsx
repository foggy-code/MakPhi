import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type MouseEvent as RMouseEvent } from "react";
import type { QualityReport } from "../../api/types";
import { fmtNum } from "../../lib/format";
import {
  classLabel, fmtAngle, inRamaView, ramaData, ramaSubtitle, RAMA_SHOW, RAMA_VIEWS, regionCounts, REGION_LABEL, residueMatcher,
  showPoint, viewRegions, type RamaPoint, type RamaRegions, type RamaShow, type RamaView,
} from "../../lib/ramachandran";
import { residueName, sameResidue } from "../../lib/residues";
import { ANGLE_STEPS, niceTicks, svgPoint, useDomain, useSvgBrush, useWheel, wheelFactor, type Domain, type DomainState } from "../../lib/zoom";
import { ChartFrame, ZoomBar } from "../ChartFrame";
import { Icon } from "../Icon";
import { Segmented } from "../ui";
import { useTooltip } from "./common";
import "../../styles/rama.css";

// ---------------------------------------------------------------------------------- Ramachandran

/**
 * Colori standard dei grafici di Ramachandran (PROCHECK, Laskowski et al. 1993), uguali nei due temi: piano bianco
 * (regioni non consentite), regioni consentite gialle, favorite rosse; punti neri, glicine a triangolo, outlier con
 * l'etichetta rossa. Le etichette hanno un alone bianco per restare leggibili anche sopra il rosso e il giallo.
 */
const C = {
  plane: "#ffffff",
  allowed: "#ffff00",
  favored: "#ff0000",
  frame: "#1f1f1f",
  grid: "rgba(0, 0, 0, 0.22)",
  zero: "rgba(0, 0, 0, 0.5)",
  point: "#000000",
  // rosso più scuro del puro: sul bianco supera 4,5:1
  outlier: "#c00000",
  label: "#111111",
  selected: "#1d4ed8",
  match: "#a21caf",
};

const MIN_SPAN = 20; // gradi: ingrandimento massimo
const MIN_BRUSH = 6; // unità del viewBox: sotto, il trascinamento non cambia quell'asse
const TICK = 5;
// vista espansa: etichette per tutti i punti quando l'area visibile è stretta (e i punti non troppi)
const LABEL_ALL_SPAN = 100;
const LABEL_ALL_MAX = 250;
// oltre, le consentite si etichettano solo ingrandendo (altrimenti un mucchio illeggibile)
const ALLOWED_LABELS_MAX = 120;
const MATCH_LABELS_MAX = 60;

const PAGE = { size: 360, pad: { l: 50, r: 12, t: 12, b: 44 }, mark: 3.6, label: 10, ticks: 8 };
const BIG = { size: 640, pad: { l: 56, r: 16, t: 14, b: 50 }, mark: 4.4, label: 11.5, ticks: 12 };
type Geometry = typeof PAGE;

type Selection = { chain: string; resnum: string };

function planeScale(g: Geometry, phi: Domain, psi: Domain) {
  const { size, pad } = g;
  const fx = size / (phi.hi - phi.lo || 1);
  const fy = size / (psi.hi - psi.lo || 1);
  return {
    x: (v: number) => pad.l + (v - phi.lo) * fx,
    y: (v: number) => pad.t + (psi.hi - v) * fy,
    phiAt: (px: number) => phi.lo + (px - pad.l) / fx,
    psiAt: (py: number) => psi.hi - (py - pad.t) / fy,
  };
}

const r1 = (v: number) => Math.round(v * 10) / 10;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const square = (x: number, y: number, s: number) => `M${r1(x - s / 2)} ${r1(y - s / 2)}h${s}v${s}h${-s}z`;
function triangle(x: number, y: number, s: number) {
  const r = s * 0.85; // stessa evidenza del quadratino
  return `M${r1(x)} ${r1(y - r)}L${r1(x + r * 0.866)} ${r1(y + r / 2)}L${r1(x - r * 0.866)} ${r1(y + r / 2)}z`;
}
const ring = (x: number, y: number, r: number) => `M${r1(x - r)} ${r1(y)}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
const fmtTick = (v: number) => fmtAngle(v, Number.isInteger(v) ? 0 : 1);
const fmtSpan = (lo: number, hi: number) => `${fmtAngle(lo)}°…${fmtAngle(hi)}°`;

const labelStyle = (fill: string, fontSize: number): CSSProperties => ({
  fill, fontSize, fontWeight: 650, stroke: C.plane, strokeWidth: 3, strokeLinejoin: "round", paintOrder: "stroke",
});

/**
 * Posizione dell'etichetta di un punto: in alto a destra; a sinistra vicino al bordo destro e sotto vicino a quello
 * superiore, così il ritaglio dell'area non la taglia (gli outlier stanno spesso a φ positivi e ψ alti).
 */
function labelAt(g: Geometry, cx: number, cy: number, text: string): { x: number; y: number; textAnchor?: "end" } {
  const off = g.mark * 0.7;
  const width = text.length * g.label * 0.64; // stima: maiuscole in grassetto
  const left = cx + off + 2 + width > g.pad.l + g.size;
  const below = cy - off - g.label < g.pad.t;
  return {
    x: r1(left ? cx - off - 2 : cx + off + 2),
    y: r1(below ? cy + off + g.label : cy - off),
    textAnchor: left ? "end" : undefined,
  };
}

interface Placed {
  p: RamaPoint;
  cx: number;
  cy: number;
  /** corrisponde alla ricerca (sempre vero senza ricerca) */
  hit: boolean;
}

interface PlotProps {
  expanded: boolean;
  points: RamaPoint[];
  regions: RamaRegions;
  phi: DomainState;
  psi: DomainState;
  selected: Selection | null;
  show: RamaShow;
  match: ((p: RamaPoint) => boolean) | null;
  label: string;
  onPick: (p: RamaPoint) => void;
}

/** Il piano φ/ψ di una vista (pagina o espansa): ha il proprio SVG, trascinamento e tooltip; lo zoom è condiviso. */
function RamaPlane({ expanded, points, regions, phi, psi, selected, show, match, label, onPick }: PlotProps) {
  const g = expanded ? BIG : PAGE;
  const { size, pad, mark } = g;
  const W = pad.l + size + pad.r;
  const H = pad.t + size + pad.b;
  const area = { x0: pad.l, x1: pad.l + size, y0: pad.t, y1: pad.t + size };
  const s = useMemo(() => planeScale(g, phi.domain, psi.domain), [g, phi.domain, psi.domain]);
  const clipId = `rama-clip-${useId().replace(/[^\w-]/g, "")}`;
  const svgRef = useRef<SVGSVGElement>(null);
  const tip = useTooltip();
  const [hover, setHover] = useState<RamaPoint | null>(null);

  const placed = useMemo(() => {
    const { lo: f0, hi: f1 } = phi.domain;
    const { lo: s0, hi: s1 } = psi.domain;
    const out: Placed[] = [];
    for (const p of points) {
      if (p.phi < f0 || p.phi > f1 || p.psi < s0 || p.psi > s1) continue;
      // il filtro "Mostra" non nasconde il residuo selezionato
      if (!showPoint(p, show) && !sameResidue(selected, p)) continue;
      out.push({ p, cx: s.x(p.phi), cy: s.y(p.psi), hit: !match || match(p) });
    }
    return out;
  }, [points, phi.domain, psi.domain, s, show, match, selected]);

  // tutti i punti in pochi tracciati: leggeri da ridisegnare (tooltip) e da copiare nel PNG
  const marks = useMemo(() => {
    let res = "";
    let gly = "";
    let faded = "";
    let rings = "";
    for (const { p, cx, cy, hit } of placed) {
      const isGly = p.class === "glycine";
      const d = isGly ? triangle(cx, cy, mark) : square(cx, cy, mark);
      if (!hit) faded += d;
      else if (isGly) gly += d;
      else res += d;
      if (match && hit) rings += ring(cx, cy, mark + 2);
    }
    return (
      <g>
        {faded && <path d={faded} fill={C.point} fillOpacity={0.18} />}
        {res && <path data-mark="residue" d={res} fill={C.point} />}
        {gly && <path data-mark="glycine" d={gly} fill={C.point} />}
        {/* anello della ricerca con l'alone bianco (come il selezionato): sul rosso il solo magenta non si vede */}
        {rings && <path d={rings} fill="none" stroke={C.plane} strokeWidth={3.6} />}
        {rings && <path data-mark="match" d={rings} fill="none" stroke={C.match} strokeWidth={1.4} />}
      </g>
    );
  }, [placed, mark, match]);

  // etichette: outlier (rosse, come PROCHECK) e residuo selezionato; nella vista espansa anche le consentite, le
  // corrispondenze della ricerca e, in un'area stretta, tutti i punti. Con una ricerca restano solo quelle dei
  // risultati (e del selezionato): niente testo sbiadito
  const labels = useMemo(() => {
    const narrow = expanded && phi.domain.hi - phi.domain.lo < LABEL_ALL_SPAN && psi.domain.hi - psi.domain.lo < LABEL_ALL_SPAN && placed.length <= LABEL_ALL_MAX;
    const matchLabels = !!match && placed.filter((q) => q.hit).length <= MATCH_LABELS_MAX;
    const allowedLabels = placed.filter((q) => q.p.region === "allowed").length <= ALLOWED_LABELS_MAX;
    const shown = placed.filter(({ p, hit }) => sameResidue(selected, p)
      || (hit && (p.region === "outlier" || (expanded && (narrow || matchLabels || (allowedLabels && p.region === "allowed"))))));
    return shown.map(({ p, cx, cy }, i) => {
      const color = p.region === "outlier" ? C.outlier : sameResidue(selected, p) ? C.selected : C.label;
      const text = residueName(p);
      return <text key={i} {...labelAt(g, cx, cy, text)} style={labelStyle(color, g.label)}>{text}</text>;
    });
  }, [placed, selected, expanded, phi.domain, psi.domain, match, g]);

  const current = useMemo(() => placed.find((q) => sameResidue(selected, q.p)) ?? null, [placed, selected]);

  // `set` allarga attorno al centro una selezione più stretta di MIN_SPAN
  const brush = useSvgBrush({
    enabled: true,
    area,
    onBrush: (b) => {
      if (Math.abs(b.x1 - b.x0) >= MIN_BRUSH) phi.set({ lo: s.phiAt(Math.min(b.x0, b.x1)), hi: s.phiAt(Math.max(b.x0, b.x1)) });
      if (Math.abs(b.y1 - b.y0) >= MIN_BRUSH) psi.set({ lo: s.psiAt(Math.max(b.y0, b.y1)), hi: s.psiAt(Math.min(b.y0, b.y1)) });
    },
  });
  useWheel(svgRef, true, (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (!expanded && !ctrl) return false; // senza Ctrl la pagina scorre come sempre
    // passo proporzionale alla rotella (touchpad e pinch continui); null: scorrimento orizzontale, alla pagina
    const f = wheelFactor(e);
    if (f === null) return false;
    // già tutto il piano e si riduce: senza Ctrl scorre la finestra; con Ctrl si consuma (niente zoom della pagina)
    if (f > 1 && phi.full && psi.full) return ctrl ? undefined : false;
    const svg = svgRef.current;
    if (!svg) return false;
    const q = svgPoint(svg, e.clientX, e.clientY);
    phi.zoomAt(f, s.phiAt(clamp(q.x, area.x0, area.x1)));
    psi.zoomAt(f, s.psiAt(clamp(q.y, area.y0, area.y1)));
  });

  /** Punto più vicino al mouse entro il raggio di presa (coordinate del viewBox). */
  const nearest = (e: RMouseEvent<SVGSVGElement>): RamaPoint | null => {
    const q = svgPoint(e.currentTarget, e.clientX, e.clientY);
    if (q.x < area.x0 || q.x > area.x1 || q.y < area.y0 || q.y > area.y1) return null;
    let best: RamaPoint | null = null;
    let bestD = (mark + 3) ** 2;
    for (const { p, cx, cy } of placed) {
      const d = (cx - q.x) ** 2 + (cy - q.y) ** 2;
      if (d <= bestD) {
        best = p;
        bestD = d;
      }
    }
    return best;
  };

  const tx = niceTicks(phi.domain.lo, phi.domain.hi, g.ticks, ANGLE_STEPS);
  const ty = niceTicks(psi.domain.lo, psi.domain.hi, g.ticks, ANGLE_STEPS);
  const b = brush.brush;
  const sizing: CSSProperties = expanded
    ? { maxWidth: `min(100%, calc((100vh - 260px) * ${(W / H).toFixed(3)}))`, margin: "0 auto" }
    : { maxWidth: 520 };
  return (
    <>
      <svg
        ref={svgRef}
        className="chart zoomable"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={label}
        style={{ ...sizing, cursor: hover ? "pointer" : undefined }}
        onPointerDown={brush.handlers.onPointerDown}
        onPointerUp={brush.handlers.onPointerUp}
        onPointerCancel={brush.handlers.onPointerCancel}
        onLostPointerCapture={brush.handlers.onLostPointerCapture}
        onPointerMove={(e) => {
          brush.handlers.onPointerMove(e);
          const p = e.buttons ? null : nearest(e); // durante il trascinamento niente tooltip
          setHover(p);
          // stesso punto: il suggerimento segue il puntatore senza ridisegnare il grafico
          if (p && p === hover) tip.move(e);
          else if (p) {
            tip.show(e, (
              <>
                <b>{residueName(p)}</b>
                <div>φ {fmtAngle(p.phi, 1)}°, ψ {fmtAngle(p.psi, 1)}°</div>
                <div>Regione {REGION_LABEL[p.region].toLowerCase()}</div>
                <div>Classe: {classLabel(p.class)}</div>
              </>
            ));
          } else tip.hide();
        }}
        onPointerLeave={() => {
          setHover(null);
          tip.hide();
        }}
        onClick={(e) => {
          if (brush.consumeClick()) return;
          const p = nearest(e);
          if (p) onPick(p);
        }}
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={area.x0} y={area.y0} width={size} height={size} />
          </clipPath>
        </defs>
        <rect x={area.x0} y={area.y0} width={size} height={size} fill={C.plane} />
        <g clipPath={`url(#${clipId})`}>
          {regions.allowed.map((r, i) => (
            <rect key={`a${i}`} x={s.x(r[0])} y={s.y(r[3])} width={s.x(r[1]) - s.x(r[0])} height={s.y(r[2]) - s.y(r[3])} fill={C.allowed} />
          ))}
          {regions.favored.map((r, i) => (
            <rect key={`f${i}`} x={s.x(r[0])} y={s.y(r[3])} width={s.x(r[1]) - s.x(r[0])} height={s.y(r[2]) - s.y(r[3])} fill={C.favored} />
          ))}
          {tx.filter((v) => v > phi.domain.lo && v < phi.domain.hi).map((v) => (
            <line key={`gx${v}`} x1={s.x(v)} x2={s.x(v)} y1={area.y0} y2={area.y1} stroke={v === 0 ? C.zero : C.grid} strokeDasharray={v === 0 ? undefined : "3 3"} />
          ))}
          {ty.filter((v) => v > psi.domain.lo && v < psi.domain.hi).map((v) => (
            <line key={`gy${v}`} x1={area.x0} x2={area.x1} y1={s.y(v)} y2={s.y(v)} stroke={v === 0 ? C.zero : C.grid} strokeDasharray={v === 0 ? undefined : "3 3"} />
          ))}
          {marks}
          {labels}
          {current && (
            <g fill="none">
              <circle cx={current.cx} cy={current.cy} r={mark + 3.2} stroke={C.plane} strokeWidth={4.6} />
              <circle cx={current.cx} cy={current.cy} r={mark + 3.2} stroke={C.selected} strokeWidth={2.6} />
            </g>
          )}
          {hover && <circle cx={s.x(hover.phi)} cy={s.y(hover.psi)} r={mark + 2} fill="none" stroke={C.frame} strokeWidth={1.3} />}
        </g>
        <rect x={area.x0} y={area.y0} width={size} height={size} fill="none" stroke={C.frame} strokeWidth={1.2} />
        {tx.map((v) => (
          <g key={`tx${v}`}>
            <line x1={s.x(v)} x2={s.x(v)} y1={area.y1} y2={area.y1 - TICK} stroke={C.frame} />
            <line x1={s.x(v)} x2={s.x(v)} y1={area.y0} y2={area.y0 + TICK} stroke={C.frame} />
            <text x={s.x(v)} y={area.y1 + 15} textAnchor="middle">{fmtTick(v)}</text>
          </g>
        ))}
        {ty.map((v) => (
          <g key={`ty${v}`}>
            <line x1={area.x0} x2={area.x0 + TICK} y1={s.y(v)} y2={s.y(v)} stroke={C.frame} />
            <line x1={area.x1} x2={area.x1 - TICK} y1={s.y(v)} y2={s.y(v)} stroke={C.frame} />
            <text x={area.x0 - 6} y={s.y(v) + 4} textAnchor="end">{fmtTick(v)}</text>
          </g>
        ))}
        <text className="axis-title" x={(area.x0 + area.x1) / 2} y={H - 8} textAnchor="middle">φ (gradi)</text>
        <text className="axis-title" x={13} y={(area.y0 + area.y1) / 2} textAnchor="middle" transform={`rotate(-90 13 ${(area.y0 + area.y1) / 2})`}>ψ (gradi)</text>
        {b && (
          <rect className="brush" x={Math.min(b.x0, b.x1)} y={Math.min(b.y0, b.y1)} width={Math.abs(b.x1 - b.x0)} height={Math.abs(b.y1 - b.y0)}
            style={{ fill: "rgba(29, 78, 216, 0.12)", stroke: C.selected }} />
        )}
      </svg>
      {tip.node}
    </>
  );
}

/**
 * Zoom dei due assi insieme (vista nella pagina); nella vista espansa ogni asse ha la sua ZoomBar. Come la ZoomBar:
 * pulsanti sempre presenti (disattivati quando non servono) e intervallo largo abbastanza, così ingrandire non sposta
 * i comandi sotto il puntatore.
 */
function PlaneZoom({ phi, psi }: { phi: DomainState; psi: DomainState }) {
  const full = phi.full && psi.full;
  return (
    <span className="zoom-bar plane" data-export-ignore="" role="group" aria-label="Zoom del grafico">
      <button type="button" className="btn btn-sm btn-ghost btn-icon" title="Riduci lo zoom" aria-label="Riduci lo zoom" disabled={full}
        onClick={() => { phi.zoomOut(); psi.zoomOut(); }}>
        <Icon name="zoomOut" size={14} />
      </button>
      <span className="range">{full ? "Tutto il piano" : `φ ${fmtSpan(phi.domain.lo, phi.domain.hi)}, ψ ${fmtSpan(psi.domain.lo, psi.domain.hi)}`}</span>
      <button type="button" className="btn btn-sm btn-ghost btn-icon" title="Ingrandisci" aria-label="Ingrandisci"
        onClick={() => { phi.zoomIn(); psi.zoomIn(); }}>
        <Icon name="zoomIn" size={14} />
      </button>
      <button type="button" className="btn btn-sm btn-ghost" title="Mostra tutto il piano" disabled={full} onClick={() => { phi.reset(); psi.reset(); }}>
        <Icon name="refresh" size={13} /> Tutto
      </button>
    </span>
  );
}

/** Campione della legenda: un punto sul piano bianco del grafico (leggibile anche nel tema scuro). */
function MarkSwatch({ shape }: { shape: "square" | "triangle" | "ring" | "match" }) {
  return (
    <svg className="rama-swatch" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <rect x="0.5" y="0.5" width="13" height="13" rx="2" fill={C.plane} stroke="#6b7280" />
      {shape === "square" && <rect x="5" y="5" width="4" height="4" fill={C.point} />}
      {shape === "triangle" && <polygon points="7,3.6 10.4,9.4 3.6,9.4" fill={C.point} />}
      {shape === "ring" && <circle cx="7" cy="7" r="3.8" fill="none" stroke={C.selected} strokeWidth="2" />}
      {shape === "match" && (
        <>
          <rect x="5.5" y="5.5" width="3" height="3" fill={C.point} />
          <circle cx="7" cy="7" r="4" fill="none" stroke={C.match} strokeWidth="1.4" />
        </>
      )}
    </svg>
  );
}

function RamaLegend({ view, selected, searching }: { view: RamaView; selected: boolean; searching: boolean }) {
  return (
    <div className="legend">
      <span><i style={{ background: C.favored }} />Regioni favorite</span>
      <span><i style={{ background: C.allowed, boxShadow: "inset 0 0 0 1px rgba(0, 0, 0, 0.3)" }} />Regioni consentite</span>
      <span><i style={{ background: C.plane, boxShadow: "inset 0 0 0 1px #6b7280" }} />Non consentite (outlier)</span>
      <span><MarkSwatch shape="square" />Residuo</span>
      {(view === "all" || view === "glycine") && <span><MarkSwatch shape="triangle" />Glicina</span>}
      <span><span className="rama-sample">A45</span>Outlier (etichettato)</span>
      {selected && <span><MarkSwatch shape="ring" />Residuo selezionato</span>}
      {searching && <span><MarkSwatch shape="match" />Risultati della ricerca</span>}
      {view === "all" && <span className="muted">Regioni del caso generale; glicine e proline classificate con le proprie</span>}
    </div>
  );
}

/** Pannello della vista espansa: filtro e ricerca, conteggi per regione, residuo selezionato, elenco degli outlier. */
function RamaSide({ points, all, selected, show, onShow, query, onQuery, matches, onPick, onCenter }: {
  points: RamaPoint[];
  all: RamaPoint[];
  selected: Selection | null;
  show: RamaShow;
  onShow: (v: RamaShow) => void;
  query: string;
  onQuery: (v: string) => void;
  matches: number | null;
  onPick: (p: RamaPoint) => void;
  onCenter: (p: RamaPoint) => void;
}) {
  const counts = useMemo(() => regionCounts(points), [points]);
  const outliers = useMemo(() => points.filter((p) => p.region === "outlier"), [points]);
  const current = useMemo(() => all.find((p) => sameResidue(selected, p)) ?? null, [all, selected]);
  const pct = (n: number) => (counts.total ? `${fmtNum((100 * n) / counts.total, 1)}%` : "—");
  const visible = !!current && points.includes(current);
  return (
    <aside className="rama-side" data-export-ignore="">
      <section>
        <h4>Esplora</h4>
        <Segmented label="Residui da mostrare" value={show} onChange={onShow} options={RAMA_SHOW} />
        <input className="input" type="search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Cerca un residuo (es. A45, GLY)" aria-label="Cerca un residuo"
          onKeyDown={(e) => {
            // Esc svuota la ricerca (in ogni browser) senza chiudere la vista espansa; a campo vuoto la chiude
            if (e.key === "Escape" && query) {
              e.preventDefault();
              onQuery("");
            }
          }} />
        {matches !== null && <span className="small">{matches === 0 ? "Nessun residuo trovato" : `${matches} ${matches === 1 ? "residuo trovato" : "residui trovati"}`}</span>}
      </section>
      <section>
        <h4>Regioni</h4>
        <dl className="kv">
          <dt>Favorite</dt>
          <dd>{counts.favored} ({pct(counts.favored)})</dd>
          <dt>Consentite</dt>
          <dd>{counts.allowed} ({pct(counts.allowed)})</dd>
          <dt>Outlier</dt>
          <dd>{counts.outlier} ({pct(counts.outlier)})</dd>
        </dl>
      </section>
      <section>
        <h4>Residuo selezionato</h4>
        {current ? (
          <>
            <dl className="kv">
              <dt>Residuo</dt>
              <dd>{residueName(current)}</dd>
              <dt>Classe</dt>
              <dd>{classLabel(current.class)}</dd>
              <dt>φ, ψ</dt>
              <dd>{fmtAngle(current.phi, 1)}°, {fmtAngle(current.psi, 1)}°</dd>
              <dt>Regione</dt>
              <dd>{REGION_LABEL[current.region]}</dd>
            </dl>
            {visible ? (
              <button type="button" className="btn btn-sm" onClick={() => onCenter(current)}>
                <Icon name="zoomIn" size={14} /> Centra sul residuo
              </button>
            ) : <span className="small muted">Non è in questa vista.</span>}
          </>
        ) : <p className="small muted">Clic su un punto del grafico o su una riga della scheda Dati per i dettagli.</p>}
      </section>
      <section>
        <h4>Outlier ({outliers.length})</h4>
        {outliers.length ? (
          <div className="rama-outliers">
            {outliers.map((p) => (
              <button key={`${p.chain}:${p.resnum}`} type="button" className="chip" title="Seleziona e centra" onClick={() => { onPick(p); onCenter(p); }}>
                {residueName(p)} <span className="muted">({fmtAngle(p.phi)}, {fmtAngle(p.psi)})</span>
              </button>
            ))}
          </div>
        ) : <p className="small muted">Nessun outlier in questa vista.</p>}
      </section>
    </aside>
  );
}

/**
 * Grafico di Ramachandran con i colori standard di PROCHECK. Viste: "Tutti" (predefinita: tutti i residui sulle
 * regioni del caso generale, il grafico classico), "Generale" (anche pre-prolina), "Glicina", "Prolina".
 * Trascinare un rettangolo ingrandisce (anche un solo asse); la rotella fa lo zoom attorno al mouse (con Ctrl nella
 * pagina); il clic su un punto chiama `onSelect`. `selected` mostra un anello blu sul residuo; senza, il grafico
 * ricorda l'ultimo residuo cliccato.
 */
export function RamachandranPlot({ report, onSelect, selected }: {
  report: QualityReport["ramachandran"];
  onSelect?: (p: { chain: string; resnum: string }) => void;
  selected?: Selection | null;
}) {
  const [view, setView] = useState<RamaView>("all");
  const [show, setShow] = useState<RamaShow>("all");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Selection | null>(null);
  const phi = useDomain(-180, 180, MIN_SPAN);
  const psi = useDomain(-180, 180, MIN_SPAN);
  const all = useMemo(() => report.points.filter((p) => Number.isFinite(p.phi) && Number.isFinite(p.psi)), [report.points]);
  const points = useMemo(() => all.filter((p) => inRamaView(p, view)), [all, view]);
  const regions = viewRegions(report.regions, view);
  const subtitle = useMemo(() => ramaSubtitle(view, points), [view, points]);
  const data = useMemo(() => ramaData(points), [points]);
  const match = useMemo(() => residueMatcher(query), [query]);
  const matches = useMemo(() => (match ? points.filter(match).length : null), [points, match]);
  const current = selected !== undefined ? selected : picked;
  const viewName = RAMA_VIEWS.find((v) => v.value === view)?.label ?? view;
  const target = useMemo(() => points.find((p) => sameResidue(current, p)) ?? null, [points, current]);

  // selezione da fuori (3D, tabelle, scheda Dati) fuori dall'area ingrandita: la vista si sposta sul residuo con la
  // stessa ampiezza, solo sull'asse che serve. Spostare la vista a mano non la riporta indietro
  useEffect(() => {
    if (!target) return;
    const follow = (axis: DomainState, v: number) => {
      const { lo, hi } = axis.domain;
      if (v < lo || v > hi) axis.set({ lo: v - (hi - lo) / 2, hi: v + (hi - lo) / 2 });
    };
    follow(phi, target.phi);
    follow(psi, target.psi);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const pick = (p: RamaPoint) => {
    setPicked({ chain: p.chain, resnum: p.resnum });
    onSelect?.(p);
  };
  /** Porta il residuo al centro, con un'area di 60° (o quella attuale, se è già più stretta). */
  const center = (p: RamaPoint) => {
    const half = (d: Domain) => Math.min(60, d.hi - d.lo) / 2;
    phi.set({ lo: p.phi - half(phi.domain), hi: p.phi + half(phi.domain) });
    psi.set({ lo: p.psi - half(psi.domain), hi: p.psi + half(psi.domain) });
  };
  const plane = (expanded: boolean) => (
    <RamaPlane expanded={expanded} points={points} regions={regions} phi={phi} psi={psi} selected={current}
      show={expanded ? show : "all"} match={expanded ? match : null} label={`Grafico di Ramachandran: ${subtitle}`} onPick={pick} />
  );

  return (
    <ChartFrame
      title="Grafico di Ramachandran"
      filename={`ramachandran_${viewName.toLowerCase()}`}
      subtitle={subtitle}
      hint="Trascina per ingrandire un'area (in orizzontale o in verticale: un solo asse) · rotella per lo zoom attorno al mouse (Ctrl+rotella nella pagina) · clic su un punto per selezionare il residuo"
      controls={({ expanded }) => (
        <>
          <Segmented label="Vista del grafico" value={view} onChange={setView} options={RAMA_VIEWS} />
          {expanded ? (
            <>
              <ZoomBar zoom={phi} label="φ" format={fmtSpan} />
              <ZoomBar zoom={psi} label="ψ" format={fmtSpan} />
            </>
          ) : <PlaneZoom phi={phi} psi={psi} />}
        </>
      )}
      legend={({ expanded }) => <RamaLegend view={view} selected={!!current} searching={expanded && !!match} />}
      data={data}
      onRowClick={(_, i) => pick(points[i])}
      selectedIndex={target ? points.indexOf(target) : undefined}
    >
      {({ expanded }) => expanded ? (
        <div className="rama-expanded">
          {plane(true)}
          <RamaSide points={points} all={all} selected={current} show={show} onShow={setShow} query={query} onQuery={setQuery}
            matches={matches} onPick={pick} onCenter={center} />
        </div>
      ) : plane(false)}
    </ChartFrame>
  );
}
