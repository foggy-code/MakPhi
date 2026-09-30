import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Job, JobSummary, ResumeScope } from "../api/types";
import { isActive } from "./format";
import { cancelAndSync, type JobSync, recordStatus, resumeAndSync } from "./status";

/** Controllo lento di un job terminato ma riprendibile: la ripresa può arrivare da un'altra scheda. */
const WATCH_RESUMABLE_MS = 5000;

/** Evento: licenze o configurazione cambiate (la barra laterale e gli avvisi si aggiornano). */
export const SYSTEM_CHANGED = "bioloom:system-changed";

export function notifySystemChanged() {
  window.dispatchEvent(new CustomEvent(SYSTEM_CHANGED));
}

/** Caricamento asincrono con stato, errore e ricarica. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    fn()
      .then((d) => {
        if (id === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => id === seq.current && setError(e instanceof ApiError ? e : new ApiError(String(e), 0, "error")))
      .finally(() => id === seq.current && setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export function useInterval(fn: () => void, ms: number | null) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

/**
 * Segue un job fino al termine: stato leggero durante l'esecuzione, job completo alla fine.
 * Un job che torna attivo (ripresa di una pipeline) riprende il polling e il log continua dall'offset.
 */
export function useJob<R = unknown>(jobId: string | null | undefined, opts: { withLog?: boolean } = {}) {
  const [job, setJob] = useState<Job<R> | null>(null);
  const [summary, setSummary] = useState<JobSummary | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const offset = useRef(0);
  const last = useRef<JobSummary | null>(null); // ultimo stato visto, per ricaricare il job solo quando serve
  const active = summary ? isActive(summary.status) : !!jobId;
  const watch = !active && !!job?.resumable;

  const loadFull = useCallback(async () => {
    if (!jobId) return;
    try {
      const full = await api.job<R>(jobId);
      last.current = full;
      setJob(full);
      setSummary(full);
      if (opts.withLog) {
        const chunk = await api.jobLog(jobId, 0);
        setLog(chunk.text.slice(-200_000));
        offset.current = chunk.offset;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [jobId, opts.withLog]);
  const sync = useMemo<JobSync>(() => ({ last, setSummary, loadFull }), [loadFull]);

  useEffect(() => {
    setJob(null);
    setSummary(null);
    setLog("");
    setError(null);
    offset.current = 0;
    last.current = null;
    if (jobId) loadFull();
  }, [jobId, loadFull]);

  const poll = useCallback(async () => {
    if (!jobId) return;
    try {
      const s = await api.jobStatus(jobId);
      const reload = recordStatus(sync, s);
      if (opts.withLog && isActive(s.status)) {
        const chunk = await api.jobLog(jobId, offset.current);
        if (chunk.text) setLog((prev) => (prev + chunk.text).slice(-200_000));
        offset.current = chunk.offset;
      }
      if (reload) await loadFull();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [jobId, loadFull, opts.withLog, sync]);

  useInterval(poll, jobId && active ? 900 : jobId && watch ? WATCH_RESUMABLE_MS : null);

  /** Annulla il job; uno in coda si chiude subito e viene ricaricato (pulsanti di ripresa compresi). */
  const cancel = useCallback(async () => {
    if (jobId) await cancelAndSync(sync, () => api.cancelJob(jobId));
  }, [jobId, sync]);

  /** Riprende una pipeline terminata: il job torna in coda, si ricarica (file azzerati) e il polling riparte. */
  const resume = useCallback(async (scope: ResumeScope = "incomplete") => {
    if (jobId) await resumeAndSync(sync, () => api.resumeJob(jobId, scope));
  }, [jobId, sync]);

  return { job, summary: summary ?? job, log, error, active, cancel, resume, reload: loadFull };
}

export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage non disponibile */
    }
  }, [key, value]);
  return [value, setValue] as const;
}
