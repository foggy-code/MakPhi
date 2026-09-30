import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { LigandRef, TemplateAnalysis, TemplateRef } from "../api/types";
import { isAlphaFoldId } from "../lib/alphafold";
import {
  candidateGroups, CLASS_LABEL, CLASS_TONE, groupClass, groupTitle, isDefault, isLookupError, isTransferable, ligandOptions, lookupState, selectionSummary,
  syncSelection, type LigandLookup,
} from "../lib/ligands";
import { Badge } from "./ui";

// Template scelto da un link (Ricerca) e non ancora in libreria: il selettore dei template lo sta scaricando da RCSB
// o AlphaFold DB, quindi un 404 si riprova qualche volta (come la verifica di copertura, useCoverageCheck).
const RETRY_MS = 4000;
const RETRIES = 5;
const fetchable = (id: string) => /^[0-9][A-Za-z0-9]{3}$/.test(id) || isAlphaFoldId(id);

/** Scelta dei gruppi eteroatomici da trasferire (opzione `ligands` della modellazione): per ogni template i gruppi
 *  delle catene scelte, con la classificazione del backend; additivi e ioni del tampone deselezionati per default.
 *  `known`: analisi già lette dal selettore dei template (anche dopo un download), usate al posto di una nuova
 *  lettura; una lettura non riuscita resta visibile con «Riprova», non «in corso» per sempre. */
export function LigandPicker({ templates, known = {}, enabled, onEnabled, selection, onSelection }: {
  templates: TemplateRef[];
  known?: Record<string, TemplateAnalysis>;
  enabled: boolean;
  onEnabled: (v: boolean) => void;
  selection: Record<string, boolean>;
  /** scelta aggiornata, gruppi per l'opzione `ligands`, se le analisi di tutti i template sono arrivate e i template
   *  la cui lettura non è riuscita */
  onSelection: (selection: Record<string, boolean>, refs: LigandRef[], ready: boolean, failed: string[]) => void;
}) {
  const [own, setOwn] = useState<Record<string, LigandLookup>>({});
  const [tick, setTick] = useState(0);
  const attempts = useRef<Record<string, number>>({});
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);
  const shape = JSON.stringify(templates.map((t) => [t.id, t.chains]));
  const knownKey = Object.keys(known).sort().join(",");
  const forget = (id: string) => {
    setOwn((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setTick((k) => k + 1);
  };
  useEffect(() => {
    if (!enabled) return;
    templates.forEach((t) => {
      if (known[t.id] || own[t.id]) return; // nota dal selettore, già letta, in lettura o in errore
      setOwn((a) => ({ ...a, [t.id]: "loading" }));
      api.template(t.id)
        .then((a) => setOwn((prev) => ({ ...prev, [t.id]: a })))
        .catch((err: unknown) => {
          const n = attempts.current[t.id] ?? 0;
          const retrying = err instanceof ApiError && err.status === 404 && fetchable(t.id) && n < RETRIES;
          setOwn((prev) => ({ ...prev, [t.id]: { error: err instanceof Error ? err.message : String(err), retrying } }));
          if (retrying) {
            attempts.current[t.id] = n + 1;
            timers.current.push(window.setTimeout(() => forget(t.id), RETRY_MS));
          }
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, shape, tick, knownKey]);
  const state = lookupState(templates, own, known);
  const analyses = state.analyses;
  const candidates = useMemo(
    () => templates.flatMap((t) => { const a = analyses[t.id]; return candidateGroups(t, a && a !== "loading" && !isLookupError(a) ? a : null); }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shape, own, knownKey],
  );
  const failedKey = state.failed.join(",");
  useEffect(() => {
    const next = syncSelection(selection, candidates);
    onSelection(next, ligandOptions(candidates, next), state.ready, state.failed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, state.ready, failedKey]);
  const toggle = (key: string, on: boolean) => {
    const next = { ...selection, [key]: on };
    onSelection(next, ligandOptions(candidates, next), state.ready, state.failed);
  };
  const retry = (id: string) => {
    attempts.current[id] = 0;
    forget(id);
  };
  return (
    <div className="stack-sm">
      <label className="check" title="MODELLER copia nel modello i gruppi HETATM scelti, come corpi rigidi dal template">
        <input type="checkbox" checked={enabled} onChange={(e) => onEnabled(e.target.checked)} /> Trasferisci ligandi, cofattori e metalli dai template
      </label>
      {enabled && (
        <div className="stack-sm card flat" style={{ padding: "8px 10px" }}>
          {!templates.length && <span className="tiny muted">Scegli prima i template.</span>}
          {templates.map((t) => {
            const list = candidates.filter((c) => c.template === t.id);
            const a = analyses[t.id];
            return (
              <div key={t.id} className="stack-sm">
                <b className="small">{t.id}{t.chains.length ? ` · catene ${t.chains.join(", ")}` : ""}</b>
                {!a || a === "loading" ? <span className="tiny muted">Lettura dei gruppi eteroatomici del template…</span>
                  : isLookupError(a) ? (
                    a.retrying ? <span className="tiny muted">Template non ancora in libreria (download in corso?): nuovo tentativo fra qualche secondo…</span> : (
                      <span className="row tiny" style={{ color: "var(--warn)" }}>
                        Gruppi di {t.id} non disponibili: {a.error}
                        <button className="btn btn-sm btn-ghost" onClick={() => retry(t.id)}>Riprova</button>
                      </span>
                    )
                  )
                  : !list.length ? <span className="tiny muted">Nessun gruppo eteroatomico (acque escluse) nelle catene scelte.</span>
                  : list.map((c) => {
                    const cls = groupClass(c.group);
                    return (
                      <label key={c.key} className="check small" title={c.group.reason || c.group.description || undefined}>
                        <input type="checkbox" disabled={!isTransferable(c.group)} checked={!!selection[c.key]} onChange={(e) => toggle(c.key, e.target.checked)} />
                        <span className="mono">{groupTitle(c.group)}</span>
                        <Badge tone={CLASS_TONE[cls]}>{c.group.label ?? CLASS_LABEL[cls]}</Badge>
                        {c.group.description && <span className="tiny muted">{c.group.description}</span>}
                        {(!isDefault(c.group) || !isTransferable(c.group)) && c.group.reason && <span className="tiny muted">{c.group.reason}</span>}
                      </label>
                    );
                  })}
              </div>
            );
          })}
          <span className="tiny muted">
            {selectionSummary(candidates, selection)} I gruppi scelti entrano nell'allineamento come colonne «.» e MODELLER li copia dal template; le
            acque non si trasferiscono.{templates.length > 1 && " Lo stesso gruppo nello stesso sito di più template diventa un solo gruppo del modello, con i vincoli di tutti."} Dopo
            la modellazione BioLoom verifica contatti, scontri e coordinazione dei metalli (pagina Qualità).
          </span>
        </div>
      )}
    </div>
  );
}
