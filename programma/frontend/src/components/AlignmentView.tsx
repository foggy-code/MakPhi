import { memo, useCallback, useMemo, useState, type CSSProperties, type MouseEvent as RMouseEvent } from "react";
import { alignmentConfidence, CONFIDENCE_WINDOW, IDENTITY_SCALE, regionLabel, TWILIGHT_IDENTITY, type AlignmentConfidence } from "../lib/alignConfidence";
import { alignmentFasta } from "../lib/alignmentFasta";
import { CLASS_TINT, CONFIDENCE_TINT, CONSERVATION_TEXT, identicalColumns, isGap, residueNumberAt, rulerText } from "../lib/alignmentView";
import { RESIDUE_CLASSES, residueClass } from "../lib/aminoAcids";
import { colorFor, legendFor } from "../lib/colorscale";
import { residueText } from "../lib/compareMetrics";
import { safeFilename } from "../lib/exportImage";
import { downloadText, fmtNum } from "../lib/format";
import { useVirtualRows } from "../lib/virtual";
import { textOn } from "./charts";
import { Icon } from "./Icon";
import { Modal } from "./ui";
import "../styles/compare.css";

// Righe a finestra (F8b): altezze di .aln-row (line-height), di .aln-block (margine) e di .aln (padding sopra)
const ROW_HEIGHT = 19;
const BLOCK_GAP = 12;
const ALN_PADDING = 10;

interface AlnRow {
  kind: "ruler" | "seq" | "cons" | "gap";
  block: number;
  row?: number;
}

// Colori per classe chimica (Clustal X): pieni nelle colonne identiche, con il testo nero o bianco più leggibile;
// tenui nelle altre, dove il testo resta quello del tema (contrasto alto nei due temi).
const CLASS_CELL: Record<string, { strong: CSSProperties; soft: CSSProperties }> = {};
for (const c of RESIDUE_CLASSES) {
  const cell = { strong: { background: c.color, color: textOn(c.color), fontWeight: 700 }, soft: { background: `${c.color}${CLASS_TINT}` } };
  for (const l of c.letters) CLASS_CELL[l] = cell;
}
const GAP_STYLE: CSSProperties = { color: "var(--muted)" };
const isRes = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

type Mode = "class" | "confidence" | "plain";
const MODES: [Mode, string][] = [["class", "Classe chimica"], ["confidence", "Confidenza"], ["plain", "Nessun colore"]];
const BLOCK_WIDTHS = [50, 60, 80, 100, 150];

type CellStyle = (ch: string, r: number, col: number) => { style?: CSSProperties; className?: string };

interface Props {
  ids: string[];
  aligned: string[];
  conservation?: string;
  width?: number;
  /** riga del bersaglio per la modalità "Confidenza" (PIR: la voce `sequence`); predefinita la prima */
  reference?: number;
  /** colorazione iniziale (l'anteprima per catena dei complessi e la procedura guidata partono da "Confidenza") */
  initialMode?: Mode;
  /** nome del file FASTA scaricato */
  filename?: string;
  /** pulsante "FASTA" (da togliere dove la pagina ha già i suoi formati di esportazione) */
  downloadable?: boolean;
}

export function AlignmentView({ ids, aligned, conservation, width = 60, reference = 0, initialMode = "class", filename = "allineamento", downloadable = true }: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [block, setBlock] = useState(width);
  const [expanded, setExpanded] = useState(false);
  const [hover, setHover] = useState<{ r: number; c: number } | null>(null);
  const length = aligned[0]?.length ?? 0;
  const cells = length * aligned.length;
  const canColor = cells <= 80_000;
  const ref = Math.min(Math.max(0, reference), Math.max(0, aligned.length - 1));
  const canConfidence = aligned.length > 1;
  const confidence = useMemo(
    () => (mode === "confidence" && canConfidence ? alignmentConfidence(aligned, ref) : null),
    [mode, canConfidence, aligned, ref],
  );
  const identical = useMemo(() => identicalColumns(aligned), [aligned]);
  // numero dell'ultimo residuo (senza gap) di ogni blocco, come nel formato Clustal
  const ends = useMemo(() => {
    const res: number[][] = aligned.map(() => []);
    aligned.forEach((s, r) => {
      let n = 0;
      for (let c = 0; c < length; c++) {
        if (!isGap(s[c])) n++;
        if ((c + 1) % block === 0 || c === length - 1) res[r].push(n);
      }
    });
    return res;
  }, [aligned, length, block]);
  const colored = canColor && mode !== "plain";

  const cellStyle = useCallback<CellStyle>((ch, r, col) => {
    if (confidence && canColor) {
      const column = confidence.columns[col];
      if (r === ref) {
        const cls = column.uncovered ? "aln-uncovered" : column.twilight ? "aln-twilight" : undefined;
        return { className: cls, style: column.best !== null ? { background: `${colorFor(IDENTITY_SCALE, column.best)}${CONFIDENCE_TINT}` } : isGap(ch) ? GAP_STYLE : undefined };
      }
      if (!isRes(ch)) return column.residue ? { className: "aln-gap" } : { style: GAP_STYLE };
      const v = confidence.rows[r]?.[col];
      return { style: v !== null && v !== undefined ? { background: `${colorFor(IDENTITY_SCALE, v)}${CONFIDENCE_TINT}` } : undefined };
    }
    const cell = colored ? CLASS_CELL[ch.toUpperCase()] : undefined;
    if (cell) return { style: identical[col] ? cell.strong : cell.soft };
    return { style: isGap(ch) ? GAP_STYLE : undefined };
  }, [confidence, canColor, colored, identical, ref]);
  // un nuovo stato solo quando cambia la cella sotto il puntatore
  const onHover = useCallback((cell: { r: number; c: number } | null) => {
    setHover((prev) => (prev === cell || (prev && cell && prev.r === cell.r && prev.c === cell.c) ? prev : cell));
  }, []);

  if (!aligned.length) return null;

  const download = () => downloadText(safeFilename(filename, "fasta"), alignmentFasta(ids, aligned));
  const controls = (big: boolean) => (
    <div className="row small">
      <div className="segmented" role="radiogroup" aria-label="Colorazione">
        {MODES.map(([m, label]) => (
          <button key={m} type="button" role="radio" aria-checked={m === mode} className={m === mode ? "on" : ""}
            disabled={(m === "class" && !canColor) || (m === "confidence" && !canConfidence)} onClick={() => setMode(m)}>
            {label}
          </button>
        ))}
      </div>
      <span className="text-2">{mode === "confidence" ? `Rispetto a ${ids[ref] ?? "bersaglio"}` : mode === "class" ? "Colonne identiche a colore pieno" : "Senza colori"} · {aligned.length} sequenze · {length} colonne</span>
      <span className="right row" style={{ gap: 6 }}>
        <label className="check">
          Larghezza blocco
          <select className="input input-sm" style={{ width: 80 }} value={block} onChange={(e) => setBlock(Number(e.target.value))}>
            {BLOCK_WIDTHS.map((w) => (
              <option key={w} value={w}>{w}</option>
            ))}
          </select>
        </label>
        {downloadable && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={download} title="Scarica l'allineamento in formato FASTA (con i gap)">
            <Icon name="download" size={14} /> FASTA
          </button>
        )}
        {!big && (
          <button type="button" className="btn btn-sm" onClick={() => setExpanded(true)} title="Apri l'allineamento a tutto schermo" aria-haspopup="dialog">
            <Icon name="maximize" size={14} /> Espandi
          </button>
        )}
      </span>
    </div>
  );
  const body = (big: boolean) => (
    <AlnBody ids={ids} aligned={aligned} conservation={conservation} block={block} ends={ends} cellStyle={cellStyle}
      bold={confidence ? ref : -1} expanded={big} onHover={onHover} />
  );
  const status = (
    <CellInfo ids={ids} aligned={aligned} conservation={conservation} identical={identical} confidence={confidence} reference={ref} hover={hover} />
  );
  const summary = confidence && <ConfidenceSummary confidence={confidence} ids={ids} reference={ref} />;
  const legend = (
    <>
      {colored && mode === "class" && <ClassLegend />}
      {confidence && <ConfidenceLegend canColor={canColor} />}
    </>
  );
  return (
    <div className="stack-sm">
      {controls(false)}
      {summary}
      {status}
      {body(false)}
      {legend}
      {expanded && (
        <Modal size="full" title="Allineamento" onClose={() => setExpanded(false)}>
          <div className="stack-sm">
            {controls(true)}
            {summary}
            {status}
            {body(true)}
            {legend}
          </div>
        </Modal>
      )}
    </div>
  );
}

/**
 * Righe dell'allineamento: righello numerato, sequenze, consenso. Oltre VIRTUAL_THRESHOLD righe (molte sequenze) si
 * disegnano solo quelle visibili (F8b). Il passaggio del mouse risale alla cella dalla posizione dello span nella riga.
 */
const AlnBody = memo(function AlnBody({ ids, aligned, conservation, block, ends, cellStyle, bold, expanded, onHover }: {
  ids: string[];
  aligned: string[];
  conservation?: string;
  block: number;
  ends: number[][];
  cellStyle: CellStyle;
  /** riga del bersaglio in evidenza (modalità Confidenza), −1 nessuna */
  bold: number;
  expanded: boolean;
  onHover: (cell: { r: number; c: number } | null) => void;
}) {
  const length = aligned[0]?.length ?? 0;
  // righe disegnate: per ogni blocco righello, sequenze, consenso e spazio prima del blocco successivo
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

  const span = (b: number) => [b * block, Math.min(length, b * block + block)] as const;
  const rulerRow = (b: number, key?: string) => {
    const [from, to] = span(b);
    return (
      <div className="aln-row aln-ruler" key={key} aria-hidden="true">
        <div className="aln-name" />
        <div className="aln-seq">
          {rulerText(from, to).split("").map((ch, i) => <span key={i}>{ch}</span>)}
          <span className="aln-end">{to}</span>
        </div>
      </div>
    );
  };
  const seqRow = (b: number, r: number, key?: string) => {
    const [from, to] = span(b);
    return (
      <div className={`aln-row${r === bold ? " aln-ref" : ""}`} key={key ?? r} data-r={r} data-b={b}>
        <div className="aln-name" title={ids[r]}>{ids[r]}</div>
        <div className="aln-seq">
          {aligned[r]
            .slice(from, to)
            .split("")
            .map((ch, i) => {
              const { style, className } = cellStyle(ch, r, from + i);
              return <span key={i} style={style} className={className}>{ch}</span>;
            })}
          <span className="aln-end">{ends[r][b]}</span>
        </div>
      </div>
    );
  };
  const consRow = (b: number, key?: string) => {
    const [from, to] = span(b);
    return (
      <div className="aln-row aln-cons" key={key}>
        <div className="aln-name">consenso</div>
        <div className="aln-seq">
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
  // cella sotto il puntatore: lo span è un figlio di .aln-seq, la riga porta sequenza (data-r) e blocco (data-b)
  const onMove = (e: RMouseEvent<HTMLDivElement>) => {
    const cell = e.target as HTMLElement;
    const seq = cell.parentElement;
    const row = seq?.parentElement;
    if (cell.tagName !== "SPAN" || !seq?.classList.contains("aln-seq") || row?.dataset.r === undefined) return onHover(null);
    const b = Number(row.dataset.b);
    const k = Array.prototype.indexOf.call(seq.children, cell);
    const c = b * block + k;
    onHover(k >= 0 && c < Math.min(length, (b + 1) * block) ? { r: Number(row.dataset.r), c } : null);
  };
  const style: CSSProperties | undefined = expanded
    ? { maxHeight: "calc(100vh - 250px)", overflowY: "auto" }
    : virtual.active ? { maxHeight: "70vh", overflowY: "auto" } : undefined;
  return (
    <>
      {virtual.active && <span className="tiny text-2">{aligned.length} sequenze: nel riquadro si disegnano solo le righe visibili (scorri per le altre).</span>}
      <div ref={virtual.ref} onScroll={virtual.onScroll} className="aln aln-readable" style={style} onMouseMove={onMove} onMouseLeave={() => onHover(null)}>
        {blocks}
      </div>
    </>
  );
});

/** Riga di stato: la posizione sotto il puntatore (colonna, residuo e suo numero, classe, conservazione, identità locale). */
function CellInfo({ ids, aligned, conservation, identical, confidence, reference, hover }: {
  ids: string[];
  aligned: string[];
  conservation?: string;
  identical: boolean[];
  confidence: AlignmentConfidence | null;
  reference: number;
  hover: { r: number; c: number } | null;
}) {
  if (!hover || !aligned[hover.r]) {
    return <div className="aln-status">Passa sull'allineamento per i dettagli di una posizione.</div>;
  }
  const { r, c } = hover;
  const ch = aligned[r][c] ?? "";
  const number = residueNumberAt(aligned[r], c);
  const cls = residueClass(ch);
  const symbol = conservation?.[c];
  const column = symbol && symbol !== " " ? CONSERVATION_TEXT[symbol] : identical[c] ? CONSERVATION_TEXT["*"] : "colonna non conservata";
  const local = confidence ? (r === reference ? confidence.columns[c]?.best : confidence.rows[r]?.[c]) : undefined;
  return (
    <div className="aln-status">
      Colonna <b>{c + 1}</b> · <b>{ids[r]}</b>: {number === null ? "gap" : <><b>{residueText(ch)}</b> {number}{cls ? ` · ${cls.one}` : ""}</>}
      {" · "}{column}
      {local !== undefined && <> · identità locale {local === null ? "—" : `${fmtNum(local, 0)}%`}{r === reference ? " (miglior template)" : ""}</>}
    </div>
  );
}

function ClassLegend() {
  return (
    <div className="legend aln-legend">
      {RESIDUE_CLASSES.map((c) => (
        <span key={c.letters}><i style={{ background: c.color }} />{c.label} <span className="mono">{c.letters}</span></span>
      ))}
      <span className="text-2">colore pieno: colonna identica in tutte le sequenze · tenue: le altre</span>
    </div>
  );
}

/**
 * Traccia e riepilogo della confidenza. `memo`: il passaggio del mouse sull'allineamento ridisegna la vista a ogni
 * cella, non la traccia (una colonna per div), che dipende solo dalla confidenza calcolata.
 */
const ConfidenceSummary = memo(function ConfidenceSummary({ confidence, ids, reference }: { confidence: AlignmentConfidence; ids: string[]; reference: number }) {
  const twilight = confidence.regions.filter((r) => r.kind === "twilight");
  const uncovered = confidence.regions.filter((r) => r.kind === "uncovered");
  return (
    <div className="stack-sm">
      <div className="aln-track" title={`Miglior identità locale per colonna (finestra ${CONFIDENCE_WINDOW})`}>
        {confidence.columns.map((c, i) => (
          <div key={i} style={{ background: !c.residue ? "transparent" : c.uncovered ? "var(--err)" : c.best !== null ? colorFor(IDENTITY_SCALE, c.best) : "var(--panel-3)" }} />
        ))}
      </div>
      <div className="small text-2">
        Identità locale su {CONFIDENCE_WINDOW} colonne rispetto a <b>{ids[reference]}</b>: media {fmtNum(confidence.summary.meanIdentity, 0)}% sui {confidence.summary.covered} residui coperti.
        {" "}Zona crepuscolare (&lt; {TWILIGHT_IDENTITY}%): {confidence.summary.twilight ? <b>{confidence.summary.twilight} residui ({twilight.map(regionLabel).join(", ")})</b> : "nessun residuo"}.
        {" "}Non coperti dal template: {confidence.summary.uncovered ? <b>{confidence.summary.uncovered} residui ({uncovered.map(regionLabel).join(", ")})</b> : "nessuno"}.
      </div>
    </div>
  );
});

function ConfidenceLegend({ canColor }: { canColor: boolean }) {
  const idLegend = legendFor(IDENTITY_SCALE, "Identità locale");
  if (idLegend.kind !== "gradient") return null;
  return (
    <div className="legend aln-legend">
      <span className="cmp-scale">
        Identità locale
        <span className="cmp-scale-bar" style={{ background: idLegend.css }} />
        {idLegend.ticks.map((t) => t.label).join(" · ")}
      </span>
      <span><i style={{ background: "rgba(248, 113, 113, 0.35)" }} />gap del template</span>
      <span><i style={{ background: "var(--err)" }} />bersaglio non coperto</span>
      <span><i style={{ background: "var(--warn)" }} />zona crepuscolare (sottolineato)</span>
      {!canColor && <span className="text-2">allineamento troppo grande per colorare le celle: resta la traccia riassuntiva</span>}
    </div>
  );
}
