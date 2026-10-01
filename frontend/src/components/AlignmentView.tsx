import { useMemo, useState, type CSSProperties } from "react";
import { alignmentConfidence, CONFIDENCE_WINDOW, IDENTITY_SCALE, regionLabel, TWILIGHT_IDENTITY } from "../lib/alignConfidence";
import { colorFor, legendFor } from "../lib/colorscale";
import { fmtNum } from "../lib/format";

// Colori per classe chimica (schema in stile Clustal X).
const CLASS_COLOR: Record<string, string> = {};
const groups: [string, string][] = [
  ["AILMFWV", "#5b8def"],
  ["KR", "#ef5350"],
  ["ED", "#c35bd6"],
  ["NQST", "#34c759"],
  ["C", "#f48fb1"],
  ["G", "#f5a05a"],
  ["P", "#d4c22b"],
  ["HY", "#26b5b5"],
];
groups.forEach(([letters, color]) => letters.split("").forEach((l) => (CLASS_COLOR[l] = color)));

type Mode = "class" | "confidence" | "plain";

interface Props {
  ids: string[];
  aligned: string[];
  conservation?: string;
  width?: number;
  /** riga del bersaglio per la modalità "Confidenza" (PIR: la voce `sequence`); predefinita la prima */
  reference?: number;
  /** modalità iniziale dei colori (l'anteprima per catena dei complessi parte da "Confidenza") */
  initialMode?: Mode;
}

const isRes = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

export function AlignmentView({ ids, aligned, conservation, width = 60, reference = 0, initialMode = "class" }: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [block, setBlock] = useState(width);
  const length = aligned[0]?.length ?? 0;
  const cells = length * aligned.length;
  const canColor = cells <= 80_000;
  const ref = Math.min(Math.max(0, reference), Math.max(0, aligned.length - 1));
  const canConfidence = aligned.length > 1;
  const confidence = useMemo(
    () => (mode === "confidence" && canConfidence ? alignmentConfidence(aligned, ref) : null),
    [mode, canConfidence, aligned, ref],
  );
  const identical = useMemo(() => {
    const out: boolean[] = [];
    for (let c = 0; c < length; c++) {
      const col = aligned.map((s) => s[c]);
      out.push(col[0] !== "-" && col.every((x) => x === col[0]));
    }
    return out;
  }, [aligned, length]);
  // numero dell'ultimo residuo (senza gap) di ogni blocco, come nel formato Clustal
  const ends = useMemo(() => {
    const res: number[][] = aligned.map(() => []);
    aligned.forEach((s, r) => {
      let n = 0;
      for (let c = 0; c < length; c++) {
        if (s[c] !== "-" && s[c] !== ".") n++;
        if ((c + 1) % block === 0 || c === length - 1) res[r].push(n);
      }
    });
    return res;
  }, [aligned, length, block]);

  if (!aligned.length) return null;
  const colored = canColor && mode !== "plain";

  const cellStyle = (ch: string, r: number, col: number): { style?: CSSProperties; className?: string } => {
    if (confidence && canColor) {
      const column = confidence.columns[col];
      if (r === ref) {
        const cls = column.uncovered ? "aln-uncovered" : column.twilight ? "aln-twilight" : undefined;
        return { className: cls, style: column.best !== null ? { background: `${colorFor(IDENTITY_SCALE, column.best)}55` } : ch === "-" ? { color: "var(--muted)" } : undefined };
      }
      if (!isRes(ch)) return column.residue ? { className: "aln-gap" } : { style: { color: "var(--muted)" } };
      const v = confidence.rows[r]?.[col];
      return { style: v !== null && v !== undefined ? { background: `${colorFor(IDENTITY_SCALE, v)}55` } : undefined };
    }
    const color = colored ? CLASS_COLOR[ch.toUpperCase()] : undefined;
    if (color) return { style: { background: `${color}${identical[col] ? "55" : "26"}`, color: identical[col] ? "var(--text)" : undefined, fontWeight: identical[col] ? 700 : 400 } };
    return { style: ch === "-" ? { color: "var(--muted)" } : undefined };
  };

  const blocks = [];
  for (let b = 0; b * block < length; b++) {
    const from = b * block;
    const to = Math.min(length, from + block);
    blocks.push(
      <div className="aln-block" key={b}>
        <div className="aln-row aln-ruler">
          <div className="aln-name" />
          <div className="aln-seq">
            {Array.from({ length: to - from }, (_, i) => {
              const col = from + i + 1;
              return <span key={i}>{col % 10 === 0 ? "|" : col % 5 === 0 ? "·" : " "}</span>;
            })}
            <span style={{ width: "auto", paddingLeft: 8 }}>{to}</span>
          </div>
        </div>
        {aligned.map((seq, r) => (
          <div className="aln-row" key={r}>
            <div className="aln-name" title={ids[r]}>{confidence && r === ref ? <b>{ids[r]}</b> : ids[r]}</div>
            <div className="aln-seq">
              {seq
                .slice(from, to)
                .split("")
                .map((ch, i) => {
                  const { style, className } = cellStyle(ch, r, from + i);
                  return <span key={i} style={style} className={className}>{ch}</span>;
                })}
              <span style={{ width: "auto", paddingLeft: 8, color: "var(--muted)" }}>{ends[r][b]}</span>
            </div>
          </div>
        ))}
        {conservation && (
          <div className="aln-row">
            <div className="aln-name muted">consenso</div>
            <div className="aln-seq muted">
              {conservation.slice(from, to).split("").map((c, i) => (
                <span key={i}>{c}</span>
              ))}
            </div>
          </div>
        )}
      </div>,
    );
  }
  const idLegend = legendFor(IDENTITY_SCALE, "Identità locale");
  const twilight = confidence?.regions.filter((r) => r.kind === "twilight") ?? [];
  const uncovered = confidence?.regions.filter((r) => r.kind === "uncovered") ?? [];
  return (
    <div className="stack-sm">
      <div className="row small">
        <div className="segmented">
          {([["class", "Classe chimica"], ["confidence", "Confidenza"], ["plain", "Nessun colore"]] as const).map(([m, label]) => (
            <button key={m} className={m === mode ? "on" : ""} disabled={(m === "class" && !canColor) || (m === "confidence" && !canConfidence)} onClick={() => setMode(m)}>
              {label}
            </button>
          ))}
        </div>
        <span className="muted">{mode === "confidence" ? `Rispetto a ${ids[ref] ?? "bersaglio"}` : "Colonne identiche in evidenza"} · lunghezza {length}</span>
        <label className="check right">
          Larghezza blocco
          <select className="input" style={{ width: 80, minHeight: 28, padding: "2px 6px" }} value={block} onChange={(e) => setBlock(Number(e.target.value))}>
            {[50, 60, 80, 100, 150].map((w) => (
              <option key={w} value={w}>{w}</option>
            ))}
          </select>
        </label>
      </div>
      {confidence && (
        <div className="stack-sm">
          <div className="aln-track" title={`Miglior identità locale per colonna (finestra ${CONFIDENCE_WINDOW})`}>
            {confidence.columns.map((c, i) => (
              <div key={i} style={{ background: !c.residue ? "transparent" : c.uncovered ? "var(--err)" : c.best !== null ? colorFor(IDENTITY_SCALE, c.best) : "var(--panel-3)" }} />
            ))}
          </div>
          <div className="small text-2">
            Identità locale su {CONFIDENCE_WINDOW} colonne rispetto a <b>{ids[ref]}</b>: media {fmtNum(confidence.summary.meanIdentity, 0)}% sui {confidence.summary.covered} residui coperti.
            {" "}Zona crepuscolare (&lt; {TWILIGHT_IDENTITY}%): {confidence.summary.twilight ? <b>{confidence.summary.twilight} residui ({twilight.map(regionLabel).join(", ")})</b> : "nessun residuo"}.
            {" "}Non coperti dal template: {confidence.summary.uncovered ? <b>{confidence.summary.uncovered} residui ({uncovered.map(regionLabel).join(", ")})</b> : "nessuno"}.
          </div>
        </div>
      )}
      <div className="aln">{blocks}</div>
      {colored && mode === "class" && (
        <div className="legend">
          {[["Idrofobici", "#5b8def"], ["Positivi", "#ef5350"], ["Negativi", "#c35bd6"], ["Polari", "#34c759"], ["Cys", "#f48fb1"], ["Gly", "#f5a05a"], ["Pro", "#d4c22b"], ["His/Tyr", "#26b5b5"]].map(([l, c]) => (
            <span key={l}><i style={{ background: c }} />{l}</span>
          ))}
        </div>
      )}
      {confidence && idLegend.kind === "gradient" && (
        <div className="legend" style={{ alignItems: "center" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Identità locale
            <span style={{ display: "inline-block", width: 140, height: 10, borderRadius: 3, background: idLegend.css }} />
            {idLegend.ticks.map((t) => t.label).join(" · ")}
          </span>
          <span><i style={{ background: "rgba(248, 113, 113, 0.35)" }} />gap del template</span>
          <span><i style={{ background: "var(--err)" }} />bersaglio non coperto</span>
          <span><i style={{ background: "var(--warn)" }} />zona crepuscolare (sottolineato)</span>
          {!canColor && <span className="muted">allineamento troppo grande per colorare le celle: resta la traccia riassuntiva</span>}
        </div>
      )}
    </div>
  );
}
