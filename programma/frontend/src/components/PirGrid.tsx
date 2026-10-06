import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as RKeyboardEvent, type MouseEvent as RMouseEvent } from "react";
import { CLASS_TINT } from "../lib/alignmentView";
import { RESIDUE_CLASSES, residueClass } from "../lib/aminoAcids";
import { residueText } from "../lib/compareMetrics";
import { deleteGaps, insertGaps, isRagged, normSel, pirResidueNumber, removeGapColumns, slideBlock, type Selection } from "../lib/pirEdit";
import { Icon } from "./Icon";
import "../styles/pir.css";

// Geometria fissa delle celle: la colonna sotto il puntatore si ricava dalla posizione, senza misurare gli span.
const CW = 14;
const RH = 22;
const NAME_W = 170;
/** colonne disegnate oltre i bordi visibili (scorrimento orizzontale senza vuoti) */
const OVERSCAN = 24;

const TINT: Record<string, CSSProperties> = {};
for (const c of RESIDUE_CLASSES) for (const l of c.letters) TINT[l] = { background: `${c.color}${CLASS_TINT}` };

interface Props {
  ids: string[];
  kinds: string[];
  rows: string[];
  /** nuove righe dopo una modifica (la pagina le riscrive nel PIR e le registra nella cronologia) */
  onEdit: (rows: string[]) => void;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

/**
 * Griglia modificabile dell'allineamento PIR: una riga per voce, una cella per colonna. Si spostano solo gap
 * (lib/pirEdit): Spazio o "-" inserisce, Canc / Backspace toglie, Alt+frecce fa scorrere il blocco selezionato.
 * Le colonne fuori vista non si disegnano: allineamenti lunghi restano fluidi.
 */
export function PirGrid({ ids, kinds, rows, onEdit, onUndo, onRedo, canUndo, canRedo }: Props) {
  const width = Math.max(0, ...rows.map((s) => s.length));
  const [anchor, setAnchor] = useState({ r: 0, c: 0 });
  const [cursor, setCursor] = useState({ r: 0, c: 0 });
  const [view, setView] = useState({ left: 0, width: 1200 });
  const [message, setMessage] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const drag = useRef(false);

  const sel: Selection = normSel({ r0: anchor.r, c0: anchor.c, r1: cursor.r, c1: cursor.c });
  const clampR = useCallback((r: number) => Math.min(Math.max(0, r), Math.max(0, rows.length - 1)), [rows.length]);
  const clampC = useCallback((c: number) => Math.min(Math.max(0, c), Math.max(0, width - 1)), [width]);

  // la selezione resta dentro l'allineamento quando le righe cambiano (colonne finali tolte, altro documento)
  useEffect(() => {
    setAnchor((a) => ({ r: clampR(a.r), c: clampC(a.c) }));
    setCursor((a) => ({ r: clampR(a.r), c: clampC(a.c) }));
  }, [clampR, clampC]);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const measure = () => setView({ left: box.scrollLeft, width: box.clientWidth });
    measure();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(box);
    return () => ro?.disconnect();
  }, []);

  /** Porta in vista la colonna del cursore scorrendo solo la griglia. */
  const reveal = (c: number, r: number) => {
    const box = boxRef.current;
    if (!box) return;
    const x = c * CW;
    const visible = box.clientWidth - NAME_W;
    if (x < box.scrollLeft) box.scrollLeft = Math.max(0, x - CW * 4);
    else if (x + CW > box.scrollLeft + visible) box.scrollLeft = x + CW * 5 - visible;
    const y = (r + 1) * RH;
    if (y < box.scrollTop + RH) box.scrollTop = Math.max(0, y - RH * 2);
    else if (y + RH > box.scrollTop + box.clientHeight) box.scrollTop = y + RH * 2 - box.clientHeight;
  };

  const moveTo = (r: number, c: number, extend: boolean) => {
    const next = { r: clampR(r), c: clampC(c) };
    setCursor(next);
    if (!extend) setAnchor(next);
    reveal(next.c, next.r);
  };

  /** Sposta la selezione di `dc` colonne dopo una modifica (cursore e ancora insieme). */
  const shiftSel = (dc: number) => {
    setAnchor((a) => ({ ...a, c: Math.max(0, a.c + dc) }));
    setCursor((a) => ({ ...a, c: Math.max(0, a.c + dc) }));
    reveal(Math.max(0, cursor.c + dc), cursor.r);
  };

  const edit = (next: string[] | null, refusal: string, dc = 0) => {
    if (!next) {
      setMessage(refusal);
      return false;
    }
    setMessage(null);
    onEdit(next);
    if (dc) shiftSel(dc);
    return true;
  };

  const actions = {
    insert: () => edit(insertGaps(rows, sel, sel.c0), "", 1),
    deleteAt: () => edit(deleteGaps(rows, sel, sel.c0), "Nessun gap da togliere nella colonna selezionata."),
    deleteBefore: () => sel.c0 > 0
      ? edit(deleteGaps(rows, sel, sel.c0 - 1), "Nessun gap da togliere prima della selezione.", -1)
      : (setMessage("Sei alla prima colonna."), false),
    slide: (dir: -1 | 1) => edit(slideBlock(rows, sel, dir), dir < 0 ? "Il blocco non può andare a sinistra: c'è un residuo o il bordo." : "Il blocco non può andare a destra: c'è un residuo di fronte.", dir),
    squeeze: () => edit(removeGapColumns(rows), "Nessuna colonna fatta solo di gap."),
  };

  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    const mod = e.ctrlKey || e.metaKey;
    const step = mod ? 10 : 1;
    let handled = true;
    const k = e.key;
    if (mod && (k === "z" || k === "Z")) e.shiftKey ? onRedo() : onUndo();
    else if (mod && (k === "y" || k === "Y")) onRedo();
    else if (mod && (k === "a" || k === "A")) {
      setAnchor({ r: 0, c: 0 });
      setCursor({ r: clampR(rows.length - 1), c: clampC(width - 1) });
    } else if (e.altKey && k === "ArrowLeft") actions.slide(-1);
    else if (e.altKey && k === "ArrowRight") actions.slide(1);
    else if (k === "ArrowLeft") moveTo(cursor.r, cursor.c - step, e.shiftKey);
    else if (k === "ArrowRight") moveTo(cursor.r, cursor.c + step, e.shiftKey);
    else if (k === "ArrowUp") moveTo(cursor.r - 1, cursor.c, e.shiftKey);
    else if (k === "ArrowDown") moveTo(cursor.r + 1, cursor.c, e.shiftKey);
    else if (k === "Home") moveTo(cursor.r, 0, e.shiftKey);
    else if (k === "End") moveTo(cursor.r, width - 1, e.shiftKey);
    else if (k === " " || k === "-") actions.insert();
    else if (k === "Delete") actions.deleteAt();
    else if (k === "Backspace") actions.deleteBefore();
    else if (k === "Escape") setAnchor(cursor);
    else handled = false;
    if (handled) {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  /** Cella sotto il puntatore; colonna −1 sul nome della voce. */
  const cellAt = (e: { clientX: number; clientY: number }) => {
    const rect = bodyRef.current?.getBoundingClientRect();
    const box = boxRef.current;
    if (!rect || !box) return null;
    const r = clampR(Math.floor((e.clientY - rect.top) / RH));
    const xName = e.clientX - box.getBoundingClientRect().left;
    if (xName < NAME_W) return { r, c: -1 };
    return { r, c: clampC(Math.floor((e.clientX - rect.left - NAME_W) / CW)) };
  };

  const onMouseDown = (e: RMouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const cell = cellAt(e);
    if (!cell) return;
    boxRef.current?.focus();
    e.preventDefault();
    if (cell.c < 0) {
      // clic sul nome: tutta la riga (con Maiusc fino alla riga del cursore)
      setAnchor({ r: e.shiftKey ? anchor.r : cell.r, c: 0 });
      setCursor({ r: cell.r, c: clampC(width - 1) });
      return;
    }
    setCursor(cell);
    if (!e.shiftKey) setAnchor(cell);
    drag.current = true;
  };

  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!drag.current) return;
      const cell = cellAt(e);
      if (cell && cell.c >= 0) setCursor(cell);
    };
    const up = () => (drag.current = false);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  });

  const c0 = Math.max(0, Math.floor(view.left / CW) - OVERSCAN);
  const c1 = Math.min(width, Math.ceil((view.left + view.width) / CW) + OVERSCAN);
  const ruler = useMemo(() => {
    const marks: { c: number; label: string }[] = [];
    for (let c = Math.ceil((c0 + 1) / 10) * 10; c <= c1; c += 10) marks.push({ c: c - 1, label: String(c) });
    return marks;
  }, [c0, c1]);

  const ch = rows[cursor.r]?.[cursor.c] ?? "";
  const number = pirResidueNumber(rows[cursor.r] ?? "", cursor.c);
  const cls = residueClass(ch);
  const selCols = sel.c1 - sel.c0 + 1;
  const selRows = sel.r1 - sel.r0 + 1;

  return (
    <div className="stack-sm">
      <div className="row small pirg-tools">
        <button type="button" className="btn btn-sm" disabled={!canUndo} onClick={onUndo} title="Annulla (Ctrl+Z)"><Icon name="undo" size={14} /> Annulla</button>
        <button type="button" className="btn btn-sm" disabled={!canRedo} onClick={onRedo} title="Ripristina (Ctrl+Y)"><Icon name="redo" size={14} /> Ripristina</button>
        <span className="pirg-sep" />
        <button type="button" className="btn btn-sm" onClick={actions.insert} title="Inserisce un gap prima della selezione nelle righe selezionate (Spazio o -)">+ gap</button>
        <button type="button" className="btn btn-sm" onClick={actions.deleteAt} title="Toglie il gap nella prima colonna della selezione (Canc)">− gap</button>
        <button type="button" className="btn btn-sm" onClick={() => actions.slide(-1)} title="Sposta il blocco selezionato a sinistra sopra un gap (Alt+←)">← blocco</button>
        <button type="button" className="btn btn-sm" onClick={() => actions.slide(1)} title="Sposta il blocco selezionato a destra sopra un gap (Alt+→)">blocco →</button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={actions.squeeze} title="Toglie le colonne in cui tutte le voci hanno un gap">Togli colonne di soli gap</button>
      </div>
      {isRagged(rows) && <div className="small" style={{ color: "var(--warn)" }}>Le voci hanno lunghezze diverse: la prima modifica le pareggia con gap in coda.</div>}
      <div
        ref={boxRef}
        className="pirg"
        tabIndex={0}
        role="grid"
        aria-label="Allineamento modificabile: frecce per muoversi, Spazio inserisce un gap, Canc lo toglie, Alt+frecce sposta il blocco"
        aria-rowcount={rows.length}
        aria-colcount={width}
        onKeyDown={onKeyDown}
        onScroll={(e) => setView({ left: e.currentTarget.scrollLeft, width: e.currentTarget.clientWidth })}
        style={{ "--cw": `${CW}px`, "--rh": `${RH}px`, "--name-w": `${NAME_W}px` } as CSSProperties}
      >
        <div className="pirg-ruler" style={{ width: NAME_W + width * CW }} aria-hidden="true">
          <div className="pirg-name">colonna</div>
          {ruler.map((m) => (
            <span key={m.c} style={{ left: NAME_W + m.c * CW }}>{m.label}</span>
          ))}
        </div>
        <div ref={bodyRef} className="pirg-body" style={{ width: NAME_W + width * CW, height: rows.length * RH }} onMouseDown={onMouseDown}>
          {rows.map((s, r) => (
            <div key={r} className={`pirg-row${kinds[r] === "sequence" ? " pirg-target" : ""}`} style={{ top: r * RH }}>
              <div className="pirg-name" title={`${ids[r]} · ${kinds[r]}`}>
                <span className="pirg-kind">{kinds[r] === "sequence" ? "bersaglio" : "template"}</span>{ids[r]}
              </div>
              <div className="pirg-seq" style={{ left: NAME_W + c0 * CW }}>
                {s.slice(c0, c1).split("").map((x, i) => (
                  <span key={c0 + i} style={TINT[x]} className={x === "-" ? "pirg-gap" : x === "/" || x === "." ? "pirg-mark" : undefined}>{x}</span>
                ))}
              </div>
            </div>
          ))}
          <div className="pirg-sel" aria-hidden="true"
            style={{ top: sel.r0 * RH, left: NAME_W + sel.c0 * CW, width: selCols * CW, height: selRows * RH }} />
          <div className="pirg-cursor" aria-hidden="true" style={{ top: cursor.r * RH, left: NAME_W + cursor.c * CW }} />
        </div>
      </div>
      <div className="aln-status" aria-live="polite">
        Colonna <b>{cursor.c + 1}</b> di {width} · <b>{ids[cursor.r]}</b>: {number === null ? (ch === "/" ? "separatore di catena" : ch === "." ? "blocco di ligando" : "gap") : <><b>{residueText(ch)}</b> {number}{cls ? ` · ${cls.one}` : ""}</>}
        {(selCols > 1 || selRows > 1) && <> · selezione {selRows} × {selCols}</>}
        {message && <> · <span style={{ color: "var(--warn)" }}>{message}</span></>}
      </div>
      <div className="tiny text-2">
        Frecce: muovi · Maiusc+frecce o trascina: seleziona · clic sul nome: tutta la riga · Spazio / -: inserisci gap · Canc / Backspace: togli gap · Alt+← →: sposta il blocco · Ctrl+Z / Ctrl+Y: annulla / ripristina
      </div>
    </div>
  );
}
