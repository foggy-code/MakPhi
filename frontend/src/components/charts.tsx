import { useMemo, useState, type MouseEvent as RMouseEvent, type ReactNode } from "react";
import type { ConsensusRegion, ConsensusResult, QualityReport, RamaRect, ResidueRow } from "../api/types";
import { colorFor } from "../lib/colorscale";
import { CONSENSUS_SCALE, describeResidue, type ResidueColoring } from "../lib/residueColoring";

// ---------------------------------------------------------------------------------- tooltip
function useTooltip() {
  const [tip, setTip] = useState<{ x: number; y: number; content: ReactNode } | null>(null);
  const node = tip ? (
    <div className="tooltip" style={{ left: tip.x + 14, top: tip.y + 12 }}>
      {tip.content}
    </div>
  ) : null;
  return { show: (e: RMouseEvent, content: ReactNode) => setTip({ x: e.clientX, y: e.clientY, content }), hide: () => setTip(null), node };
}

// ---------------------------------------------------------------------------------- Ramachandran
const REGION_COLOR = { favored: "var(--accent)", allowed: "var(--accent-2)", outlier: "var(--err)" };

export function RamachandranPlot({ report, onSelect }: { report: QualityReport["ramachandran"]; onSelect?: (p: { chain: string; resnum: string }) => void }) {
  const [cls, setCls] = useState<"general" | "glycine" | "proline">("general");
  const tip = useTooltip();
  const size = 360;
  const pad = { l: 38, b: 30, t: 8, r: 8 };
  const W = size + pad.l + pad.r;
  const H = size + pad.t + pad.b;
  const x = (phi: number) => pad.l + ((phi + 180) / 360) * size;
  const y = (psi: number) => pad.t + ((180 - psi) / 360) * size;
  const rect = (r: RamaRect, fill: string, key: string) => (
    <rect key={key} x={x(r[0])} y={y(r[3])} width={x(r[1]) - x(r[0])} height={y(r[2]) - y(r[3])} fill={fill} />
  );
  const regions = report.regions[cls];
  const pts = report.points.filter((p) => (cls === "general" ? p.class === "general" || p.class === "prepro" : p.class === cls));
  const others = report.points.length - pts.length;
  return (
    <div className="stack-sm">
      <div className="row small">
        <div className="segmented">
          {(["general", "glycine", "proline"] as const).map((c) => (
            <button key={c} className={c === cls ? "on" : ""} onClick={() => setCls(c)}>
              {c === "general" ? "Generale" : c === "glycine" ? "Glicina" : "Prolina"}
            </button>
          ))}
        </div>
        <span className="muted">{pts.length} residui{others ? ` (altri ${others} nelle altre classi)` : ""}</span>
      </div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: 520 }}>
        <rect x={pad.l} y={pad.t} width={size} height={size} fill="var(--bg-2)" stroke="var(--border-strong)" />
        {regions.allowed.map((r, i) => rect(r, "rgba(129,140,248,0.16)", `a${i}`))}
        {regions.favored.map((r, i) => rect(r, "rgba(34,211,238,0.22)", `f${i}`))}
        {[-120, -60, 0, 60, 120].map((v) => (
          <g key={v}>
            <line className="grid-line" x1={x(v)} x2={x(v)} y1={pad.t} y2={pad.t + size} />
            <line className="grid-line" y1={y(v)} y2={y(v)} x1={pad.l} x2={pad.l + size} />
          </g>
        ))}
        {[-180, -90, 0, 90, 180].map((v) => (
          <g key={`t${v}`}>
            <text x={x(v)} y={H - 12} textAnchor="middle">{v}</text>
            <text x={pad.l - 6} y={y(v) + 3} textAnchor="end">{v}</text>
          </g>
        ))}
        <text x={pad.l + size / 2} y={H - 1} textAnchor="middle">φ (gradi)</text>
        <text x={10} y={pad.t + size / 2} textAnchor="middle" transform={`rotate(-90 10 ${pad.t + size / 2})`}>ψ (gradi)</text>
        {pts.map((p, i) => (
          <circle
            key={i}
            cx={x(p.phi)}
            cy={y(p.psi)}
            r={p.region === "outlier" ? 4 : 2.6}
            fill={REGION_COLOR[p.region]}
            stroke={p.region === "outlier" ? "#fff" : "none"}
            strokeWidth={0.8}
            opacity={p.region === "favored" ? 0.8 : 1}
            style={{ cursor: onSelect ? "pointer" : "default" }}
            onMouseMove={(e) =>
              tip.show(e, (
                <>
                  <b>{p.resname} {p.chain}{p.resnum}</b>
                  <div>φ {p.phi.toFixed(0)}°, ψ {p.psi.toFixed(0)}°</div>
                  <div>{p.region === "favored" ? "regione favorita" : p.region === "allowed" ? "regione consentita" : "outlier"}</div>
                </>
              ))
            }
            onMouseLeave={tip.hide}
            onClick={() => onSelect?.(p)}
          />
        ))}
      </svg>
      <div className="legend">
        <span><i style={{ background: "rgba(34,211,238,0.5)" }} />Favorita</span>
        <span><i style={{ background: "rgba(129,140,248,0.45)" }} />Consentita</span>
        <span><i style={{ background: "var(--err)" }} />Outlier</span>
      </div>
      {tip.node}
    </div>
  );
}

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
    <div className="ss-strip" title="Struttura secondaria: elica (rosa), foglietto (giallo), coil (grigio)">
      {segs.map((s, i) => (
        <div key={i} style={{ flex: s.n, background: SS_COLOR[s.c] ?? "var(--coil)", opacity: s.c === "C" ? 0.45 : 1 }} />
      ))}
    </div>
  );
}

type Metric = "dope" | "bfactor" | "rsa" | "plddt";
const METRIC_INFO: Record<Metric, { label: string; unit: string; hint: string }> = {
  dope: { label: "Energia DOPE per residuo", unit: "", hint: "Media mobile calcolata da MODELLER (finestra 15): valori più alti (meno negativi) indicano regioni potenzialmente mal modellate." },
  bfactor: { label: "B-factor medio per residuo", unit: "Å²", hint: "Nei modelli MODELLER il B-factor non ha significato sperimentale." },
  rsa: { label: "Accessibilità relativa al solvente", unit: "", hint: "RSA < 0,2 = residuo sepolto." },
  plddt: { label: "pLDDT per residuo (modello predetto)", unit: "", hint: "Confidenza del predittore (AlphaFold, ESMFold) nella colonna B-factor: > 90 molto alta, 70–90 alta, 50–70 bassa, < 50 molto bassa." },
};

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
}

export function ResidueProfile({ residues, metric = "dope", values: custom, label, hint, height = 200, selected, threshold, onSelect }: ProfileProps) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const values = custom ?? residues.map((r) => (metric === "plddt" ? r.bfactor : r[metric]));
  const valid = values.filter((v): v is number => v !== null && v !== undefined);
  const W = 900;
  const pad = { l: 44, r: 10, t: 16, b: 24 };
  const n = residues.length;
  const lim = threshold ? [...valid, threshold.value] : valid;
  const min = lim.length ? Math.min(...lim) : 0;
  const max = lim.length ? Math.max(...lim) : 1;
  const span = max - min || 1;
  const x = (i: number) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
  const y = (v: number) => pad.t + (1 - (v - min) / span) * (height - pad.t - pad.b);
  const path = useMemo(() => {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v === null || v === undefined) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [residues, metric, custom, min, max]);
  if (!valid.length) return <p className="muted small">Dato non disponibile per questa struttura.</p>;
  const ticks = 6;
  const info = { ...METRIC_INFO[metric], ...(label ? { label, unit: "" } : {}), ...(hint !== undefined ? { hint } : {}) };
  const selIndex = selected ? residues.findIndex((r) => r.chain === selected.chain && r.resnum === selected.resnum) : -1;
  return (
    <div className="stack-sm">
      <div className="row small">
        <b>{info.label}</b>
        <span className="muted">{info.hint}</span>
      </div>
      <SSStrip ss3={residues.map((r) => r.ss3).join("")} />
      <svg
        className="chart"
        viewBox={`0 0 ${W} ${height}`}
        onMouseLeave={() => {
          setHover(null);
          tip.hide();
        }}
        onMouseMove={(e) => {
          const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1));
          if (i >= 0 && i < n) {
            setHover(i);
            const r = residues[i];
            const v = values[i];
            tip.show(e, (
              <>
                <b>{r.resname} {r.chain}{r.resnum}</b>
                <div>{info.label.split(" ")[0]}: {v === null || v === undefined ? "—" : v.toFixed(3)} {info.unit}</div>
                <div className="muted">DSSP {r.ss} · Rama {r.rama ?? "—"}</div>
              </>
            ));
          }
        }}
        onClick={() => hover !== null && onSelect?.(residues[hover])}
      >
        {Array.from({ length: ticks }, (_, k) => {
          const v = min + (span * k) / (ticks - 1);
          return (
            <g key={k}>
              <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
              <text x={pad.l - 6} y={y(v) + 3} textAnchor="end">{Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)}</text>
            </g>
          );
        })}
        {Array.from({ length: Math.min(10, n) }, (_, k) => {
          const i = Math.round((k / Math.max(1, Math.min(10, n) - 1)) * (n - 1));
          return <text key={k} x={x(i)} y={height - 6} textAnchor="middle">{residues[i]?.resnum}</text>;
        })}
        {threshold && (
          <g>
            <line x1={pad.l} x2={W - pad.r} y1={y(threshold.value)} y2={y(threshold.value)} stroke="var(--warn)" strokeDasharray="5 4" />
            <text x={W - pad.r} y={y(threshold.value) - 3} textAnchor="end" style={{ fill: "var(--warn)" }}>{threshold.label}</text>
          </g>
        )}
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
        {selIndex >= 0 && <line x1={x(selIndex)} x2={x(selIndex)} y1={pad.t} y2={height - pad.b} stroke="var(--warn)" strokeWidth={2} />}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={height - pad.b} stroke="var(--text-2)" strokeDasharray="3 3" />}
      </svg>
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------------------------- striscia colorata per residuo
export function ResidueStrip({ residues, coloring, selected, onSelect }: { residues: ResidueRow[]; coloring: ResidueColoring; selected?: { chain: string; resnum: string } | null; onSelect?: (r: ResidueRow) => void }) {
  const tip = useTooltip();
  const byKey = useMemo(() => new Map(coloring.values.map((v) => [`${v.chain}:${v.resnum}`, v])), [coloring]);
  return (
    <div>
      <div className="res-strip" onMouseLeave={tip.hide}>
        {residues.map((r) => {
          const v = byKey.get(`${r.chain}:${r.resnum}`);
          const sel = !!selected && selected.chain === r.chain && selected.resnum === r.resnum;
          return (
            <div
              key={`${r.chain}${r.resnum}`}
              className={sel ? "sel" : undefined}
              style={{ background: colorFor(coloring.scale, v?.value ?? null) }}
              onMouseMove={(e) => tip.show(e, <><b>{r.resname} {r.chain}{r.resnum}</b><div>{describeResidue(coloring, r)}</div></>)}
              onClick={() => onSelect?.(r)}
            />
          );
        })}
      </div>
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------------------------- consenso per residuo
const SIGNAL_COLOR: Record<string, string> = { rama: "#ef4444", clash: "#f59e0b", dope: "#a78bfa", qmean: "#38bdf8", prosa: "#34d399" };
const DISORDER_COLOR = "#94a3b8";
const PREDICTOR_COLOR = "#f472b6"; // F9.3: diverge da una predizione sicura (informativo)

/** Segnalazioni dei metodi sovrapposte sullo stesso asse dei residui, numero di metodi e regioni problematiche. */
export function ConsensusTracks({ residues, consensus, selected, onSelect, onRegion }: {
  residues: ResidueRow[];
  consensus: ConsensusResult;
  selected?: { chain: string; resnum: string } | null;
  onSelect?: (r: ResidueRow) => void;
  onRegion?: (region: ConsensusRegion) => void;
}) {
  const tip = useTooltip();
  const per = consensus.per_residue;
  const n = residues.length;
  if (!n || per.flags.length !== n) return <p className="muted small">Consenso per residuo non disponibile per questa struttura.</p>;
  const W = 640;
  const label = 118;
  const rowH = 16;
  const barsH = 44;
  const signals = per.signals;
  // disordine predetto (F5.5): riga informativa in più, fuori dal conteggio dei metodi
  const dis = consensus.disorder && consensus.disorder.flags.length === n ? consensus.disorder : null;
  // accordo con una predizione esterna (F9.3): riga informativa, solo con il confronto incluso
  const pred = consensus.predictor && consensus.predictor.flags.length === n ? consensus.predictor : null;
  const top = 4;
  const disTop = top + signals.length * (rowH + 3);
  const predTop = disTop + (dis ? rowH + 3 : 0);
  const barsTop = top + (signals.length + (dis ? 1 : 0) + (pred ? 1 : 0)) * (rowH + 3) + 6;
  const regionsTop = barsTop + barsH + 4;
  const H = regionsTop + 12 + 16;
  const cw = (W - label - 10) / n;
  const x = (i: number) => label + i * cw;
  const maxCount = Math.max(3, ...per.counts);
  const selIndex = selected ? residues.findIndex((r) => r.chain === selected.chain && r.resnum === selected.resnum) : -1;
  const pickAt = (e: RMouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((((e.clientX - box.left) / box.width) * W - label) / cw);
    return i >= 0 && i < n ? i : null;
  };
  return (
    <div>
      <svg
        className="chart"
        viewBox={`0 0 ${W} ${H}`}
        onMouseMove={(e) => {
          const i = pickAt(e);
          if (i === null) return tip.hide();
          const r = residues[i];
          tip.show(e, (
            <>
              <b>{r.resname} {r.chain}{r.resnum}</b>
              <div>{per.flags[i].length ? per.flags[i].map((k) => signals.find((s) => s.key === k)?.label ?? k).join(", ") : "nessuna segnalazione"}</div>
              {dis?.flags[i] && <div className="muted">disordine predetto: atteso poco affidabile</div>}
              {pred?.lddt[i] != null && <div className="muted">lDDT rispetto alla predizione {pred.lddt[i]!.toFixed(2)}{pred.plddt[i] != null ? ` · pLDDT ${pred.plddt[i]!.toFixed(0)}` : ""}</div>}
            </>
          ));
        }}
        onMouseLeave={tip.hide}
        onClick={(e) => {
          const i = pickAt(e);
          if (i !== null) onSelect?.(residues[i]);
        }}
        style={{ cursor: onSelect ? "pointer" : "default" }}
      >
        {signals.map((s, k) => {
          const yy = top + k * (rowH + 3);
          return (
            <g key={s.key}>
              <text x={label - 8} y={yy + rowH - 3} textAnchor="end">{s.label} ({s.flagged})</text>
              <rect x={label} y={yy} width={W - label - 10} height={rowH} fill="var(--panel-3)" rx={2} />
              {per.flags.map((f, i) => (f.includes(s.key) ? <rect key={i} x={x(i)} y={yy} width={Math.max(1, cw)} height={rowH} fill={SIGNAL_COLOR[s.key]} /> : null))}
            </g>
          );
        })}
        {dis && (
          <g>
            <text x={label - 8} y={disTop + rowH - 3} textAnchor="end">disordine (info)</text>
            <rect x={label} y={disTop} width={W - label - 10} height={rowH} fill="var(--panel-3)" rx={2} />
            {dis.regions.map((r, k) => (
              <rect key={k} x={x(r.start_index)} y={disTop + 3} width={Math.max(1, x(r.end_index + 1) - x(r.start_index))} height={rowH - 6} rx={2} fill={DISORDER_COLOR} opacity={0.75}>
                <title>{`Disordine predetto ${r.text}: atteso poco affidabile (non entra nel consenso)`}</title>
              </rect>
            ))}
          </g>
        )}
        {pred && (
          <g>
            <text x={label - 8} y={predTop + rowH - 3} textAnchor="end">predizione (info)</text>
            <rect x={label} y={predTop} width={W - label - 10} height={rowH} fill="var(--panel-3)" rx={2} />
            {pred.flags.map((f, i) => (f ? <rect key={i} x={x(i)} y={predTop} width={Math.max(1, cw)} height={rowH} fill={PREDICTOR_COLOR} /> : null))}
          </g>
        )}
        <text x={label - 8} y={barsTop + barsH / 2 + 3} textAnchor="end">metodi</text>
        <line className="grid-line" x1={label} x2={W - 10} y1={barsTop + barsH - (2 / maxCount) * barsH} y2={barsTop + barsH - (2 / maxCount) * barsH} />
        {per.counts.map((c, i) => (c ? <rect key={i} x={x(i)} y={barsTop + barsH - (c / maxCount) * barsH} width={Math.max(1, cw)} height={(c / maxCount) * barsH} fill={colorFor(CONSENSUS_SCALE, c)} /> : null))}
        {consensus.regions.map((r, k) => (
          <rect
            key={k}
            x={x(r.start_index)}
            y={regionsTop}
            width={Math.max(2, x(r.end_index + 1) - x(r.start_index))}
            height={10}
            rx={3}
            fill={r.expected ? "var(--warn)" : "var(--err)"}
            opacity={0.85}
            onClick={(e) => {
              e.stopPropagation();
              onRegion?.(r);
            }}
          >
            <title>{r.text}</title>
          </rect>
        ))}
        <text x={label - 8} y={regionsTop + 9} textAnchor="end">regioni</text>
        {selIndex >= 0 && <line x1={x(selIndex) + cw / 2} x2={x(selIndex) + cw / 2} y1={top} y2={regionsTop + 10} stroke="var(--text)" strokeWidth={1.5} strokeDasharray="3 2" />}
        {Array.from({ length: Math.min(10, n) }, (_, k) => {
          const i = Math.round((k / Math.max(1, Math.min(10, n) - 1)) * (n - 1));
          return <text key={k} x={x(i) + cw / 2} y={H - 3} textAnchor="middle">{residues[i]?.resnum}</text>;
        })}
      </svg>
      <div className="legend">
        {signals.map((s) => <span key={s.key} title={s.rule}><i style={{ background: SIGNAL_COLOR[s.key] }} />{s.label}: {s.rule}</span>)}
        {dis && <span title={dis.rule}><i style={{ background: DISORDER_COLOR }} />{dis.label}: {dis.rule}</span>}
        {pred && <span title={pred.rule}><i style={{ background: PREDICTOR_COLOR }} />{pred.label}: {pred.rule}</span>}
      </div>
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------------------------- barre composizione
export function StackBar({ parts }: { parts: { label: string; value: number; color: string }[] }) {
  const total = parts.reduce((s, p) => s + p.value, 0) || 1;
  return (
    <div className="stack-sm">
      <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", background: "var(--panel-3)" }}>
        {parts.map((p) => (
          <div key={p.label} title={`${p.label}: ${p.value.toFixed(1)}%`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />
        ))}
      </div>
      <div className="legend">
        {parts.map((p) => (
          <span key={p.label}><i style={{ background: p.color }} />{p.label} {p.value.toFixed(1)}%</span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------- heatmap identità
export function Heatmap({ ids, values }: { ids: string[]; values: number[][] }) {
  const tip = useTooltip();
  const n = ids.length;
  const cell = Math.max(10, Math.min(34, Math.floor(520 / Math.max(1, n))));
  const label = 110;
  const W = label + cell * n + 4;
  const H = label + cell * n + 4;
  const color = (v: number) => `color-mix(in srgb, var(--accent) ${Math.round(Math.max(5, v))}%, var(--panel-2))`;
  return (
    <div style={{ overflowX: "auto" }}>
      <svg width={W} height={H} className="chart" style={{ width: W }}>
        {ids.map((id, i) => (
          <g key={id}>
            <text x={label - 6} y={label + i * cell + cell / 2 + 3} textAnchor="end">{id.slice(0, 16)}</text>
            <text x={label + i * cell + cell / 2} y={label - 6} textAnchor="start" transform={`rotate(-50 ${label + i * cell + cell / 2} ${label - 6})`}>{id.slice(0, 16)}</text>
          </g>
        ))}
        {values.map((row, i) =>
          row.map((v, j) => (
            <rect
              key={`${i}-${j}`}
              x={label + j * cell}
              y={label + i * cell}
              width={cell - 1}
              height={cell - 1}
              rx={2}
              fill={color(v)}
              onMouseMove={(e) => tip.show(e, <>{ids[i]} ↔ {ids[j]}: <b>{v.toFixed(1)}%</b> identità</>)}
              onMouseLeave={tip.hide}
            />
          )),
        )}
        {n <= 12 &&
          values.map((row, i) =>
            row.map((v, j) => (
              <text key={`t${i}-${j}`} x={label + j * cell + cell / 2} y={label + i * cell + cell / 2 + 3} textAnchor="middle" style={{ fill: v > 60 ? "#041018" : "var(--text-2)", fontSize: 9, pointerEvents: "none" }}>
                {v.toFixed(0)}
              </text>
            )),
          )}
      </svg>
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------------------------- mappa delle feature (UniProt)
export interface Track {
  label: string;
  color: string;
  /** colore di un tratto diverso da quello della traccia (es. eliche e filamenti nella stessa traccia) */
  items: { start: number; end: number; title: string; color?: string }[];
  /** testo sulla traccia senza tratti (es. "nessuna regione") */
  empty?: string;
}

export function FeatureMap({ length, tracks }: { length: number; tracks: Track[] }) {
  const tip = useTooltip();
  const W = 900;
  const label = 150;
  const rowH = 20;
  const x = (p: number) => label + ((p - 1) / Math.max(1, length - 1)) * (W - label - 10);
  const H = 24 + tracks.length * rowH;
  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`}>
        {Array.from({ length: 6 }, (_, k) => {
          const p = Math.round(1 + (k / 5) * (length - 1));
          return (
            <g key={k}>
              <line className="grid-line" x1={x(p)} x2={x(p)} y1={14} y2={H} />
              <text x={x(p)} y={10} textAnchor="middle">{p}</text>
            </g>
          );
        })}
        {tracks.map((t, r) => (
          <g key={t.label}>
            <text x={label - 8} y={24 + r * rowH + 11} textAnchor="end">{t.label.slice(0, 24)}</text>
            <line x1={label} x2={W - 10} y1={24 + r * rowH + 7} y2={24 + r * rowH + 7} stroke="var(--border)" />
            {!t.items.length && t.empty && <text x={label + 6} y={24 + r * rowH + 4} style={{ fill: "var(--text-2)" }}>{t.empty}</text>}
            {t.items.map((it, i) => (
              <rect
                key={i}
                x={x(it.start)}
                y={24 + r * rowH + 1}
                width={Math.max(2, x(it.end) - x(it.start))}
                height={12}
                rx={3}
                fill={it.color ?? t.color}
                opacity={0.85}
                onMouseMove={(e) => tip.show(e, <><b>{it.title || t.label}</b><div>{it.start}–{it.end}</div></>)}
                onMouseLeave={tip.hide}
              />
            ))}
          </g>
        ))}
      </svg>
      {tip.node}
    </div>
  );
}
