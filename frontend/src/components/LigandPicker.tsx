import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { LigandRef, TemplateAnalysis, TemplateRef } from "../api/types";
import { candidateGroups, CLASS_LABEL, CLASS_TONE, groupClass, groupTitle, isDefault, isTransferable, ligandOptions, selectionSummary, syncSelection } from "../lib/ligands";
import { Badge } from "./ui";

/** Scelta dei gruppi eteroatomici da trasferire (opzione `ligands` della modellazione): per ogni template i gruppi
 *  delle catene scelte, con la classificazione del backend; additivi e ioni del tampone deselezionati per default. */
export function LigandPicker({ templates, enabled, onEnabled, selection, onSelection }: {
  templates: TemplateRef[];
  enabled: boolean;
  onEnabled: (v: boolean) => void;
  selection: Record<string, boolean>;
  /** scelta aggiornata, gruppi per l'opzione `ligands` e se le analisi di tutti i template sono arrivate */
  onSelection: (selection: Record<string, boolean>, refs: LigandRef[], ready: boolean) => void;
}) {
  const [analyses, setAnalyses] = useState<Record<string, TemplateAnalysis | "loading">>({});
  const shape = JSON.stringify(templates.map((t) => [t.id, t.chains]));
  // analisi dei template (ligandi classificati): un template appena scaricato da RCSB si riprova quando il selettore
  // dei template ne aggiorna le catene
  useEffect(() => {
    if (!enabled) return;
    templates.forEach((t) => {
      if (analyses[t.id]) return; // già letta o in lettura
      setAnalyses((a) => ({ ...a, [t.id]: "loading" }));
      api.template(t.id)
        .then((a) => setAnalyses((prev) => ({ ...prev, [t.id]: a })))
        .catch(() => setAnalyses((prev) => {
          const next = { ...prev };
          delete next[t.id];
          return next;
        }));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, shape]);
  const candidates = useMemo(
    () => templates.flatMap((t) => { const a = analyses[t.id]; return candidateGroups(t, a && a !== "loading" ? a : null); }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shape, analyses],
  );
  const ready = templates.every((t) => { const a = analyses[t.id]; return !!a && a !== "loading"; });
  useEffect(() => {
    const next = syncSelection(selection, candidates);
    onSelection(next, ligandOptions(candidates, next), ready);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, ready]);
  const toggle = (key: string, on: boolean) => {
    const next = { ...selection, [key]: on };
    onSelection(next, ligandOptions(candidates, next), ready);
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
            acque non si trasferiscono. Dopo la modellazione BioLoom verifica contatti, scontri e coordinazione dei metalli (pagina Qualità).
          </span>
        </div>
      )}
    </div>
  );
}
