import type { ReactNode } from "react";
import { METRIC_GUIDE, type MetricKey } from "../lib/compareMetrics";
import { Badge, DetailGrid, Disclosure } from "./ui";
import "../styles/compare.css";

/**
 * Spiegazione di una metrica: cosa misura, soglie di lettura e fonte. `values` aggiunge i valori grezzi del risultato
 * (nei dettagli delle statistiche).
 */
export function MetricNotes({ metric, values }: { metric: MetricKey; values?: { label: ReactNode; value: ReactNode }[] }) {
  const guide = METRIC_GUIDE[metric];
  return (
    <div className="cmp-notes">
      <p>{guide.what}</p>
      <ul className="cmp-thresholds" aria-label={`Soglie di lettura: ${guide.name}`}>
        {guide.thresholds.map((t) => (
          <li key={t.range}><Badge tone={t.tone}>{t.range}</Badge>{t.reading}</li>
        ))}
      </ul>
      {values && values.length > 0 && <DetailGrid items={values} />}
      <span className="tiny muted">{guide.source}</span>
    </div>
  );
}

/**
 * Guida alle metriche: una sezione a scomparsa per metrica (il testo resta nella pagina anche da chiusa); il riepilogo
 * accanto al nome dice scala e soglia principale anche a sezione chiusa.
 */
export function MetricGuide({ metrics, open }: { metrics: MetricKey[]; open?: MetricKey }) {
  return (
    <div className="stack-sm">
      {metrics.map((m) => (
        <Disclosure key={m} title={METRIC_GUIDE[m].name} extra={METRIC_GUIDE[m].brief} defaultOpen={m === open}>
          <MetricNotes metric={m} />
        </Disclosure>
      ))}
    </div>
  );
}
