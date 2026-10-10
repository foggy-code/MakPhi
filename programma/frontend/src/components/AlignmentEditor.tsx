import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { PirSecondary, StructureSource, TemplateRef } from "../api/types";
import { parsePir } from "../lib/pir";
import { emptyHistory, record, redo, undo, type History } from "../lib/pirEdit";
import { chainBreakIssues, emptyColumnIssues } from "../lib/pirIssues";
import { columnCodes, columnStates } from "../lib/pirSecondary";
import { PirGrid, type GridFocus, type GridSecondary } from "./PirGrid";

/** Tipo di allineamento della sezione Allineamenti (il PIR sequenza → struttura usa il banco dell'Editor PIR). */
export type EditableKind = "pairwise" | "multiple" | "structural";

const sameRows = (a: string[], b: string[]) => a.length === b.length && a.every((s, i) => s === b[i]);

/** Codici PIR univoci dai nomi (solo lettere, cifre, _ . -). */
function codesOf(ids: string[]): string[] {
  const seen = new Map<string, number>();
  return ids.map((id, i) => {
    const base = id.replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 36) || `seq${i + 1}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n ? `${base}_${n + 1}` : base;
  });
}

/**
 * PIR di servizio per chiedere la struttura secondaria al server: sequenze come "sequence" (predizione GOR per
 * ciascuna), strutture della libreria come "structureX" con la loro catena (DSSP); gli altri file non hanno DSSP.
 */
function servicePir(codes: string[], rows: string[], structures: (StructureSource | null)[]): { text: string; templates: TemplateRef[] } {
  const templates: TemplateRef[] = [];
  const blocks = rows.map((row, i) => {
    const src = structures[i];
    const lib = src?.type === "template" && src.id;
    if (lib) templates.push({ id: src.id!, chains: src.chain ? [src.chain] : [] });
    const header = lib ? `structureX:${codes[i]}::${src.chain ?? ""}::${src.chain ?? ""}::::` : `sequence:${codes[i]}::::::::`;
    return `>P1;${codes[i]}\n${header}\n${row}*`;
  });
  return { text: blocks.join("\n\n") + "\n", templates };
}

/**
 * Modifica di un allineamento non PIR nella sezione Allineamenti, con la stessa griglia dell'Editor PIR e le funzioni
 * adatte al tipo: gap e blocchi sempre; sostituzioni su ogni sequenza (pairwise, multiplo) e mai sulle strutture
 * (strutturale); struttura secondaria predetta (GOR) per le sequenze, DSSP per le strutture della libreria; controlli
 * di separatori e colonne vuote. Le righe modificate tornano alla pagina per esportazione e salvataggio.
 */
export function AlignmentEditor({ kind, ids, rows, onRows, structures = [] }: {
  kind: EditableKind;
  ids: string[];
  rows: string[];
  onRows: (rows: string[]) => void;
  /** solo strutturale: sorgente di ogni riga */
  structures?: (StructureSource | null)[];
}) {
  const [history, setHistory] = useState<History<string[]>>(emptyHistory);
  const [focus, setFocus] = useState<GridFocus | null>(null);
  const [ssOn, setSsOn] = useState(true);
  const [ss, setSs] = useState<{ key: string; data: PirSecondary } | null>(null);
  const structural = kind === "structural";
  const codes = useMemo(() => codesOf(ids), [ids]);

  const edit = (next: string[]) => {
    setHistory((h) => record(h, rows, sameRows));
    onRows(next);
  };
  const step = (dir: "undo" | "redo") => {
    const r = dir === "undo" ? undo(history, rows) : redo(history, rows);
    if (!r) return;
    setHistory(r.history);
    onRows(r.value);
  };

  // struttura secondaria per residuo: si richiede quando cambiano i residui, non i gap
  const residuesKey = useMemo(() => JSON.stringify(rows.map((s) => s.replace(/[^A-Za-z]/g, ""))), [rows]);
  useEffect(() => {
    if (!ssOn || ss?.key === residuesKey) return;
    let cancelled = false;
    const { text, templates } = servicePir(codes, rows, structural ? structures : []);
    const t = setTimeout(() => {
      api.pirSecondary(text, templates).then((data) => !cancelled && setSs({ key: residuesKey, data })).catch(() => undefined);
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- si richiede solo quando cambiano i residui (residuesKey), non i gap
  }, [residuesKey, ssOn]);

  const entries = ss?.data.entries ?? [];
  const usable = (i: number) => {
    const e = entries[i];
    return e?.ss && e.residues === rows[i].replace(/[^A-Za-z]/g, "").length ? e : null;
  };
  const secondary = useMemo<GridSecondary | null>(() => {
    if (!ssOn || !ss) return null;
    const states = rows.map((r, i) => (usable(i) ? columnStates(r, usable(i)!.ss!) : null));
    const ssCodes = rows.map((r, i) => (usable(i)?.source === "dssp" ? columnCodes(r, usable(i)!.ss!) : null));
    return { states, codes: ssCodes, sources: rows.map((_r, i) => usable(i)?.source ?? null), conflicts: [] };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- usable dipende solo da ss e rows, già nelle dipendenze
  }, [ssOn, ss, rows]);
  const issues = useMemo(() => [...chainBreakIssues(rows), ...emptyColumnIssues(rows)], [rows]);
  const noDssp = structural && ss && rows.some((_r, i) => !usable(i));

  return (
    <div className="stack-sm">
      <PirGrid
        ids={ids}
        kinds={rows.map(() => (structural ? "structureX" : "sequence"))}
        headers={parsePir(servicePir(codes, rows, []).text).map((e) => e.header)}
        rows={rows}
        onEdit={edit}
        onUndo={() => step("undo")}
        onRedo={() => step("redo")}
        canUndo={history.past.length > 0}
        canRedo={history.future.length > 0}
        templatesEditable={false}
        onTemplatesEditable={() => undefined}
        lockToggle={false}
        markers={false}
        rowLabels={rows.map(() => (structural ? "struttura" : "sequenza"))}
        secondary={secondary}
        issues={issues}
        focus={focus}
        onFocusDone={() => setFocus(null)}
      />
      <div className="row small">
        <label className="check">
          <input type="checkbox" checked={ssOn} onChange={(e) => setSsOn(e.target.checked)} />
          Struttura secondaria
        </label>
        <span className="text-2">
          {structural
            ? "DSSP sui file delle strutture della libreria; i residui delle strutture non si sostituiscono."
            : "Predizione GOR dalla sola sequenza (euristica, non è un dato); le sostituzioni valgono per ogni sequenza."}
        </span>
        {noDssp && <span style={{ color: "var(--warn)" }}>DSSP non disponibile per le strutture che non sono nella libreria (file caricati, modelli).</span>}
      </div>
    </div>
  );
}
