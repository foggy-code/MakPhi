import { useCallback, useMemo, useState, type CSSProperties } from "react";
import { alignmentConfidence, CONFIDENCE_WINDOW, IDENTITY_SCALE, regionLabel, TWILIGHT_IDENTITY } from "../lib/alignConfidence";
import { colorFor, legendFor } from "../lib/colorscale";
import { fmtNum } from "../lib/format";
import { useVirtualRows } from "../lib/virtual";

// Righe a finestra (F8b): altezze di .aln-row (line-height), di .aln-block (margine) e di .aln (padding sopra)
const ROW_HEIGHT = 19;
const BLOCK_GAP = 12;
const ALN_PADDING = 10;

interface AlnRow {
  kind: "ruler" | "seq" | "cons" | "gap";
  block: number;
  row?: number;
}

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
}

const isRes = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

export function AlignmentView({ ids, aligned, conservation, width = 60, reference = 0 }: Props) {
  const [mode, setMode] = useState<Mode>("class");
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

  // righe disegnate: per ogni blocco righello, sequenze, consenso e spazio prima del blocco successivo. Oltre
  // VIRTUAL_THRESHOLD righe (allineamenti con molte sequenze) si disegnano solo quelle visibili nel riquadro (F8b)
  const flatRows = useMemo(() => {
    const out: AlnRow[] = [];
    const blocksCount = Math.ceil(length / block);
    for (let b = 0; b < blocksCount; b++) {
      out.push({ kind: "ruler", block: b });
      for (let r = 0; r < aligned.length; r++) out.push({ kind: "seq", block: b, row: r });
      if (conservation) out.push({ kind: "cons", block: b });
      if (b < blocksCount - 1) out.push({ kind: "gap", block: b });
    }
    return out;
  }, [length, block, aligned.length, conservation]);
  const rowHeight = useCallback((i: number) => (flatRows[i]?.kind === "gap" ? BLOCK_GAP : ROW_HEIGHT), [flatRows]);
  const virtual = useVirtualRows<HTMLDivElement>({ count: flatRows.length, rowHeight, header: ALN_PADDING });

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

  const span = (b: number) => [b * block, Math.min(length, b * block + block)] as const;
  const rulerRow = (b: number, key?: string) => {
    const [from, to] = span(b);
    return (
      <div className="aln-row aln-ruler" key={key}>
        <div className="aln-name" />
        <div className="aln-seq">
          {Array.from({ length: to - from }, (_, i) => {
            const col = from + i + 1;
            return <span key={i}>{col % 10 === 0 ? "|" : col % 5 === 0 ? "·" : " "}</span>;
          })}
          <span style={{ width: "auto", paddingLeft: 8 }}>{to}</span>
        </div>
      </div>
    );
  };
  const seqRow = (b: number, r: number, key?: string) => {
    const [from, to] = span(b);
    return (
      <div className="aln-row" key={key ?? r}>
        <div className="aln-name" title={ids[r]}>{confidence && r === ref ? <b>{ids[r]}</b> : ids[r]}</div>
        <div className="aln-seq">
          {aligned[r]
            .slice(from, to)
            .split("")
            .map((ch, i) => {
              const { style, className } = cellStyle(ch, r, from + i);
              return <span key={i} style={style} className={className}>{ch}</span>;
            })}
          <span style={{ width: "auto", paddingLeft: 8, color: "var(--muted)" }}>{ends[r][b]}</span>
        </div>
      </div>
    );
  };
  const consRow = (b: number, key?: string) => {
    const [from, to] = span(b);
    return (
      <div className="aln-row" key={key}>
        <div className="aln-name muted">consenso</div>
        <div className="aln-seq muted">
          {(conservation ?? "").slice(from, to).split("").map((c, i) => (
            <span key={i}>{c}</span>
          ))}
        </div>
      </div>
    );
  };

  const blocks = [];
  if (!virtual.active) {
    for (let b = 0; b * block < length; b++) {
      blocks.push(
        <div className="aln-block" key={b}>
          {rulerRow(b)}
          {aligned.map((_seq, r) => seqRow(b, r))}
          {conservation && consRow(b)}
        </div>,
      );
    }
  } else {
    // finestra: righe piatte alte ROW_HEIGHT, spazio fra i blocchi alto BLOCK_GAP, spaziatori sopra e sotto
    const { start, end, padTop, padBottom } = virtual.range;
    blocks.push(<div key="pad-top" style={{ height: padTop }} aria-hidden="true" />);
    for (let i = start; i < end; i++) {
      const row = flatRows[i];
      const key = `r${i}`;
      if (row.kind === "ruler") blocks.push(rulerRow(row.block, key));
      else if (row.kind === "seq") blocks.push(seqRow(row.block, row.row ?? 0, key));
      else if (row.kind === "cons") blocks.push(consRow(row.block, key));
      else blocks.push(<div key={key} style={{ height: BLOCK_GAP }} aria-hidden="true" />);
    }
    blocks.push(<div key="pad-bottom" style={{ height: padBottom }} aria-hidden="true" />);
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
      {virtual.active && <span className="tiny muted">{aligned.length} sequenze: nel riquadro si disegnano solo le righe visibili (scorri per le altre).</span>}
      <div ref={virtual.ref} onScroll={virtual.onScroll} className="aln" style={virtual.active ? { maxHeight: "70vh", overflowY: "auto" } : undefined}>{blocks}</div>
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
