import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as RKeyboardEvent, type MouseEvent as RMouseEvent } from "react";
import { CLASS_TINT } from "../lib/alignmentView";
import { RESIDUE_CLASSES, residueClass } from "../lib/aminoAcids";
import { residueText } from "../lib/compareMetrics";
import { AMINO } from "../lib/mutations";
import { deleteGaps, insertGaps, isGapBlock, isRagged, normSel, removeGapColumns, sameRows, slideBlock, squareUp, type Selection } from "../lib/pirEdit";
import { blosum62, blosumTone, isStandardAa, residuePositions, substitute } from "../lib/pirMutations";
import { conflictText, type GapConflict, type SS3 } from "../lib/pirSecondary";
import { dsspLabel } from "../lib/qualityView";
import { SS3_LABEL } from "../lib/residues";
import { Icon } from "./Icon";
import "../styles/pir.css";

// Geometria fissa delle celle: la colonna sotto il puntatore si ricava dalla posizione, senza misurare gli span.
const CW = 14;
const RH = 22;
const NAME_W = 170;
/** altezza della traccia della struttura secondaria sotto ogni riga */
const TH = 12;
/** colonne disegnate oltre i bordi visibili (scorrimento orizzontale senza vuoti) */
const OVERSCAN = 24;

const TINT: Record<string, CSSProperties> = {};
for (const c of RESIDUE_CLASSES) for (const l of c.letters) TINT[l] = { background: `${c.color}${CLASS_TINT}` };

/** Struttura secondaria da disegnare sotto le righe (una voce per riga dell'allineamento). */
export interface GridSecondary {
  /** stati per colonna; null = nessuna struttura secondaria per quella voce */
  states: (SS3[] | null)[];
  /** codici DSSP a 8 stati per colonna (dettaglio nella riga di stato) */
  codes: (string[] | null)[];
  /** "dssp" (template), "gor" (predizione del bersaglio) o null */
  sources: (string | null)[];
  /** gap che spezzano un'elica o un filamento di un template */
  conflicts: GapConflict[];
}

/** Richiesta di portare il focus sulla griglia, e il cursore su una cella se indicata (`n` cambia a ogni richiesta). */
export interface GridFocus {
  r?: number;
  c?: number;
  n: number;
}

interface Props {
  ids: string[];
  kinds: string[];
  /** seconde righe PIR delle voci: numerazione e nomi delle catene nella riga di stato */
  headers: string[];
  rows: string[];
  /** nuove righe dopo una modifica (la pagina le riscrive nel PIR e le registra nella cronologia) */
  onEdit: (rows: string[]) => void;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  /** i residui dei template si possono sostituire (di norma no: devono corrispondere al file della struttura) */
  templatesEditable: boolean;
  /** richiesta di sbloccare (la pagina chiede conferma) o di bloccare di nuovo i template */
  onTemplatesEditable: (on: boolean) => void;
  /** celle con un residuo diverso dall'originale, come "riga:colonna" */
  mutated?: Set<string>;
  focus?: GridFocus | null;
  /** la richiesta di focus è stata eseguita (la pagina la azzera, così non si ripete quando la griglia ricompare) */
  onFocusDone?: () => void;
  /** tracce della struttura secondaria (null o assente: nessuna traccia) */
  secondary?: GridSecondary | null;
}

const isLocked = (kind: string | undefined, templatesEditable: boolean) => !templatesEditable && !!kind?.startsWith("structure");
const widthOf = (rows: string[]) => Math.max(0, ...rows.map((s) => s.length));

/**
 * Griglia modificabile dell'allineamento PIR: una riga per voce, una cella per colonna. Si spostano solo gap
 * (lib/pirEdit): Spazio o "-" inserisce, Canc / Backspace toglie, Alt+frecce fa scorrere il blocco selezionato; una
 * lettera sostituisce il residuo sotto il cursore. Le colonne fuori vista non si disegnano: allineamenti lunghi restano
 * fluidi. Il focus resta sulla griglia anche dopo i pulsanti, così le lettere non finiscono alle scorciatoie globali.
 */
export function PirGrid({ ids, kinds, headers, rows, onEdit, onUndo, onRedo, canUndo, canRedo, templatesEditable, onTemplatesEditable, mutated, focus, onFocusDone, secondary }: Props) {
  const width = widthOf(rows);
  // passo verticale fra le righe: con la struttura secondaria ogni riga ha la sua traccia sotto
  const stride = secondary ? RH + TH : RH;
  const [anchor, setAnchor] = useState({ r: 0, c: 0 });
  const [cursor, setCursor] = useState({ r: 0, c: 0 });
  const [view, setView] = useState({ left: 0, width: 1200 });
  // messaggio di rifiuto: `n` cambia a ogni messaggio, così lo stesso rifiuto ripetuto viene riletto
  const [message, setMessage] = useState<{ text: string; n: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const drag = useRef(false);
  const pendingReveal = useRef(false);
  const statusId = useId();

  const sel: Selection = normSel({ r0: anchor.r, c0: anchor.c, r1: cursor.r, c1: cursor.c });
  const clampR = useCallback((r: number) => Math.min(Math.max(0, r), Math.max(0, rows.length - 1)), [rows.length]);
  const clampC = useCallback((c: number) => Math.min(Math.max(0, c), Math.max(0, width - 1)), [width]);
  const say = (text: string | null) => setMessage((m) => (text ? { text, n: (m?.n ?? 0) + 1 } : null));

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

  /** Porta in vista la cella scorrendo solo la griglia. */
  const reveal = (c: number, r: number) => {
    const box = boxRef.current;
    if (!box) return;
    const x = c * CW;
    const visible = box.clientWidth - NAME_W;
    if (x < box.scrollLeft) box.scrollLeft = Math.max(0, x - CW * 4);
    else if (x + CW > box.scrollLeft + visible) box.scrollLeft = x + CW * 5 - visible;
    const y = RH + r * stride; // il righello sta sopra le righe
    if (y < box.scrollTop + RH) box.scrollTop = Math.max(0, y - RH - stride);
    else if (y + stride > box.scrollTop + box.clientHeight) box.scrollTop = y + stride * 2 - box.clientHeight;
  };

  // dopo una modifica la griglia può essersi allargata: il cursore si porta in vista a DOM aggiornato
  useLayoutEffect(() => {
    if (!pendingReveal.current) return;
    pendingReveal.current = false;
    reveal(cursor.c, cursor.r);
  });

  useEffect(() => {
    if (!focus) return;
    if (focus.r !== undefined && focus.c !== undefined) {
      const cell = { r: clampR(focus.r), c: clampC(focus.c) };
      setAnchor(cell);
      setCursor(cell);
      pendingReveal.current = true;
      boxRef.current?.scrollIntoView({ block: "nearest" });
    }
    boxRef.current?.focus({ preventScroll: true });
    onFocusDone?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  const refocus = () => boxRef.current?.focus({ preventScroll: true });

  const moveTo = (r: number, c: number, extend: boolean) => {
    const next = { r: clampR(r), c: clampC(c) };
    setCursor(next);
    if (!extend) setAnchor(next);
    say(null);
    reveal(next.c, next.r);
  };

  /** Applica una modifica (pareggiando le righe); `dc` sposta la selezione, entro la nuova larghezza. */
  const edit = (next: string[] | null, refusal: string, dc = 0) => {
    const squared = next ? squareUp(next) : null;
    if (!squared || sameRows(squared, rows)) {
      say(refusal || "Nessun cambiamento.");
      return false;
    }
    say(null);
    onEdit(squared);
    if (dc) {
      const last = Math.max(0, widthOf(squared) - 1);
      const shift = (a: { r: number; c: number }) => ({ ...a, c: Math.min(last, Math.max(0, a.c + dc)) });
      setAnchor(shift);
      setCursor(shift);
      pendingReveal.current = true;
    }
    return true;
  };

  const actions = {
    insert: () => edit(insertGaps(rows, sel, sel.c0), "Un gap in coda non cambia l'allineamento.", 1),
    deleteAt: () => edit(deleteGaps(rows, sel, sel.c0), "Nessun gap da togliere nella colonna selezionata."),
    deleteBefore: () => sel.c0 > 0
      ? edit(deleteGaps(rows, sel, sel.c0 - 1), "Nessun gap da togliere prima della selezione.", -1)
      : (say("Sei alla prima colonna."), false),
    slide: (dir: -1 | 1) => edit(slideBlock(rows, sel, dir), isGapBlock(rows, sel)
      ? "I gap non scavalcano il bordo, un separatore di catena «/» o un ligando «.»."
      : dir < 0 ? "Il blocco non può andare a sinistra: c'è un residuo o il bordo." : "Il blocco non può andare a destra: c'è un residuo di fronte.", dir),
    squeeze: () => edit(removeGapColumns(rows), "Nessuna colonna fatta solo di gap."),
    /** sostituisce il residuo sotto il cursore */
    substitute: (aa: string) => {
      const up = aa.toUpperCase();
      if (!isStandardAa(up)) return edit(null, `«${up}» non è uno dei 20 amminoacidi standard.`);
      if (isLocked(kinds[cursor.r], templatesEditable)) {
        return edit(null, "I residui dei template sono bloccati: devono corrispondere al file della struttura. Sbloccali dalla barra solo se serve davvero.");
      }
      const cur = rows[cursor.r]?.[cursor.c];
      if (!cur || !/[A-Za-z]/.test(cur)) return edit(null, "Qui non c'è un residuo: si sostituiscono solo i residui (per aggiungerne usa il testo PIR).");
      if (cur.toUpperCase() === up) return edit(null, `Il residuo è già ${residueText(up)}.`);
      return edit(substitute(rows, cursor.r, cursor.c, up), "");
    },
  };
  /** azione da un pulsante: poi il focus torna alla griglia */
  const run = (f: () => unknown) => () => {
    f();
    refocus();
  };

  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    const mod = e.ctrlKey || e.metaKey;
    const step = mod ? 10 : 1;
    let handled = true;
    const k = e.key;
    if (e.nativeEvent.isComposing) return;
    if (mod && (k === "z" || k === "Z")) e.shiftKey ? onRedo() : onUndo();
    else if (mod && (k === "y" || k === "Y")) onRedo();
    else if (mod && (k === "a" || k === "A")) {
      setAnchor({ r: 0, c: 0 });
      setCursor({ r: clampR(rows.length - 1), c: clampC(width - 1) });
      say(null);
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
    else if (k === "Escape") {
      setAnchor(cursor);
      say(null);
    } else if (!mod && !e.altKey && k.length === 1 && /[a-z]/i.test(k)) actions.substitute(k);
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
    const r = clampR(Math.floor((e.clientY - rect.top) / stride));
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
    say(null);
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

  const cursorRow = rows[cursor.r] ?? "";
  const cursorHeader = headers[cursor.r] ?? "";
  const positions = useMemo(() => residuePositions(cursorRow, cursorHeader), [cursorRow, cursorHeader]);
  const ch = cursorRow[cursor.c] ?? "";
  const pos = positions[cursor.c] ?? null;
  const cls = residueClass(ch);
  const selCols = sel.c1 - sel.c0 + 1;
  const selRows = sel.r1 - sel.r0 + 1;
  const ssText = secondary ? secondaryText(secondary, cursor.r, cursor.c) : null;
  // gap che spezza un elemento in questa colonna: del template sotto il cursore, o di un template qualsiasi se il cursore è sul bersaglio
  const conflict = secondary?.conflicts.find((x) => x.row === cursor.r && x.from <= cursor.c && cursor.c <= x.to)
    ?? secondary?.conflicts.find((x) => kinds[cursor.r] === "sequence" && x.from <= cursor.c && cursor.c <= x.to);

  return (
    <div className="stack-sm">
      <div className="row small pirg-tools">
        <button type="button" className="btn btn-sm" disabled={!canUndo} onClick={run(onUndo)} title="Annulla (Ctrl+Z)"><Icon name="undo" size={14} /> Annulla</button>
        <button type="button" className="btn btn-sm" disabled={!canRedo} onClick={run(onRedo)} title="Ripristina (Ctrl+Y)"><Icon name="redo" size={14} /> Ripristina</button>
        <span className="pirg-sep" />
        <button type="button" className="btn btn-sm" onClick={run(actions.insert)} title="Inserisce un gap prima della selezione nelle righe selezionate (Spazio o -)">+ gap</button>
        <button type="button" className="btn btn-sm" onClick={run(actions.deleteAt)} title="Toglie il gap nella prima colonna della selezione (Canc)">− gap</button>
        <button type="button" className="btn btn-sm" onClick={run(() => actions.slide(-1))} title="Sposta il blocco selezionato a sinistra sopra un gap; un blocco di soli gap scavalca i residui (Alt+←)">← blocco</button>
        <button type="button" className="btn btn-sm" onClick={run(() => actions.slide(1))} title="Sposta il blocco selezionato a destra sopra un gap; un blocco di soli gap scavalca i residui (Alt+→)">blocco →</button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={run(actions.squeeze)} title="Toglie le colonne in cui tutte le voci hanno un gap">Togli colonne di soli gap</button>
        <label className="check right" title="I residui dei template devono corrispondere al file della struttura: sbloccali solo per correggere un PIR che non corrisponde già">
          <input type="checkbox" checked={templatesEditable} onChange={(e) => onTemplatesEditable(e.target.checked)} />
          Residui dei template modificabili
        </label>
      </div>
      {isRagged(rows) && <div className="small" style={{ color: "var(--warn)" }}>Le voci hanno lunghezze diverse: la prima modifica le pareggia con gap in coda.</div>}
      <div
        ref={boxRef}
        className="pirg"
        tabIndex={0}
        role="application"
        aria-roledescription="griglia dell'allineamento"
        aria-label="Allineamento modificabile: frecce per muoversi, Spazio inserisce un gap, Canc lo toglie, Alt+frecce sposta il blocco, una lettera sostituisce il residuo"
        aria-describedby={statusId}
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
        <div ref={bodyRef} className="pirg-body" style={{ width: NAME_W + width * CW, height: rows.length * stride }} onMouseDown={onMouseDown} aria-hidden="true">
          {secondary && rows.map((_s, r) => (
            <Track key={`t${r}`} top={r * stride + RH} c0={c0} c1={c1} states={secondary.states[r]} source={secondary.sources[r]}
              conflicts={secondary.conflicts.filter((x) => x.row === r)} />
          ))}
          {rows.map((s, r) => (
            <div key={r} className={`pirg-row${kinds[r] === "sequence" ? " pirg-target" : ""}`} style={{ top: r * stride }}>
              <div className="pirg-name" title={`${ids[r]} · ${kinds[r]}`}>
                <span className="pirg-kind">{kinds[r] === "sequence" ? "bersaglio" : "template"}</span>{ids[r]}
              </div>
              <div className="pirg-seq" style={{ left: NAME_W + c0 * CW }}>
                {s.slice(c0, c1).split("").map((x, i) => (
                  <span key={c0 + i} style={TINT[x.toUpperCase()]}
                    className={x === "-" ? "pirg-gap" : x === "/" || x === "." ? "pirg-mark" : mutated?.has(`${r}:${c0 + i}`) ? "pirg-mut" : undefined}>{x}</span>
                ))}
              </div>
            </div>
          ))}
          <div className="pirg-sel" style={{ top: sel.r0 * stride, left: NAME_W + sel.c0 * CW, width: selCols * CW, height: (selRows - 1) * stride + RH }} />
          <div className="pirg-cursor" style={{ top: cursor.r * stride, left: NAME_W + cursor.c * CW }} />
        </div>
      </div>
      <div className="aln-status" id={statusId} aria-live="polite" aria-atomic="true">
        Colonna <b>{cursor.c + 1}</b> di {width} · <b>{ids[cursor.r]}</b>:{" "}
        {pos === null
          ? (ch === "/" ? "separatore di catena" : ch === "." ? "blocco di ligando" : "gap")
          : <>{pos.multichain ? <>catena <b>{pos.chain}</b> · </> : null}<b>{residueText(ch)}</b> {pos.number}{cls ? ` · ${cls.one}` : ""}</>}
        {ssText && <> · {ssText}</>}
        {conflict && <> · <span className="pirg-conflict-text">{conflictText(conflict)} di {ids[conflict.row]}</span></>}
        {(selCols > 1 || selRows > 1) && <> · selezione {selRows} × {selCols}</>}
        {message && <span key={message.n}> · <span style={{ color: "var(--warn)" }}>{message.text}</span></span>}
      </div>
      <Palette current={ch} locked={isLocked(kinds[cursor.r], templatesEditable)}
        onPick={(aa) => {
          actions.substitute(aa);
          refocus();
        }} />
      <div className="tiny text-2">
        Frecce: muovi · Maiusc+frecce o trascina: seleziona · clic sul nome: tutta la riga · Spazio / -: inserisci gap · Canc / Backspace: togli gap · Alt+← →: sposta il blocco · una lettera: sostituisci il residuo · Ctrl+Z / Ctrl+Y: annulla / ripristina
      </div>
    </div>
  );
}

const TONE_TEXT = { conservative: "conservativa", neutral: "neutra", radical: "radicale", same: "" } as const;
const signed = (n: number) => (n > 0 ? `+${n}` : String(n).replace("-", "−"));

/**
 * Tavolozza delle sostituzioni per il residuo sotto il cursore: i 20 amminoacidi raggruppati per classe chimica, con il
 * punteggio BLOSUM62 dal residuo attuale (positivo = sostituzione frequente fra proteine omologhe).
 */
function Palette({ current, locked, onPick }: { current: string; locked: boolean; onPick: (aa: string) => void }) {
  const wt = current.toUpperCase();
  const residue = /^[A-Z]$/.test(wt);
  const reason = !residue ? "porta il cursore su un residuo" : locked ? "residuo di un template: bloccato" : null;
  return (
    <div className="pirg-palette" role="group" aria-label="Sostituzione del residuo sotto il cursore">
      <div className="pirg-palette-head small">
        <b>Sostituisci</b>
        {residue && <span className="mono">{residueText(wt)}</span>}
        <span className="text-2">{reason ?? "clic su un amminoacido o premi la sua lettera · BLOSUM62 dal residuo attuale: + conservativa, − radicale"}</span>
      </div>
      <div className="pirg-palette-groups">
        {RESIDUE_CLASSES.map((cls) => (
          <div key={cls.letters} className="pirg-palette-group">
            <span className="tiny text-2"><i style={{ background: cls.color }} />{cls.label}</span>
            <div className="row" style={{ gap: 4 }}>
              {cls.letters.split("").map((aa) => {
                const same = aa === wt;
                const score = residue && !same ? blosum62(wt, aa) : null;
                const tone = same ? "same" : blosumTone(score);
                return (
                  <button key={aa} type="button" className={`pirg-aa pirg-aa-${tone}`} disabled={!!reason || same} onClick={() => onPick(aa)}
                    title={same ? `${AMINO[aa]}: residuo attuale` : `${AMINO[aa]}${score !== null ? ` · BLOSUM62 ${signed(score)} (${TONE_TEXT[tone]})` : ""}`}>
                    <b>{aa}</b>
                    <span>{same ? "attuale" : score === null ? " " : signed(score)}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Stato della struttura secondaria nella cella, per la riga di stato. */
function secondaryText(secondary: GridSecondary, r: number, c: number): string | null {
  const state = secondary.states[r]?.[c];
  if (!state || state === " ") return null;
  if (state === "?") return "struttura secondaria sconosciuta";
  if (secondary.sources[r] === "dssp") return `${dsspLabel(secondary.codes[r]?.[c] ?? state)} (DSSP)`;
  return `${SS3_LABEL[state]} (predetta)`;
}

/** Tratti consecutivi con lo stesso valore nelle colonne [c0, c1). */
function runs<T>(values: T[], c0: number, c1: number, skip: (v: T) => boolean): { from: number; to: number; value: T }[] {
  const out: { from: number; to: number; value: T }[] = [];
  for (let c = c0; c < c1; c++) {
    const v = values[c];
    if (v === undefined || skip(v)) continue;
    const last = out[out.length - 1];
    if (last && last.value === v && last.to === c - 1) last.to = c;
    else out.push({ from: c, to: c, value: v });
  }
  return out;
}

const SS_CLASS: Record<string, string> = { H: "pirg-ss-h", E: "pirg-ss-e", C: "pirg-ss-c", "?": "pirg-ss-u" };

/** Traccia della struttura secondaria sotto una riga: eliche, filamenti, coil, e i gap che spezzano un elemento. */
function Track({ top, c0, c1, states, source, conflicts }: { top: number; c0: number; c1: number; states: SS3[] | null; source: string | null; conflicts: GapConflict[] }) {
  return (
    <div className={`pirg-track${source === "gor" ? " pirg-track-pred" : ""}`} style={{ top }}>
      <div className="pirg-track-name">{source === "dssp" ? "DSSP" : source === "gor" ? "predetta (GOR)" : "—"}</div>
      {states && runs(states, c0, c1, (v) => v === " ").map((x) => (
        <i key={x.from} className={SS_CLASS[x.value]} style={{ left: NAME_W + x.from * CW, width: (x.to - x.from + 1) * CW }} />
      ))}
      {conflicts.filter((x) => x.to >= c0 && x.from < c1).map((x) => (
        <b key={`k${x.from}`} className="pirg-ss-conflict" title={conflictText(x)} style={{ left: NAME_W + x.from * CW, width: (x.to - x.from + 1) * CW }} />
      ))}
    </div>
  );
}
