import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as RKeyboardEvent, type MouseEvent as RMouseEvent } from "react";
import { CLASS_TINT } from "../lib/alignmentView";
import { RESIDUE_CLASSES, residueClass } from "../lib/aminoAcids";
import { residueText } from "../lib/compareMetrics";
import { AMINO } from "../lib/mutations";
import { COLOR_MODES, columnConservation, conservationLevel, hydroClass, type ColorMode } from "../lib/pirColors";
import { deleteGaps, insertGaps, insertMarkerColumn, isGapBlock, isRagged, normSel, removeGapColumns, sameRows, slideBlock, squareUp, type Selection } from "../lib/pirEdit";
import { blosum62, blosumTone, isStandardAa, residuePositions, substitute } from "../lib/pirMutations";
import { issueSummary, issuesAt, nextIssue, type PirIssue } from "../lib/pirIssues";
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
/** colonne per blocco: la riga standard dei file PIR scritti da MODELLER */
export const BLOCK = 75;
/** spazio fra un blocco e il successivo */
const BG = 14;

/** altezza dell'istogramma della conservazione sotto il righello */
const HH = 14;
const COLOR_PREF = "bioloom.pir.colors";

// tinte di fondo delle altre colorazioni (testo nel colore del tema, come le tinte tenui delle classi)
const IDENT: CSSProperties = { background: "#22c55e55" };
const CONS: CSSProperties[] = [{}, { background: "#3b82f622" }, { background: "#3b82f640" }, { background: "#3b82f666" }];
const HYDRO: Record<string, CSSProperties> = { phobic: { background: "#f59e0b4d" }, philic: { background: "#3b82f64d" }, mid: {} };
const MODE_LEGEND: Record<ColorMode, string> = {
  class: "classi chimiche di Clustal X",
  identity: "verde: residuo uguale a quello del bersaglio nella stessa colonna",
  conservation: "blu più intenso: colonna più conservata (≥ 50%, 70%, 90% delle voci con lo stesso residuo)",
  hydro: "arancio: idrofobico · blu: idrofilo (Kyte & Doolittle)",
  none: "senza colori",
};

function readColorMode(): ColorMode {
  try {
    const v = localStorage.getItem(COLOR_PREF);
    return COLOR_MODES.some((m) => m.value === v) ? (v as ColorMode) : "class";
  } catch {
    return "class";
  }
}

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
  /** controlli da guardare prima di modellare (F8 / Maiusc+F8 per passare dall'uno all'altro) */
  issues?: PirIssue[];
  /** apre il modulo della seconda riga per la voce (pulsante o doppio clic sul nome) */
  onEditHeader?: (row: number) => void;
  /** toglie le code del bersaglio senza template (la pagina aggiorna anche riferimento e numerazione) */
  onTrimEnds?: () => void;
  /** residui allineati diversamente rispetto alla versione salvata, come "riga:colonna" */
  changed?: Set<string>;
  /** annotazioni del documento, già in colonne */
  bands?: { from: number; to: number; color: string; label: string }[];
  /** nuova annotazione sulle colonne selezionate */
  onAnnotate?: (c0: number, c1: number) => void;
  /** il cursore si è spostato (vista 3D collegata) */
  onCursor?: (r: number, c: number) => void;
  /** etichetta di ogni riga accanto al nome (predefinita: bersaglio / template secondo il tipo PIR) */
  rowLabels?: string[];
  /** pulsanti per inserire «/» e «.» (solo PIR) */
  markers?: boolean;
  /** casella per sbloccare i residui dei template (solo PIR) */
  lockToggle?: boolean;
}

const isLocked = (kind: string | undefined, templatesEditable: boolean) => !templatesEditable && !!kind?.startsWith("structure");
const widthOf = (rows: string[]) => Math.max(0, ...rows.map((s) => s.length));

/**
 * Griglia modificabile dell'allineamento PIR: una riga per voce, una cella per colonna. Si spostano solo gap
 * (lib/pirEdit): Spazio o "-" inserisce, Canc / Backspace toglie, Alt+frecce fa scorrere il blocco selezionato; una
 * lettera sostituisce il residuo sotto il cursore. Le colonne fuori vista non si disegnano: allineamenti lunghi restano
 * fluidi. Il focus resta sulla griglia anche dopo i pulsanti, così le lettere non finiscono alle scorciatoie globali.
 */
export function PirGrid({ ids, kinds, headers, rows, onEdit, onUndo, onRedo, canUndo, canRedo, templatesEditable, onTemplatesEditable, mutated, focus, onFocusDone, secondary, issues = [], onEditHeader, onTrimEnds, changed, bands = [], onAnnotate, onCursor, rowLabels, markers = true, lockToggle = true }: Props) {
  const [colorMode, setColorModeState] = useState<ColorMode>(readColorMode);
  const setColorMode = (m: ColorMode) => {
    setColorModeState(m);
    try {
      localStorage.setItem(COLOR_PREF, m);
    } catch {
      // preferenza non salvata: vale finché la pagina resta aperta
    }
  };
  const width = widthOf(rows);
  // passo verticale fra le righe: con la struttura secondaria ogni riga ha la sua traccia sotto
  const stride = secondary ? RH + TH : RH;
  // blocchi da BLOCK colonne, uno sotto l'altro: righello, istogramma, voci (con le tracce), spazio
  const blockH = RH + HH + rows.length * stride + BG;
  const nBlocks = Math.max(1, Math.ceil(width / BLOCK));
  const blockW = NAME_W + Math.min(BLOCK, Math.max(1, width)) * CW;
  /** alto della cella (riga r, colonna c) dentro la griglia */
  const cellTop = (r: number, c: number) => Math.floor(c / BLOCK) * blockH + RH + HH + r * stride;
  /** sinistra della cella (colonna c) */
  const cellLeft = (c: number) => NAME_W + (c % BLOCK) * CW;
  const [anchor, setAnchor] = useState({ r: 0, c: 0 });
  const [cursor, setCursor] = useState({ r: 0, c: 0 });
  // parte visibile della griglia (in verticale): si disegnano solo i blocchi visibili
  const [view, setView] = useState({ top: 0, height: 600 });
  // messaggio di rifiuto: `n` cambia a ogni messaggio, così lo stesso rifiuto ripetuto viene riletto
  const [message, setMessage] = useState<{ text: string; n: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const drag = useRef(false);
  const pendingReveal = useRef(false);
  // problema raggiunto con l'ultimo F8 e cella in cui ha portato il cursore
  const lastIssue = useRef<{ issue: PirIssue; r: number; c: number } | null>(null);
  // tavolozza delle sostituzioni a comparsa vicino al cursore; griglia a schermo intero; menu "Altro"
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [full, setFull] = useState(false);
  const moreRef = useRef<HTMLDetailsElement>(null);
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
    const measure = () => setView({ top: box.scrollTop, height: box.clientHeight });
    measure();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(box);
    return () => ro?.disconnect();
  }, []);

  /** Porta in vista la cella scorrendo solo la griglia. */
  const reveal = (c: number, r: number) => {
    const box = boxRef.current;
    if (!box) return;
    const x = (c % BLOCK) * CW;
    const visible = box.clientWidth - NAME_W;
    if (x < box.scrollLeft) box.scrollLeft = Math.max(0, x - CW * 4);
    else if (x + CW > box.scrollLeft + visible) box.scrollLeft = x + CW * 5 - visible;
    const y = cellTop(r, c);
    // in alto si lascia vedere anche il righello del blocco
    if (y < box.scrollTop) box.scrollTop = Math.max(0, y - RH - HH - 4);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo a una nuova richiesta di focus (un oggetto nuovo per richiesta)
  }, [focus]);

  const refocus = () => boxRef.current?.focus({ preventScroll: true });

  useEffect(() => {
    onCursor?.(cursor.r, cursor.c);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- si avvisa solo lo spostamento del cursore; onCursor può cambiare a ogni disegno del genitore
  }, [cursor.r, cursor.c]);

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
    /** colonna di marcatori prima della selezione: "/" in tutte le voci, "." nelle righe selezionate */
    marker: (mark: "/" | ".") => {
      const marked = new Set(Array.from({ length: sel.r1 - sel.r0 + 1 }, (_, i) => sel.r0 + i));
      return edit(insertMarkerColumn(rows, sel.c0, mark, marked), "");
    },
    /** sostituisce il residuo sotto il cursore */
    substitute: (aa: string) => {
      const up = aa.toUpperCase();
      if (!isStandardAa(up)) return edit(null, `«${up}» non è uno dei 20 amminoacidi standard.`);
      if (isLocked(kinds[cursor.r], templatesEditable)) {
        return edit(null, lockToggle
          ? "I residui dei template sono bloccati: devono corrispondere al file della struttura. Sbloccali dalla barra solo se serve davvero."
          : "I residui delle strutture non si sostituiscono: devono restare quelli dei file.");
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
    if (mod && (k === "z" || k === "Z")) {
      if (e.shiftKey) onRedo();
      else onUndo();
    } else if (mod && (k === "y" || k === "Y")) onRedo();
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
    else if (k === "F8") {
      // se il cursore è ancora dove l'ha portato l'ultimo F8, si passa al problema vicino nell'elenco
      const prev = lastIssue.current && lastIssue.current.r === cursor.r && lastIssue.current.c === cursor.c ? lastIssue.current.issue : null;
      const next = nextIssue(issues, cursor.r, cursor.c, e.shiftKey ? -1 : 1, prev);
      if (next) {
        const r = next.row ?? cursor.r;
        moveTo(r, next.from, false);
        lastIssue.current = { issue: next, r, c: Math.min(Math.max(0, next.from), Math.max(0, width - 1)) };
      } else say("Nessun controllo da guardare.");
    } else if (k === "Enter") {
      setPaletteOpen((o) => !o);
    } else if (k === "Escape") {
      if (paletteOpen) setPaletteOpen(false);
      else if (full && sel.r0 === sel.r1 && sel.c0 === sel.c1) setFull(false);
      else setAnchor(cursor);
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
    const y = e.clientY - rect.top;
    const b = Math.min(nBlocks - 1, Math.max(0, Math.floor(y / blockH)));
    const r = clampR(Math.floor((y - b * blockH - RH - HH) / stride));
    const xName = e.clientX - box.getBoundingClientRect().left;
    if (xName < NAME_W) return { r, c: -1 };
    const local = Math.min(BLOCK - 1, Math.max(0, Math.floor((e.clientX - rect.left - NAME_W) / CW)));
    return { r, c: clampC(b * BLOCK + local) };
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

  // blocchi disegnati: quelli visibili, più uno sopra e uno sotto
  const b0 = Math.max(0, Math.floor(view.top / blockH) - 1);
  const b1 = Math.min(nBlocks, Math.ceil((view.top + view.height) / blockH) + 1);

  const cursorRow = rows[cursor.r] ?? "";
  // numerazione di ogni riga (riga di stato e numero del primo residuo visibile accanto al nome)
  const numbering = useMemo(() => rows.map((s, i) => residuePositions(s, headers[i] ?? "")), [rows, headers]);
  const positions = numbering[cursor.r] ?? [];
  const conservation = useMemo(() => columnConservation(rows), [rows]);
  const targetRow = kinds.indexOf("sequence");
  /** Tinta di fondo di una cella secondo la colorazione scelta. */
  const cellStyle = (x: string, r: number, c: number): CSSProperties | undefined => {
    if (colorMode === "class") return TINT[x.toUpperCase()];
    if (colorMode === "none" || !/[A-Za-z]/.test(x)) return undefined;
    if (colorMode === "identity") {
      const t = rows[targetRow]?.[c];
      return r !== targetRow && t && t.toUpperCase() === x.toUpperCase() ? IDENT : undefined;
    }
    if (colorMode === "conservation") return CONS[conservationLevel(conservation[c] ?? 0)];
    const h = hydroClass(x);
    return h ? HYDRO[h] : undefined;
  };
  /** numero del primo residuo della riga nel blocco che comincia alla colonna `from` (come a inizio riga in un PIR) */
  const firstNumber = (r: number, from: number) => {
    const list = numbering[r] ?? [];
    for (let c = from; c < Math.min(list.length, from + BLOCK); c++) {
      const p = list[c];
      if (p) return p.multichain ? `${p.chain}:${p.number}` : String(p.number);
    }
    return "";
  };
  const ch = cursorRow[cursor.c] ?? "";
  const pos = positions[cursor.c] ?? null;
  const cls = residueClass(ch);
  const selCols = sel.c1 - sel.c0 + 1;
  const selRows = sel.r1 - sel.r0 + 1;
  const ssText = secondary ? secondaryText(secondary, cursor.r, cursor.c) : null;
  // controlli nella cella; sul bersaglio anche quelli dei template nella stessa colonna
  const hereIssues = issuesAt(issues, cursor.r, cursor.c);
  const shownIssues = hereIssues.length || kinds[cursor.r] !== "sequence" ? hereIssues : issues.filter((i) => i.from <= cursor.c && cursor.c <= i.to);
  const firstIssue = shownIssues[0];

  /** Posizione della tavolozza: sotto la cella del cursore, tenuta dentro il riquadro della griglia. */
  const palettePos = (): CSSProperties => {
    const box = boxRef.current;
    if (!box) return {};
    const top = cellTop(cursor.r, cursor.c) + stride - box.scrollTop + 4;
    const left = cellLeft(cursor.c) - box.scrollLeft;
    return { top: Math.min(Math.max(0, top), box.clientHeight - 8), left: Math.max(0, Math.min(left, box.clientWidth - 440)) };
  };

  /** voce del menu "Altro": esegue e chiude il menu */
  const more = (f: () => unknown) => () => {
    if (moreRef.current) moreRef.current.open = false;
    f();
    refocus();
  };

  return (
    <div className={`stack-sm pirg-root${full ? " pirg-full" : ""}`}>
      <div className="pirg-tools" role="toolbar" aria-label="Strumenti della griglia">
        <button type="button" className="btn btn-sm" disabled={!canUndo} onClick={run(onUndo)} title="Annulla (Ctrl+Z)" aria-label="Annulla"><Icon name="undo" size={14} /></button>
        <button type="button" className="btn btn-sm" disabled={!canRedo} onClick={run(onRedo)} title="Ripristina (Ctrl+Y)" aria-label="Ripristina"><Icon name="redo" size={14} /></button>
        <span className="pirg-sep" />
        <button type="button" className="btn btn-sm" onClick={run(actions.insert)} title="Inserisce un gap prima della selezione nelle righe selezionate (Spazio o -)">+ gap</button>
        <button type="button" className="btn btn-sm" onClick={run(actions.deleteAt)} title="Toglie il gap nella prima colonna della selezione (Canc)">− gap</button>
        <button type="button" className="btn btn-sm" onClick={run(() => actions.slide(-1))} title="Sposta il blocco selezionato a sinistra; un blocco di soli gap scavalca i residui (Alt+←)" aria-label="Sposta il blocco a sinistra">← blocco</button>
        <button type="button" className="btn btn-sm" onClick={run(() => actions.slide(1))} title="Sposta il blocco selezionato a destra; un blocco di soli gap scavalca i residui (Alt+→)" aria-label="Sposta il blocco a destra">blocco →</button>
        <button type="button" className="btn btn-sm" onClick={() => { setPaletteOpen((o) => !o); refocus(); }} aria-expanded={paletteOpen}
          title="Tavolozza delle sostituzioni per il residuo sotto il cursore (Invio o doppio clic; una lettera sostituisce subito)">Sostituisci…</button>
        <span className="pirg-sep" />
        <label className="check" title={MODE_LEGEND[colorMode]}>
          Colori
          <select className="input input-sm" value={colorMode} onChange={(e) => { setColorMode(e.target.value as ColorMode); refocus(); }} aria-label="Colori della griglia">
            {COLOR_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
        <details className="pirg-more" ref={moreRef}>
          <summary className="btn btn-sm">Altro ▾</summary>
          <div className="pirg-menu" role="menu">
            <button type="button" role="menuitem" onClick={more(actions.squeeze)}>Togli colonne di soli gap</button>
            {onEditHeader && <button type="button" role="menuitem" onClick={more(() => onEditHeader(cursor.r))}>Seconda riga della voce…</button>}
            {markers && <button type="button" role="menuitem" onClick={more(() => actions.marker("/"))}>Inserisci colonna «/» (separatore di catena)</button>}
            {markers && <button type="button" role="menuitem" onClick={more(() => actions.marker("."))}>Inserisci colonna «.» (ligando) nelle righe selezionate</button>}
            {onAnnotate && <button type="button" role="menuitem" onClick={more(() => onAnnotate(sel.c0, sel.c1))}>Annota la selezione…</button>}
            {onTrimEnds && <button type="button" role="menuitem" onClick={more(onTrimEnds)}>Ritaglia le estremità senza template</button>}
            {lockToggle && (
              <label className="check" title="I residui dei template devono corrispondere al file della struttura: sbloccali solo per correggere un PIR che non corrisponde già">
                <input type="checkbox" checked={templatesEditable} onChange={(e) => { if (moreRef.current) moreRef.current.open = false; onTemplatesEditable(e.target.checked); }} />
                Residui dei template modificabili
              </label>
            )}
          </div>
        </details>
        <span className="right row" style={{ gap: 6 }}>
          {issues.length > 0 && (
            <button type="button" className="btn btn-sm btn-ghost pirg-issues" title="Controlli da guardare: F8 / Maiusc+F8 portano il cursore al successivo / precedente"
              onClick={() => { refocus(); boxRef.current?.dispatchEvent(new KeyboardEvent("keydown", { key: "F8", bubbles: true })); }}>
              {issueSummary(issues)} · F8
            </button>
          )}
          <button type="button" className="btn btn-sm" onClick={() => { setFull((f) => !f); refocus(); }} aria-pressed={full}
            title={full ? "Torna alla pagina (Esc)" : "Griglia a schermo intero"}>
            <Icon name={full ? "minimize" : "maximize"} size={14} /> {full ? "Riduci" : "Espandi"}
          </button>
        </span>
      </div>
      {isRagged(rows) && <div className="small" style={{ color: "var(--warn)" }}>Le voci hanno lunghezze diverse: la prima modifica le pareggia con gap in coda.</div>}
      <div className="pirg-wrap">
      <div
        ref={boxRef}
        className="pirg"
        tabIndex={0}
        role="application"
        aria-roledescription="griglia dell'allineamento"
        aria-label="Allineamento modificabile: frecce per muoversi, Spazio inserisce un gap, Canc lo toglie, Alt+frecce sposta il blocco, una lettera sostituisce il residuo"
        aria-describedby={statusId}
        onKeyDown={onKeyDown}
        onScroll={(e) => setView({ top: e.currentTarget.scrollTop, height: e.currentTarget.clientHeight })}
        style={{ "--cw": `${CW}px`, "--rh": `${RH}px`, "--name-w": `${NAME_W}px` } as CSSProperties}
      >
        <div ref={bodyRef} className="pirg-body" style={{ width: blockW, height: nBlocks * blockH - BG }} onMouseDown={onMouseDown} aria-hidden="true"
          onDoubleClick={(e) => {
            const cell = cellAt(e);
            if (cell && cell.c < 0) onEditHeader?.(cell.r);
            else if (cell) setPaletteOpen(true);
          }}>
          {Array.from({ length: b1 - b0 }, (_, k) => b0 + k).map((b) => {
            const from = b * BLOCK;
            const to = Math.min(width, from + BLOCK);
            const local = (c: number) => NAME_W + (c - from) * CW;
            const clip = <T extends { from: number; to: number }>(x: T) => x.to >= from && x.from < to;
            const span = (a: number, z: number) => ({ left: local(Math.max(a, from)), width: (Math.min(z, to - 1) - Math.max(a, from) + 1) * CW });
            const marks: number[] = [];
            for (let c = Math.ceil((from + 1) / 10) * 10; c <= to; c += 10) marks.push(c);
            const top = RH + HH;
            const selHere = sel.c1 >= from && sel.c0 < to;
            return (
              <div key={b} className="pirg-block" style={{ top: b * blockH, height: blockH - BG }}>
                <div className="pirg-ruler">
                  <div className="pirg-name">{b === 0 ? "colonna" : ""}</div>
                  {marks.map((c) => <span key={c} style={{ left: local(c - 1) }}>{c}</span>)}
                  {issues.filter(clip).map((x, i) => <i key={`i${i}`} className={`pirg-issue pirg-issue-${x.severity}`} style={span(x.from, x.to)} />)}
                </div>
                <div className="pirg-hist">
                  <div className="pirg-name">{b === 0 ? "conservazione" : ""}</div>
                  {conservation.slice(from, to).map((v, i) => (v > 0 ? (
                    <i key={i} style={{ left: local(from + i), height: Math.max(1, Math.round(v * (HH - 2))) }} className={v >= 0.999 ? "pirg-hist-full" : undefined} />
                  ) : null))}
                </div>
                {bands.filter(clip).map((x, i) => (
                  <div key={`b${i}`} className="pirg-band" style={{ ...span(x.from, x.to), top, height: rows.length * stride, background: `${x.color}26`, borderColor: x.color }} />
                ))}
                {secondary && rows.map((_s, r) => (
                  <Track key={`t${r}`} top={top + r * stride + RH} from={from} to={to} states={secondary.states[r]} source={secondary.sources[r]}
                    conflicts={secondary.conflicts.filter((x) => x.row === r)} />
                ))}
                {rows.map((s, r) => (
                  <div key={r} className={`pirg-row${kinds[r] === "sequence" ? " pirg-target" : ""}`} style={{ top: top + r * stride }}>
                    <div className="pirg-name" title={`${ids[r]} · ${kinds[r]}`}>
                      <span className="pirg-kind">{rowLabels?.[r] ?? (kinds[r] === "sequence" ? "bersaglio" : "template")}</span>{ids[r]}
                      <span className="pirg-num" title="Numero del primo residuo della riga">{firstNumber(r, from)}</span>
                    </div>
                    <div className="pirg-seq" style={{ left: NAME_W }}>
                      {s.slice(from, to).split("").map((x, i) => (
                        <span key={i} style={cellStyle(x, r, from + i)}
                          className={[
                            x === "-" ? "pirg-gap" : x === "/" || x === "." ? "pirg-mark" : mutated?.has(`${r}:${from + i}`) ? "pirg-mut" : "",
                            changed?.has(`${r}:${from + i}`) ? "pirg-changed" : "",
                          ].join(" ").trim() || undefined}>{x}</span>
                      ))}
                    </div>
                  </div>
                ))}
                {selHere && <div className="pirg-sel" style={{ ...span(sel.c0, sel.c1), top: top + sel.r0 * stride, height: (selRows - 1) * stride + RH }} />}
                {Math.floor(cursor.c / BLOCK) === b && <div className="pirg-cursor" style={{ top: top + cursor.r * stride, left: local(cursor.c) }} />}
              </div>
            );
          })}
        </div>
      </div>
      {paletteOpen && (
        <div className="pirg-popover" style={palettePos()}>
          <Palette current={ch} locked={isLocked(kinds[cursor.r], templatesEditable)}
            onClose={() => { setPaletteOpen(false); refocus(); }}
            onPick={(aa) => {
              actions.substitute(aa);
              setPaletteOpen(false);
              refocus();
            }} />
        </div>
      )}
      </div>
      <div className="aln-status" id={statusId} aria-live="polite" aria-atomic="true">
        Colonna <b>{cursor.c + 1}</b> di {width} · <b>{ids[cursor.r]}</b>:{" "}
        {pos === null
          ? (ch === "/" ? "separatore di catena" : ch === "." ? "blocco di ligando" : "gap")
          : <>{pos.multichain ? <>catena <b>{pos.chain}</b> · </> : null}<b>{residueText(ch)}</b> {pos.number}{cls ? ` · ${cls.one}` : ""}</>}
        {ssText && <> · {ssText}</>}
        {bands.filter((b) => b.from <= cursor.c && cursor.c <= b.to).map((b) => <span key={b.label}> · annotazione: <b>{b.label}</b></span>)}
        {firstIssue && (
          <> · <span className={`pirg-issue-text pirg-issue-text-${firstIssue.severity}`}>
            {firstIssue.text}{firstIssue.row !== null && firstIssue.row !== cursor.r ? ` (${ids[firstIssue.row]})` : ""}
          </span>{shownIssues.length > 1 ? ` (+${shownIssues.length - 1})` : ""}</>
        )}
        {(selCols > 1 || selRows > 1) && <> · selezione {selRows} × {selCols}</>}
        {message && <span key={message.n}> · <span style={{ color: "var(--warn)" }}>{message.text}</span></span>}
      </div>
      <div className="tiny text-2">Tasti: frecce, Spazio / Canc per i gap, Alt+frecce per i blocchi, una lettera per sostituire, F8 per i controlli · elenco completo con «?»</div>
    </div>
  );
}

const TONE_TEXT = { conservative: "conservativa", neutral: "neutra", radical: "radicale", same: "" } as const;
const signed = (n: number) => (n > 0 ? `+${n}` : String(n).replace("-", "−"));

/**
 * Tavolozza delle sostituzioni per il residuo sotto il cursore: i 20 amminoacidi raggruppati per classe chimica, con il
 * punteggio BLOSUM62 dal residuo attuale (positivo = sostituzione frequente fra proteine omologhe).
 */
function Palette({ current, locked, onPick, onClose }: { current: string; locked: boolean; onPick: (aa: string) => void; onClose: () => void }) {
  const wt = current.toUpperCase();
  const residue = /^[A-Z]$/.test(wt);
  const reason = !residue ? "porta il cursore su un residuo" : locked ? "residuo di una struttura: bloccato" : null;
  return (
    <div className="pirg-palette" role="group" aria-label="Sostituzione del residuo sotto il cursore">
      <div className="pirg-palette-head small">
        <b>Sostituisci</b>
        {residue && <span className="mono">{residueText(wt)}</span>}
        <span className="text-2">{reason ?? "clic su un amminoacido o premi la sua lettera · BLOSUM62 dal residuo attuale: + conservativa, − radicale"}</span>
        <button type="button" className="btn btn-sm btn-ghost right" onClick={onClose} aria-label="Chiudi la tavolozza (Esc)"><Icon name="x" size={14} /></button>
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
function Track({ top, from, to, states, source, conflicts }: { top: number; from: number; to: number; states: SS3[] | null; source: string | null; conflicts: GapConflict[] }) {
  return (
    <div className={`pirg-track${source === "gor" ? " pirg-track-pred" : ""}`} style={{ top }}>
      <div className="pirg-track-name">{source === "dssp" ? "DSSP" : source === "gor" ? "predetta (GOR)" : "—"}</div>
      {states && runs(states, from, to, (v) => v === " ").map((x) => (
        <i key={x.from} className={SS_CLASS[x.value]} style={{ left: NAME_W + (x.from - from) * CW, width: (x.to - x.from + 1) * CW }} />
      ))}
      {conflicts.filter((x) => x.to >= from && x.from < to).map((x) => {
        const a = Math.max(x.from, from);
        const z = Math.min(x.to, to - 1);
        return <b key={`k${x.from}`} className="pirg-ss-conflict" title={conflictText(x)} style={{ left: NAME_W + (a - from) * CW, width: (z - a + 1) * CW }} />;
      })}
    </div>
  );
}
