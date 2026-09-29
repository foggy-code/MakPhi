import { useMemo, useState, type MouseEvent as RMouseEvent, type ReactNode } from "react";
import type { QualityReport, RamaRect, ResidueRow } from "../api/types";

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

type Metric = "dope" | "bfactor" | "rsa";
const METRIC_INFO: Record<Metric, { label: string; unit: string; hint: string }> = {
  dope: { label: "Energia DOPE per residuo", unit: "", hint: "Valori più alti (meno negativi) indicano regioni potenzialmente mal modellate." },
  bfactor: { label: "B-factor medio per residuo", unit: "Å²", hint: "Nei modelli MODELLER il B-factor non ha significato sperimentale." },
  rsa: { label: "Accessibilità relativa al solvente", unit: "", hint: "RSA < 0,2 = residuo sepolto." },
};

export function ResidueProfile({ residues, metric, height = 200, onSelect }: { residues: ResidueRow[]; metric: Metric; height?: number; onSelect?: (r: ResidueRow) => void }) {
  const tip = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const values = residues.map((r) => r[metric]);
  const valid = values.filter((v): v is number => v !== null && v !== undefined);
  const W = 900;
  const pad = { l: 44, r: 10, t: 16, b: 24 };
  const n = residues.length;
  const min = valid.length ? Math.min(...valid) : 0;
  const max = valid.length ? Math.max(...valid) : 1;
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
  }, [residues, metric, min, max]);
  if (!valid.length) return <p className="muted small">Dato non disponibile per questa struttura.</p>;
  const ticks = 6;
  const info = METRIC_INFO[metric];
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
            const v = r[metric];
            tip.show(e, (
              <>
                <b>{r.resname} {r.chain}{r.resnum}</b>
                <div>{info.label.split(" ")[0]}: {v === null ? "—" : v.toFixed(3)} {info.unit}</div>
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
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={height - pad.b} stroke="var(--text-2)" strokeDasharray="3 3" />}
      </svg>
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
  items: { start: number; end: number; title: string }[];
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
            {t.items.map((it, i) => (
              <rect
                key={i}
                x={x(it.start)}
                y={24 + r * rowH + 1}
                width={Math.max(2, x(it.end) - x(it.start))}
                height={12}
                rx={3}
                fill={t.color}
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
