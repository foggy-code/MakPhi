import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CoverageAssessment, CoverageCheck, CoverageRequest } from "../api/types";
import { coverageFootnote, missingDomainsNote, sortedWarnings, span } from "../lib/disorder";
import { fmtNum } from "../lib/format";
import { Alert, Badge, BarCell, Disclosure, Spinner } from "./ui";

// Template non ancora valutabili (il selettore li sta scaricando da RCSB o AlphaFold DB): nuovi tentativi
const RETRY_MS = 4000;
const RETRIES = 5;

const DOMAIN_STATUS = {
  covered: { label: "coperto", tone: "ok" },
  partial: { label: "in parte", tone: "warn" },
  uncovered: { label: "scoperto", tone: "err" },
} as const;

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

/**
 * Avvisi di copertura (regioni disordinate e domini fuori dal template), suggerimento e, se i domini mancano, il perché
 * in evidenza (senza, l'assenza di avvisi sui domini sembrerebbe una copertura completa); metodo, tratti coperti e
 * domini del bersaglio nei dettagli a scomparsa.
 */
export function CoverageAlerts({ check, assessment, loading = false, error = null }: {
  check: CoverageCheck | null;
  assessment: Partial<CoverageAssessment> | null | undefined;
  loading?: boolean;
  error?: string | null;
}) {
  if (error) return <p className="small text-2">Verifica di disordine e domini non riuscita: {error}</p>;
  if (!check) return loading ? <p className="small text-2 row" style={{ gap: 6 }}><Spinner /> Verifica di disordine e domini del bersaglio…</p> : null;
  if (check.note) return <p className="small text-2">{check.note}</p>;
  const warnings = sortedWarnings(assessment);
  const covered = assessment?.covered ?? [];
  const domains = assessment?.domains ?? [];
  const domainsNote = missingDomainsNote(check);
  return (
    <div className="stack-sm">
      {warnings.map((w) => <Alert key={`${w.kind}${w.start}`} tone={w.tone === "warn" ? "warn" : "info"}>{w.text}</Alert>)}
      {assessment?.suggestion && <Alert tone="info"><b>Suggerimento.</b> {assessment.suggestion}</Alert>}
      {check.templates.filter((t) => t.error).map((t) => <p key={t.label} className="small" style={{ color: "var(--warn)" }}>{t.label}: non valutato ({t.error})</p>)}
      {domainsNote && <p className="small text-2"><b>Domini non verificati.</b> {domainsNote}</p>}
      <Disclosure title="Dettagli della verifica" extra={assessment?.coverage !== undefined ? `copertura ${fmtNum(assessment.coverage, 1)}%` : undefined}>
        <div className="stack-sm">
          <p>{coverageFootnote(check, { withNote: !domainsNote })}</p>
          {covered.length > 0 && <p>Residui del bersaglio con un residuo del template di fronte: {covered.map(([a, b]) => span(a, b)).join(", ")}.</p>}
          {domains.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Dominio</th><th>Residui</th><th>Coperto</th><th>Stato</th></tr></thead>
                <tbody>
                  {domains.map((d) => (
                    <tr key={`${d.accession}${d.start}`}>
                      <td>{d.name}<div className="tiny text-2">{d.accession} · {d.type}</div></td>
                      <td className="nowrap">{span(d.start, d.end)}</td>
                      <td><BarCell value={d.covered_pct} /></td>
                      <td><Badge tone={DOMAIN_STATUS[d.status].tone}>{DOMAIN_STATUS[d.status].label}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Disclosure>
    </div>
  );
}
