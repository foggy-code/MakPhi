import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { LigandRef, TemplateAnalysis, TemplateRef, WaterSite } from "../api/types";
import { isAlphaFoldId } from "../lib/alphafold";
import {
  candidateGroups, CLASS_LABEL, CLASS_TONE, groupClass, groupTitle, isDefault, isLookupError, isTransferable, isWater, ligandOptions, lookupState,
  selectionSummary, syncSelection, waterCandidates, waterText, type LigandCandidate, type LigandLookup,
} from "../lib/ligands";
import { Badge, Disclosure } from "./ui";

// Template scelto da un link (Ricerca) e non ancora in libreria: il selettore dei template lo sta scaricando da RCSB
// o AlphaFold DB, quindi un 404 si riprova qualche volta (come la verifica di copertura, useCoverageCheck).
const RETRY_MS = 4000;
const RETRIES = 5;
const fetchable = (id: string) => /^[0-9][A-Za-z0-9]{3}$/.test(id) || isAlphaFoldId(id);

/** Scelta dei gruppi eteroatomici da trasferire (opzione `ligands` della modellazione): per ogni template i gruppi
 *  delle catene scelte, con la classificazione del backend; additivi e ioni del tampone deselezionati per default.
 *  Sotto, le acque legate alla struttura (acque catalitiche e strutturali): nessuna scelta per default, le suggerite
 *  (metallo coordinato, ponte con un ligando) in evidenza e scelte tutte insieme con un pulsante; le altre, strutturali
 *  comprese, in un riquadro chiuso.
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shape e knownKey riassumono templates e known; own lo scrive questo effetto
  }, [enabled, shape, tick, knownKey]);
  const state = lookupState(templates, own, known);
  const analyses = state.analyses;
  const candidates = useMemo(
    () => templates.flatMap((t) => {
      const a = analyses[t.id];
      const ok = a && a !== "loading" && !isLookupError(a) ? a : null;
      return [...candidateGroups(t, ok), ...waterCandidates(t, ok)];
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shape, own e knownKey riassumono templates e analyses
    [shape, own, knownKey],
  );
  const failedKey = state.failed.join(",");
  useEffect(() => {
    const next = syncSelection(selection, candidates);
    onSelection(next, ligandOptions(candidates, next), state.ready, state.failed);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- si riallinea ai candidati nuovi; con selection si ripeterebbe a ogni scelta
  }, [candidates, state.ready, failedKey]);
  const toggle = (key: string, on: boolean) => setMany([key], on);
  const setMany = (keys: string[], on: boolean) => {
    const next = { ...selection };
    for (const k of keys) next[k] = on;
    onSelection(next, ligandOptions(candidates, next), state.ready, state.failed);
  };
  const retry = (id: string) => {
    attempts.current[id] = 0;
    forget(id);
  };
  return (
    <div className="stack-sm">
      <label className="check" title="MODELLER copia nel modello i gruppi HETATM scelti, come corpi rigidi dal template">
        <input type="checkbox" checked={enabled} onChange={(e) => onEnabled(e.target.checked)} /> Trasferisci ligandi, cofattori, metalli e acque dai template
      </label>
      {enabled && (
        <div className="stack-sm card flat" style={{ padding: "8px 10px" }}>
          {!templates.length && <span className="small text-2">Scegli prima i template.</span>}
          {templates.map((t) => {
            const all = candidates.filter((c) => c.template === t.id);
            const list = all.filter((c) => !isWater(c));
            const waters = all.filter(isWater);
            const usable = list.filter((c) => isTransferable(c.group));
            const chosen = usable.filter((c) => selection[c.key]).length;
            const a = analyses[t.id];
            return (
              <div key={t.id} className="stack-sm">
                <div className="row small">
                  <b>{t.id}{t.chains.length ? ` · catene ${t.chains.join(", ")}` : ""}</b>
                  {usable.length > 0 && <Badge tone={chosen ? "accent" : undefined}>{chosen} di {usable.length} scelti</Badge>}
                </div>
                {!a || a === "loading" ? <span className="small text-2">Lettura dei gruppi eteroatomici del template…</span>
                  : isLookupError(a) ? (
                    a.retrying ? <span className="small text-2">Template non ancora in libreria (download in corso?): nuovo tentativo fra qualche secondo…</span> : (
                      <span className="row small" style={{ color: "var(--warn)" }}>
                        Gruppi di {t.id} non disponibili: {a.error}
                        <button className="btn btn-sm btn-ghost" onClick={() => retry(t.id)}>Riprova</button>
                      </span>
                    )
                  )
                  : !list.length ? <span className="small text-2">Nessun gruppo eteroatomico nelle catene scelte.</span>
                  : list.map((c) => {
                    const cls = groupClass(c.group);
                    return (
                      <label key={c.key} className="check small" title={c.group.reason || c.group.description || undefined}>
                        <input type="checkbox" disabled={!isTransferable(c.group)} checked={!!selection[c.key]} onChange={(e) => toggle(c.key, e.target.checked)} />
                        <span className="mono">{groupTitle(c.group)}</span>
                        <Badge tone={CLASS_TONE[cls]}>{c.group.label ?? CLASS_LABEL[cls]}</Badge>
                        {c.group.description && <span className="text-2">{c.group.description}</span>}
                        {(!isDefault(c.group) || !isTransferable(c.group)) && c.group.reason && <span className="text-2">· {c.group.reason}</span>}
                      </label>
                    );
                  })}
                {a && a !== "loading" && !isLookupError(a) && (
                  <WaterChoice waters={waters} others={a.waters?.others ?? 0} selection={selection} onChange={setMany} />
                )}
              </div>
            );
          })}
          <span className="small text-2">{selectionSummary(candidates, selection)}</span>
          <Disclosure title="Come funziona il trasferimento">
            I gruppi scelti entrano nell'allineamento come colonne «.» e MODELLER li copia dal template. Le acque si trasferiscono solo se
            scelte una per una (colonne «w» nel PIR letto da MODELLER): restano nella posizione del template rispetto ai residui vicini, senza
            ottimizzare la rete di legami idrogeno.
            {templates.length > 1 && " Lo stesso gruppo nello stesso sito di più template diventa un solo gruppo del modello, con i vincoli di tutti."} Dopo
            la modellazione BioLoom verifica contatti, scontri e coordinazione dei metalli (pagina Qualità).
          </Disclosure>
        </div>
      )}
    </div>
  );
}

/** Acque di un template: le suggerite e quelle scelte in vista, le altre legate alla struttura in un riquadro chiuso. */
function WaterChoice({ waters, others, selection, onChange }: {
  waters: (LigandCandidate & { group: WaterSite })[];
  others: number;
  selection: Record<string, boolean>;
  onChange: (keys: string[], on: boolean) => void;
}) {
  if (!waters.length) {
    return <span className="small text-2">Nessuna acqua legata alla struttura nelle catene scelte{others ? ` (${others} di solvatazione, non elencate)` : ""}.</span>;
  }
  const suggested = waters.filter((c) => c.group.suggested);
  const chosen = waters.filter((c) => selection[c.key]);
  const shown = waters.filter((c) => c.group.suggested || selection[c.key]);
  const hidden = waters.filter((c) => !c.group.suggested && !selection[c.key]);
  const row = (c: LigandCandidate & { group: WaterSite }) => (
    <label key={c.key} className="check small" title={c.group.why || undefined}>
      <input type="checkbox" checked={!!selection[c.key]} onChange={(e) => onChange([c.key], e.target.checked)} />
      <span className="mono">{groupTitle(c.group)}</span>
      {c.group.why && <Badge tone={c.group.suggested ? "accent" : undefined}>{c.group.why}</Badge>}
      <span className="text-2">{waterText(c.group)}</span>
    </label>
  );
  return (
    <div className="stack-sm">
      <div className="row small">
        <b>Acque</b>
        <Badge tone={chosen.length ? "accent" : undefined}>{chosen.length} di {waters.length} scelte</Badge>
        {suggested.some((c) => !selection[c.key]) && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange(suggested.map((c) => c.key), true)}>
            Scegli le suggerite ({suggested.length})
          </button>
        )}
        {chosen.length > 0 && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange(chosen.map((c) => c.key), false)}>Nessuna</button>
        )}
      </div>
      {shown.map(row)}
      {!suggested.length && <span className="small text-2">Nessuna acqua suggerita (che coordini un metallo o faccia da ponte con un ligando).</span>}
      {hidden.length > 0 && (
        <Disclosure title={`Altre ${hidden.length} ${hidden.length === 1 ? "acqua legata" : "acque legate"} alla struttura`} lazy>
          <div className="stack-sm">{hidden.map(row)}</div>
        </Disclosure>
      )}
      {others > 0 && <span className="small text-2">{others} {others === 1 ? "acqua" : "acque"} di solvatazione senza legami con la struttura non {others === 1 ? "è elencata" : "sono elencate"}.</span>}
    </div>
  );
}
