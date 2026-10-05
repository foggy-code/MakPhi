import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { PreflightCheck, PreflightRequest, PreflightResult } from "../api/types";
import { useToast } from "../lib/context";
import { coverageBadge, missingDomainsNote, sortedWarnings } from "../lib/disorder";
import { confirmTitle, countsText, estimateText, launchDecision, LEVEL_LABEL, LEVEL_TONE, requestKey, splitChecks } from "../lib/preflight";
import { CoverageAlerts } from "./CoverageAlerts";
import { Icon } from "./Icon";
import { Alert, Badge, DetailGrid, Disclosure, Modal, Spinner } from "./ui";

const RETRY_MS = 4000;
const RETRIES = 5;

export interface PreflightState {
  request: PreflightRequest | null;
  /** esito della richiesta corrente (null mentre è in corso o se non c'è richiesta) */
  result: PreflightResult | null;
  loading: boolean;
  error: string | null;
  /** controlli subito, senza attesa (clic su «Avvia» prima che la risposta arrivi) */
  check: () => Promise<PreflightResult | null>;
}

/**
 * Controlli preventivi (POST /api/compute/preflight) per una richiesta che cambia mentre l'utente sceglie: attesa di
 * `delay` ms dall'ultimo cambio, risposte superate ignorate (come useCoverageCheck). `request` null = niente da
 * controllare.
 */
export function usePreflight(request: PreflightRequest | null, delay = 600): PreflightState {
  const key = requestKey(request);
  const [state, setState] = useState<{ key: string; result: PreflightResult | null; error: string | null }>({ key: "", result: null, error: null });
  // template ancora da scaricare (il selettore lo sta scaricando): controlli ripetuti qualche volta, come la verifica
  // di copertura (useCoverageCheck), così identità e copertura arrivano a download finito
  const [attempt, setAttempt] = useState({ key: "", n: 0 });
  const n = attempt.key === key ? attempt.n : 0;
  const latest = useRef(key);
  latest.current = key;
  const fetchNow = useCallback(async (k: string): Promise<PreflightResult | null> => {
    try {
      const result = await api.preflight(JSON.parse(k) as PreflightRequest);
      if (latest.current === k) setState({ key: k, result, error: null });
      return result;
    } catch (e) {
      if (latest.current === k) setState({ key: k, result: null, error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  }, []);
  useEffect(() => {
    if (!key) return;
    let retry: number | undefined;
    const timer = window.setTimeout(() => {
      void fetchNow(key).then((result) => {
        if (result && n < RETRIES && result.checks.some((c) => c.code === "template_download") && latest.current === key) {
          retry = window.setTimeout(() => setAttempt({ key, n: n + 1 }), RETRY_MS);
        }
      });
    }, n ? 0 : delay);
    return () => {
      window.clearTimeout(timer);
      window.clearTimeout(retry);
    };
  }, [key, delay, fetchNow, n]);
  const fresh = !!key && state.key === key;
  const check = useCallback(async () => {
    if (!key) return null;
    if (fresh && state.result) return state.result;
    return fetchNow(key);
  }, [key, fresh, state.result, fetchNow]);
  return { request, result: fresh ? state.result : null, loading: !!key && !fresh, error: fresh ? state.error : null, check };
}

function CheckAlert({ check }: { check: PreflightCheck }) {
  return (
    <Alert tone={LEVEL_TONE[check.level]}>
      {check.message}
      {check.suggestion && <div className="small" style={{ marginTop: 3 }}><b>Suggerimento.</b> {check.suggestion}</div>}
    </Alert>
  );
}

/**
 * Esiti dei controlli: sintesi e tempo stimato in testa, errori e avvisi in evidenza, poi le informazioni sempre
 * visibili (dati che lasciano il computer, consigli); avvisi di copertura con CoverageAlerts, aperti se c'è un avviso o
 * un suggerimento («domini non verificati» nel riepilogo); base della stima e codici degli esiti a scomparsa.
 */
export function PreflightPanel({ state, title = "Controlli prima dell'avvio" }: { state: PreflightState; title?: string }) {
  if (!state.request) return null;
  const { result, loading, error } = state;
  const parts = splitChecks(result);
  const estimate = estimateText(result?.estimate);
  // la stima è già nel testo dell'esito "estimated_time" (modellazione, pipeline); per la ricerca la si scrive qui
  const timed = parts.infos.find((c) => c.code === "estimated_time");
  const infos = parts.infos.filter((c) => c !== timed);
  const tone = !result ? undefined : result.counts.error ? "err" : result.counts.warning ? "warn" : "ok";
  const combined = result?.coverage?.combined;
  const coverageWarn = coverageBadge(combined);
  // perché i domini mancano: nel riepilogo, così si vede anche a sezione chiusa
  const domainsNote = result?.coverage && !result.coverage.note ? missingDomainsNote(result.coverage) : null;
  return (
    <div className="stack-sm" aria-live="polite">
      <div className="row small">
        <b>{title}</b>
        {loading ? <span className="small text-2 row" style={{ gap: 6 }}><Spinner /> verifica in corso…</span>
          : result ? <Badge tone={tone}>{countsText(result)}</Badge> : null}
      </div>
      {(timed || estimate) && (
        <span className="small text-2 row" style={{ gap: 6 }} title={result?.estimate?.basis}>
          <Icon name="activity" size={14} />
          {timed ? `${timed.message}${timed.suggestion ? ` ${timed.suggestion}` : ""}` : `Tempo stimato: ${estimate}.`}
        </span>
      )}
      {error && <p className="small text-2">Controlli non riusciti ({error}): l'avvio resta possibile, il server valida comunque la richiesta.</p>}
      {parts.errors.map((c, i) => <CheckAlert key={`e${i}${c.code}`} check={c} />)}
      {parts.warnings.map((c, i) => <CheckAlert key={`w${i}${c.code}`} check={c} />)}
      {infos.length > 0 && (
        <ul className="small text-2" style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 3 }}>
          {infos.map((c, i) => <li key={`i${i}${c.code}`}>{c.message}{c.suggestion ? ` ${c.suggestion}` : ""}</li>)}
        </ul>
      )}
      {result?.coverage && (
        <Disclosure title="Disordine e domini del bersaglio"
          extra={coverageWarn || domainsNote ? (
            <>
              {coverageWarn && <Badge tone="warn">{coverageWarn.label}</Badge>}
              {domainsNote && <Badge title={domainsNote}>domini non verificati</Badge>}
            </>
          ) : undefined}
          defaultOpen={sortedWarnings(combined).length > 0 || !!combined?.suggestion}>
          <CoverageAlerts check={result.coverage} assessment={combined} />
        </Disclosure>
      )}
      {result && (result.estimate || result.checks.length > 0) && (
        <Disclosure title="Dettagli tecnici">
          <DetailGrid items={[
            ...(result.estimate ? [
              { label: "Tempo stimato", value: estimate },
              { label: "Base della stima", value: result.estimate.basis || "—" },
              ...(result.estimate.detail ? [{ label: "Dettaglio della stima", value: result.estimate.detail }] : []),
            ] : []),
            { label: "Esiti", value: result.checks.length ? result.checks.map((c) => `${c.code} (${LEVEL_LABEL[c.level]})`).join(" · ") : "nessuno" },
          ]} />
        </Disclosure>
      )}
    </div>
  );
}

/**
 * Pulsante di avvio con i controlli preventivi: disattivato con la spiegazione se c'è un errore, conferma («Avvia
 * comunque») se ci sono avvisi; senza risposta ancora i controlli si ripetono al clic.
 */
export function LaunchButton({ preflight, onLaunch, disabled = false, children, className = "btn btn-primary" }: {
  preflight: PreflightState;
  onLaunch: () => Promise<void> | void;
  disabled?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<PreflightCheck[] | null>(null);
  const decision = launchDecision(preflight.result, { pending: preflight.loading, failed: !!preflight.error });
  const launch = async () => {
    setBusy(true);
    try {
      await onLaunch();
    } catch (e) {
      toast.error(e); // la rotta di avvio ha rifiutato la richiesta (validazione del server)
    } finally {
      setBusy(false);
    }
  };
  const click = async () => {
    let current = decision;
    if (current.state === "checking" && preflight.request) {
      setBusy(true);
      const result = await preflight.check();
      setBusy(false);
      current = launchDecision(result, { failed: !result });
    }
    if (current.state === "blocked") {
      toast.push(current.reason ?? "Controlli non superati.", "warn");
      return;
    }
    if (current.state === "confirm") {
      setConfirm(current.warnings);
      return;
    }
    await launch();
  };
  return (
    <div className="stack-sm">
      <button className={className} style={{ justifySelf: "start" }} disabled={disabled || busy || decision.state === "blocked"} onClick={click}
        title={decision.state === "blocked" ? decision.reason ?? undefined : undefined}>
        {busy ? <Spinner /> : <Icon name="play" size={14} />} {children}
      </button>
      {decision.state === "blocked" && !disabled && (
        <span className="small" style={{ color: "var(--err)" }}>Non si può avviare: {decision.reason}</span>
      )}
      {confirm && (
        <Modal
          title={confirmTitle(confirm)}
          onClose={() => setConfirm(null)}
          footer={
            <>
              <button className="btn" onClick={() => setConfirm(null)}>Annulla</button>
              <button className="btn btn-primary" onClick={() => { setConfirm(null); void launch(); }}>Avvia comunque</button>
            </>
          }
        >
          <div className="stack-sm">
            {confirm.map((c, i) => <CheckAlert key={`${i}${c.code}`} check={c} />)}
          </div>
        </Modal>
      )}
    </div>
  );
}
