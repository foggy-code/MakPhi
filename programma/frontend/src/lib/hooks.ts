import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Job, JobLogChunk, JobSummary, ResumeScope } from "../api/types";
import { isActive } from "./format";
import {
  FeedHub, type FeedState, isJobSummary, isLive, jobPollInterval, LiveFeed, LOG_MAX_CHARS, mergeJob, ownJob,
  polledLogChunk, RecentJobs, sliceLogChunk, streamSupported, throttle, wantJobStream,
} from "./live";
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- le dipendenze di fn le dichiara chi chiama useAsync
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
 * La scheda è visibile. Le schede nascoste chiudono i flussi in tempo reale (un browser tiene al massimo 6
 * connessioni HTTP/1.1 per server, contando tutte le schede) e li riaprono quando si torna.
 */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

/** `fn` al massimo una volta ogni `ms` (l'ultima richiesta non si perde): ricariche guidate dagli eventi. */
export function useThrottled(fn: () => void, ms: number): () => void {
  const saved = useRef(fn);
  saved.current = fn;
  const limited = useMemo(() => throttle(() => saved.current(), ms), [ms]);
  useEffect(() => () => limited.cancel(), [limited]);
  return limited.call;
}

let jobsHub: FeedHub | null = null;

/** Flusso SSE dell'elenco dei job, uno per scheda qualunque sia il numero di componenti che lo usano. */
function sharedJobsFeed(): FeedHub {
  jobsHub ??= new FeedHub((listener) => new LiveFeed({
    url: () => api.jobsEventsUrl(),
    events: ["ready", "job", "items", "active", "deleted", "resync"],
    onEvent: listener.onEvent,
    onState: listener.onState,
  }));
  return jobsHub;
}

/**
 * Eventi del flusso dell'elenco dei job (`ready`, `job`, `items`, `active`, `deleted`, `resync`). Restituisce
 * true mentre il flusso serve gli aggiornamenti: il chiamante sospende il suo polling, che riprende se il
 * flusso cade. Dopo ogni (ri)connessione arriva `ready`: è il momento di ricaricare ciò che si è perso.
 */
export function useJobsFeed(onEvent: (type: string, data: unknown) => void): boolean {
  const visible = usePageVisible();
  const [state, setState] = useState<FeedState>("off");
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => {
    if (!visible || !streamSupported()) {
      setState("off");
      return;
    }
    return sharedJobsFeed().subscribe({ onEvent: (type, data) => handler.current(type, data), onState: setState });
  }, [visible]);
  return isLive(state);
}

/**
 * Elenco di job tenuto aggiornato dal flusso dell'elenco: i job presenti si aggiornano sul posto (stato,
 * progresso, tempo residuo); un job nuovo che rientra in `matches`, un job eliminato o una riconnessione
 * ricaricano l'elenco, al più una volta al secondo. Un elenco ricaricato riceve sopra i riepiloghi del flusso più
 * recenti della sua lettura (`RecentJobs`): una ricarica lenta non riporta indietro un job già concluso.
 * Restituisce true mentre il flusso è attivo (niente polling).
 */
export function useLiveJobList(
  list: JobSummary[] | null,
  setList: (list: JobSummary[] | null) => void,
  reload: () => void,
  matches: (job: JobSummary) => boolean,
): boolean {
  const shown = useRef(list);
  shown.current = list;
  const recent = useRef(new RecentJobs());
  const own = useRef<JobSummary[] | null>(null); // ultimo elenco scritto qui (gli altri vengono dalle ricariche)
  const refresh = useThrottled(reload, 1000);
  useEffect(() => {
    if (!list || list === own.current) return;
    const fixed = recent.current.apply(list);
    if (fixed !== list) {
      own.current = shown.current = fixed;
      setList(fixed);
    }
  }, [list, setList]);
  return useJobsFeed((type, data) => {
    if (type === "job" && isJobSummary(data)) {
      if (matches(data) || shown.current?.some((j) => j.id === data.id)) recent.current.remember(data);
      const merged = mergeJob(shown.current, data, matches);
      if (merged.list !== shown.current) {
        own.current = shown.current = merged.list; // eventi ravvicinati si sommano prima del nuovo render
        setList(merged.list);
      }
      if (merged.reload) refresh();
    } else if (type === "ready" || type === "resync" || type === "deleted") {
      refresh();
    }
  });
}

/**
 * Segue un job fino al termine: stato leggero durante l'esecuzione, job completo alla fine.
 * Durante l'esecuzione usa il flusso SSE del job (stato, righe di log, fine); se il flusso non è disponibile o
 * cade, ricade sul polling finché la riconnessione (con backoff) non riesce. Un job che torna attivo (ripresa
 * di una pipeline) riapre il flusso, e il log continua dall'offset.
 */
export function useJob<R = unknown>(jobId: string | null | undefined, opts: { withLog?: boolean } = {}) {
  const [job, setJob] = useState<Job<R> | null>(null);
  const [summary, setSummary] = useState<JobSummary | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  // il server non ha il job (eliminato, link vecchio): nessun polling che riceverebbe sempre 404
  const [gone, setGone] = useState(false);
  const [loads, setLoads] = useState(0); // caricamenti completi: il flusso (ri)parte dall'offset appena letto
  const [feed, setFeed] = useState<FeedState>("off");
  const visible = usePageVisible();
  const offset = useRef(0);
  const last = useRef<JobSummary | null>(null); // ultimo stato visto, per ricaricare il job solo quando serve
  const wanted = useRef(jobId); // un caricamento del job precedente che risponde dopo il cambio non scrive nulla
  wanted.current = jobId;
  // solo il job chiesto: al cambio di id lo stato tiene il precedente per un render (ownJob)
  const current = ownJob(job, jobId);
  const currentSummary = ownJob(summary, jobId) ?? current;
  const active = currentSummary ? isActive(currentSummary.status) : !!jobId;
  const watch = !active && !!current?.resumable;
  const withLog = !!opts.withLog;

  const loadFull = useCallback(async () => {
    if (!jobId) return;
    try {
      const full = await api.job<R>(jobId);
      if (wanted.current !== jobId) return;
      last.current = full;
      setJob(full);
      setSummary(full);
      if (withLog) {
        const chunk = await api.jobLog(jobId, 0);
        setLog(chunk.text.slice(-LOG_MAX_CHARS));
        offset.current = chunk.offset;
      }
      setLoads((n) => n + 1);
    } catch (e) {
      if (wanted.current !== jobId) return;
      setError(e instanceof Error ? e.message : String(e));
      if (e instanceof ApiError && e.status === 404) setGone(true);
    }
  }, [jobId, withLog]);
  const sync = useMemo<JobSync>(() => ({ last, setSummary, loadFull }), [loadFull]);

  useEffect(() => {
    setJob(null);
    setSummary(null);
    setLog("");
    setError(null);
    setGone(false);
    setLoads(0);
    offset.current = 0;
    last.current = null;
    if (jobId) loadFull();
  }, [jobId, loadFull]);

  const streaming = wantJobStream({ hasJob: !!jobId, active, visible, loaded: loads > 0, supported: streamSupported() });
  useEffect(() => {
    if (!streaming || !jobId) return;
    const live = new LiveFeed({
      url: () => api.jobEventsUrl(jobId, withLog ? offset.current : null),
      events: ["status", "log"],
      final: ["end"],
      onState: setFeed,
      onEvent: (type, data) => {
        if (type === "log") {
          const part = sliceLogChunk(offset.current, data as JobLogChunk);
          if (part) {
            offset.current = part.offset;
            if (part.text) setLog((prev) => (prev + part.text).slice(-LOG_MAX_CHARS));
          }
        } else if (type === "end" && (data as { deleted?: boolean } | null)?.deleted) {
          setError("Il job è stato eliminato.");
          setGone(true);
        } else if (isJobSummary(data) && recordStatus(sync, data)) {
          void loadFull(); // terminato ("end" porta lo stato finale, a log completo): risultato e file
        }
      },
    });
    live.start();
    return () => live.stop();
  }, [streaming, jobId, loads, withLog, sync, loadFull]);

  const poll = useCallback(async () => {
    if (!jobId) return;
    try {
      const s = await api.jobStatus(jobId);
      const reload = recordStatus(sync, s);
      if (withLog && isActive(s.status)) {
        const from = offset.current;
        const chunk = await api.jobLog(jobId, from);
        // il flusso riaperto nel frattempo (scheda di nuovo visibile) può aver già accodato queste righe
        const part = polledLogChunk(from, offset.current, chunk);
        if (part) {
          offset.current = part.offset;
          if (part.text) setLog((prev) => (prev + part.text).slice(-LOG_MAX_CHARS));
        }
      }
      if (reload) await loadFull();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (e instanceof ApiError && e.status === 404) setGone(true);
    }
  }, [jobId, loadFull, withLog, sync]);

  // polling solo senza flusso (non supportato, scheda nascosta, connessione caduta) e per i job riprendibili
  useInterval(poll, jobPollInterval({ hasJob: !!jobId, active, watch, live: isLive(feed), gone }, 900, WATCH_RESUMABLE_MS));

  /** Annulla il job; uno in coda si chiude subito e viene ricaricato (pulsanti di ripresa compresi). */
  const cancel = useCallback(async () => {
    if (jobId) await cancelAndSync(sync, () => api.cancelJob(jobId));
  }, [jobId, sync]);

  /** Riprende una pipeline terminata: il job torna in coda, si ricarica (file azzerati) e il polling riparte. */
  const resume = useCallback(async (scope: ResumeScope = "incomplete") => {
    if (jobId) await resumeAndSync(sync, () => api.resumeJob(jobId, scope));
  }, [jobId, sync]);

  return { job: current, summary: currentSummary, log, error, gone, active, cancel, resume, reload: loadFull };
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
