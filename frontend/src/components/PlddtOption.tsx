// Opzione di modellazione per i template AlphaFold: ritaglio dei residui a basso pLDDT (Modellazione e Pipeline).
import { clampThreshold, PLDDT_DEFAULT } from "../lib/alphafold";

export function PlddtOption({ enabled, threshold, onEnabled, onThreshold }: {
  enabled: boolean;
  threshold: number;
  onEnabled: (v: boolean) => void;
  onThreshold: (v: number) => void;
}) {
  return (
    <div className="stack-sm card flat" style={{ padding: "10px 12px" }}>
      <label className="check">
        <input type="checkbox" checked={enabled} onChange={(e) => onEnabled(e.target.checked)} />
        Togli dai template AlphaFold i residui con pLDDT &lt;
        <input
          className="input"
          type="number"
          min={0}
          max={100}
          step={5}
          style={{ width: 72, minHeight: 28 }}
          disabled={!enabled}
          value={threshold}
          onChange={(e) => onThreshold(clampThreshold(Number(e.target.value)))}
        />
      </label>
      <span className="tiny muted">
        Le regioni a bassa confidenza (spesso disordinate) degradano il modello: i residui tolti diventano gap nell'allineamento e MODELLER li
        costruisce senza vincoli dal template. Predefinito {PLDDT_DEFAULT}: sotto, AlphaFold DB considera lo scheletro poco affidabile. Le
        strutture sperimentali non vengono mai ritagliate.
      </span>
    </div>
  );
}
