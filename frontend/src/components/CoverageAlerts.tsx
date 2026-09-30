import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CoverageAssessment, CoverageCheck, CoverageRequest } from "../api/types";
import { coverageFootnote, sortedWarnings } from "../lib/disorder";
import { Alert } from "./ui";

// Template non ancora valutabili (il selettore li sta scaricando da RCSB o AlphaFold DB): nuovi tentativi
const RETRY_MS = 4000;
const RETRIES = 5;

/**
 * Verifica di disordine e domini (POST /api/bio/coverage) per una richiesta che cambia mentre l'utente sceglie bersaglio
 * e template: attesa di `delay` ms dall'ultimo cambio, risposte superate ignorate. `request` null = niente da verificare.
 * Se un template non è valutabile (download in corso) la stessa richiesta si ripete qualche volta.
 */
export function useCoverageCheck(request: CoverageRequest | null, delay = 600): { check: CoverageCheck | null; loading: boolean; error: string | null } {
  const key = request ? JSON.stringify(request) : "";
  const [state, setState] = useState<{ key: string; check: CoverageCheck | null; error: string | null }>({ key: "", check: null, error: null });
  const [attempt, setAttempt] = useState({ key: "", n: 0 });
  const n = attempt.key === key ? attempt.n : 0;
  useEffect(() => {
    if (!key) return;
    let current = true;
    let retry: number | undefined;
    const timer = window.setTimeout(() => {
      api.coverage(JSON.parse(key) as CoverageRequest)
        .then((check) => {
          if (!current) return;
          setState({ key, check, error: null });
          if (check.templates.some((t) => t.error) && n < RETRIES) retry = window.setTimeout(() => setAttempt({ key, n: n + 1 }), RETRY_MS);
        })
        .catch((e) => current && setState({ key, check: null, error: e instanceof Error ? e.message : String(e) }));
    }, n ? 0 : delay);
    return () => {
      current = false;
      window.clearTimeout(timer);
      window.clearTimeout(retry);
    };
  }, [key, delay, n]);
  const fresh = !!key && state.key === key;
  return { check: fresh ? state.check : null, loading: !!key && !fresh, error: fresh ? state.error : null };
}

/** Avvisi di copertura (regioni disordinate e domini fuori dal template), suggerimento e riga di sintesi. */
export function CoverageAlerts({ check, assessment, loading = false, error = null }: {
  check: CoverageCheck | null;
  assessment: Partial<CoverageAssessment> | null | undefined;
  loading?: boolean;
  error?: string | null;
}) {
  if (error) return <p className="tiny muted">Verifica di disordine e domini non riuscita: {error}</p>;
  if (!check) return loading ? <p className="tiny muted">Verifica di disordine e domini del bersaglio…</p> : null;
  if (check.note) return <p className="tiny muted">{check.note}</p>;
  const warnings = sortedWarnings(assessment);
  return (
    <div className="stack-sm">
      {warnings.map((w) => <Alert key={`${w.kind}${w.start}`} tone={w.tone === "warn" ? "warn" : "info"}>{w.text}</Alert>)}
      {assessment?.suggestion && <Alert tone="info"><b>Suggerimento.</b> {assessment.suggestion}</Alert>}
      {check.templates.filter((t) => t.error).map((t) => <p key={t.label} className="tiny" style={{ color: "var(--warn)" }}>{t.label}: non valutato ({t.error})</p>)}
      <p className="tiny muted">{coverageFootnote(check)}</p>
    </div>
  );
}
