// Aggiornamenti in tempo reale dei job (Server-Sent Events) con ricaduta sul polling. Logica senza React,
// testata con vitest con flussi e timer finti: scelta fra flusso e polling, riconnessione con backoff,
// ricomposizione del log, aggiornamento degli elenchi.
import type { JobLogChunk, JobSummary } from "../api/types";

/** Stato di un flusso: spento, in connessione, aperto, in attesa di riconnessione (intanto si fa polling). */
export type FeedState = "off" | "connecting" | "open" | "retry";

export const RECONNECT_BASE_MS = 1000;
export const RECONNECT_MAX_MS = 30_000;
/** Senza un primo evento entro questo tempo la connessione si considera fallita (es. un proxy che bufferizza). */
export const CONNECT_TIMEOUT_MS = 5000;
/** Dopo "reconnect" (durata massima del flusso raggiunta) si riapre quasi subito, senza contarlo come errore. */
export const REOPEN_MS = 100;
/** Testo del log tenuto in memoria (caratteri finali). */
export const LOG_MAX_CHARS = 200_000;

/** Attesa prima del tentativo di riconnessione numero `failures` (≥ 1): 1, 2, 4, 8, 16, poi 30 s. */
export function backoffDelay(failures: number, base = RECONNECT_BASE_MS, max = RECONNECT_MAX_MS): number {
  if (failures <= 0) return 0;
  return Math.min(max, base * 2 ** Math.min(failures - 1, 30));
}

/** Il flusso serve gli aggiornamenti (o sta per farlo): il polling si sospende. */
export function isLive(state: FeedState): boolean {
  return state === "connecting" || state === "open";
}

/** Il browser ha EventSource (altrimenti si resta sul polling). */
export function streamSupported(): boolean {
  return typeof EventSource !== "undefined";
}

/**
 * Il flusso di un job va aperto: job attivo, scheda visibile (le schede nascoste chiudono i flussi: un browser
 * tiene al massimo 6 connessioni HTTP/1.1 per server) e caricamento completo già fatto, così il flusso parte
 * dall'offset del log appena letto.
 */
export function wantJobStream(o: { hasJob: boolean; active: boolean; visible: boolean; loaded: boolean; supported: boolean }): boolean {
  return o.hasJob && o.active && o.visible && o.loaded && o.supported;
}

/**
 * Intervallo di polling di `useJob` in ms (null = nessuno). Job attivo: nessun polling mentre il flusso è
 * attivo, altrimenti ogni `activeMs`. Job terminato ma riprendibile: controllo lento (la ripresa può arrivare
 * da un'altra scheda); il flusso riparte quando il job torna attivo. Job che il server non ha (`gone`: 404,
 * eliminato, link vecchio): nessun polling.
 */
export function jobPollInterval(o: { hasJob: boolean; active: boolean; watch: boolean; live: boolean; gone?: boolean }, activeMs = 900, watchMs = 5000): number | null {
  if (!o.hasJob || o.gone) return null;
  if (o.active) return o.live ? null : activeMs;
  return o.watch ? watchMs : null;
}

/** Intervallo di polling di un elenco: nessuno con il flusso attivo, altrimenti `busyMs` con job attivi e `idleMs` senza. */
export function listPollInterval(live: boolean, busy: boolean, busyMs: number, idleMs: number | null): number | null {
  if (live) return null;
  return busy ? busyMs : idleMs;
}

/**
 * Parte nuova di un blocco di log rispetto ai byte già letti (`current`); null se non c'è nulla da aggiungere.
 * - contiguo: tutto il testo;
 * - già letto (per esempio da un polling concorrente): niente;
 * - sovrapposto: solo i byte successivi a `current` (il server invia righe intere di UTF-8 valido, quindi il
 *   taglio in byte cade sull'inizio di un carattere);
 * - con un buco (evento di un flusso aperto prima di un caricamento completo): niente, il flusso riaperto
 *   dall'offset giusto lo rimanda.
 */
export function sliceLogChunk(current: number, chunk: JobLogChunk | null | undefined): { text: string; offset: number } | null {
  if (!chunk || typeof chunk.start !== "number" || typeof chunk.offset !== "number" || typeof chunk.text !== "string") return null;
  if (chunk.offset <= current || chunk.start > current) return null;
  if (chunk.start === current) return { text: chunk.text, offset: chunk.offset };
  const bytes = new TextEncoder().encode(chunk.text);
  return { text: new TextDecoder().decode(bytes.subarray(current - chunk.start)), offset: chunk.offset };
}

/**
 * Blocco di log arrivato dal polling, chiesto dall'offset `from`: da accodare solo se nel frattempo nessun altro
 * (il flusso SSE, riaperto mentre la richiesta era in corso) ha fatto avanzare l'offset, che ora è `current`;
 * altrimenti null (le stesse righe arriverebbero due volte). Il polling accetta anche un blocco che non riparte
 * da `from` (log accorciato: il server riparte dalla coda), così l'offset si riallinea.
 */
export function polledLogChunk(from: number, current: number, chunk: Pick<JobLogChunk, "text" | "offset"> | null | undefined): { text: string; offset: number } | null {
  if (!chunk || current !== from || typeof chunk.offset !== "number" || typeof chunk.text !== "string") return null;
  return { text: chunk.text, offset: chunk.offset };
}

/** Dati JSON di un evento SSE; null se illeggibili. */
export function parseData(raw: unknown): unknown {
  if (typeof raw !== "string") return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function isJobSummary(data: unknown): data is JobSummary {
  const job = data as JobSummary | null;
  return !!job && typeof job === "object" && typeof job.id === "string" && typeof job.status === "string";
}

/**
 * Il job (o il riepilogo) tenuto da useJob, solo se è quello chiesto. Al cambio di id lo stato contiene ancora il
 * job precedente fino all'effetto che lo azzera, cioè per un render intero: una pagina che passa da un'analisi
 * completata a una appena avviata mostrerebbe il risultato vecchio e chiederebbe dati del job nuovo ancora in corso.
 */
export function ownJob<T extends { id: string }>(value: T | null, jobId: string | null | undefined): T | null {
  return value && jobId && value.id === jobId ? value : null;
}

/**
 * Un riepilogo arrivato dal flusso dell'elenco: aggiorna sul posto il job già in elenco; un job nuovo che
 * rientra nel filtro chiede di ricaricare l'elenco (posizione e limite li decide il server).
 */
export function mergeJob(list: JobSummary[] | null, job: JobSummary, matches: (j: JobSummary) => boolean): { list: JobSummary[] | null; reload: boolean } {
  const index = list ? list.findIndex((j) => j.id === job.id) : -1;
  if (!list || index < 0) return { list, reload: matches(job) };
  const next = list.slice();
  next[index] = { ...list[index], ...job };
  return { list: next, reload: false };
}

/**
 * Ultimi riepiloghi arrivati dal flusso dell'elenco, da riapplicare agli elenchi ricaricati. Una ricarica partita
 * prima di un evento può rispondere dopo, con lo stato letto prima (un job appena concluso ancora "in esecuzione"),
 * e il flusso non rimanda un job che non cambia più: sopra ogni elenco ricaricato vale il riepilogo più recente per
 * `updated_at` (a parità, quello del flusso: è stato inviato dopo la scrittura). Si tengono solo i riepiloghi dei job
 * in elenco ancora più recenti della ricarica, quindi al più uno per riga.
 */
export class RecentJobs {
  private readonly seen = new Map<string, JobSummary>();

  remember(job: JobSummary): void {
    this.seen.set(job.id, job);
  }

  get size(): number {
    return this.seen.size;
  }

  /** L'elenco con i riepiloghi più recenti; lo stesso oggetto se non cambia nulla. */
  apply(list: JobSummary[]): JobSummary[] {
    let out: JobSummary[] | null = null;
    const listed = new Set<string>();
    for (let i = 0; i < list.length; i++) {
      const row = list[i];
      listed.add(row.id);
      const newer = this.seen.get(row.id);
      if (!newer) continue;
      if ((newer.updated_at ?? "") < (row.updated_at ?? "")) {
        this.seen.delete(row.id); // la ricarica è già più recente
        continue;
      }
      const fields = Object.keys(newer) as (keyof JobSummary)[];
      if (fields.every((k) => newer[k] === row[k])) continue; // già uguale
      if (!out) out = list.slice();
      out[i] = { ...row, ...newer };
    }
    for (const id of [...this.seen.keys()]) if (!listed.has(id)) this.seen.delete(id);
    return out ?? list;
  }
}

// ------------------------------------------------------------------------------------------ flussi e timer

/** Ciò che serve di EventSource (sostituibile nei test). */
export interface SourceLike {
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
  onerror: ((event: Event) => void) | null;
}

export type SourceFactory = (url: string) => SourceLike;

export interface Timers {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

export const realTimers: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/** EventSource del browser, o null se manca. */
export function nativeSource(): SourceFactory | null {
  return streamSupported() ? (url) => new EventSource(url) as unknown as SourceLike : null;
}

export interface LiveFeedOptions {
  /** URL valutato a ogni connessione (per esempio con l'offset del log aggiornato) */
  url: () => string;
  /** eventi SSE da ricevere */
  events: readonly string[];
  onEvent: (type: string, data: unknown) => void;
  onState?: (state: FeedState) => void;
  /** eventi dopo i quali il flusso è concluso: si chiude senza riconnettersi (es. "end" di un job) */
  final?: readonly string[];
  /** fabbrica dei flussi; null = non supportati (solo polling). Predefinita: EventSource del browser */
  source?: SourceFactory | null;
  timers?: Timers;
  connectTimeoutMs?: number;
}

/**
 * Un flusso SSE che si riconnette da solo. Un errore (connessione caduta, 401/404/503, server fermo, nessun
 * evento entro CONNECT_TIMEOUT_MS) chiude la connessione e riprova dopo `backoffDelay` crescente: nel frattempo
 * lo stato è "retry" e chi lo usa ricade sul polling. Il primo evento ricevuto azzera il backoff. "reconnect"
 * (durata massima raggiunta sul server) riapre subito. EventSource non riprova da solo: la riconnessione è
 * gestita qui.
 */
export class LiveFeed {
  private current: SourceLike | null = null;
  private failures = 0;
  private running = false;
  private retryTimer: unknown = null;
  private connectTimer: unknown = null;
  private status: FeedState = "off";
  private readonly factory: SourceFactory | null;
  private readonly timers: Timers;

  constructor(private readonly opts: LiveFeedOptions) {
    this.factory = opts.source === undefined ? nativeSource() : opts.source;
    this.timers = opts.timers ?? realTimers;
  }

  get state(): FeedState {
    return this.status;
  }

  /** Errori consecutivi (0 dopo un evento ricevuto). */
  get attempts(): number {
    return this.failures;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    if (!this.factory) {
      this.setState("off"); // nessun EventSource: resta il polling
      return;
    }
    this.connect();
  }

  stop(): void {
    this.running = false;
    this.clearTimers();
    this.close();
    this.setState("off");
  }

  private connect(): void {
    this.clearTimers();
    this.close();
    if (!this.running || !this.factory) return;
    let source: SourceLike;
    try {
      source = this.factory(this.opts.url());
    } catch {
      this.fail();
      return;
    }
    this.current = source;
    this.setState("connecting");
    let received = false;
    const types = new Set([...this.opts.events, ...(this.opts.final ?? []), "reconnect"]);
    for (const type of types) {
      source.addEventListener(type, (event) => {
        if (this.current !== source) return; // evento di una connessione già chiusa
        if (!received) {
          received = true;
          this.failures = 0;
          this.clearTimers();
          this.setState("open");
        }
        if (type === "reconnect") {
          this.close();
          this.setState("connecting");
          this.retryTimer = this.timers.set(() => this.connect(), REOPEN_MS);
          return;
        }
        this.opts.onEvent(type, parseData(event.data));
        if (this.opts.final?.includes(type) && this.current === source) {
          this.running = false;
          this.clearTimers();
          this.close();
          this.setState("off");
        }
      });
    }
    source.onerror = () => {
      if (this.current === source) this.fail();
    };
    this.connectTimer = this.timers.set(() => {
      this.connectTimer = null;
      if (this.current === source && !received) this.fail();
    }, this.opts.connectTimeoutMs ?? CONNECT_TIMEOUT_MS);
  }

  private fail(): void {
    this.clearTimers();
    this.close();
    if (!this.running) return;
    this.failures += 1;
    this.setState("retry");
    this.retryTimer = this.timers.set(() => this.connect(), backoffDelay(this.failures));
  }

  private close(): void {
    const source = this.current;
    this.current = null;
    if (source) {
      source.onerror = null;
      source.close();
    }
  }

  private clearTimers(): void {
    if (this.retryTimer !== null) this.timers.clear(this.retryTimer);
    if (this.connectTimer !== null) this.timers.clear(this.connectTimer);
    this.retryTimer = null;
    this.connectTimer = null;
  }

  private setState(state: FeedState): void {
    if (state === this.status) return;
    this.status = state;
    this.opts.onState?.(state);
  }
}

export interface FeedListener {
  onEvent: (type: string, data: unknown) => void;
  onState?: (state: FeedState) => void;
}

/**
 * Un solo flusso condiviso da più componenti (il flusso dell'elenco dei job: contatore della barra laterale,
 * pagina Job, tabella della pipeline): aperto al primo iscritto, chiuso quando se ne va l'ultimo. Così ogni
 * scheda tiene al massimo una connessione per l'elenco, più una per il job aperto.
 */
export class FeedHub {
  private readonly listeners = new Set<FeedListener>();
  private feed: LiveFeed | null = null;
  private status: FeedState = "off";

  constructor(private readonly create: (listener: FeedListener) => LiveFeed) {}

  get state(): FeedState {
    return this.status;
  }

  get subscribers(): number {
    return this.listeners.size;
  }

  subscribe(listener: FeedListener): () => void {
    this.listeners.add(listener);
    listener.onState?.(this.status);
    if (!this.feed) {
      const feed = this.create({
        onEvent: (type, data) => {
          for (const l of [...this.listeners]) l.onEvent(type, data);
        },
        onState: (state) => {
          if (this.feed !== feed) return;
          this.status = state;
          for (const l of [...this.listeners]) l.onState?.(state);
        },
      });
      this.feed = feed;
      feed.start();
    }
    return () => {
      if (!this.listeners.delete(listener) || this.listeners.size > 0 || !this.feed) return;
      const feed = this.feed;
      this.feed = null;
      this.status = "off";
      feed.stop();
    };
  }
}

/** `fn` subito e poi al massimo una volta ogni `ms`; una richiesta arrivata nel frattempo non si perde (una sola). */
export function throttle(fn: () => void, ms: number, timers: Timers = realTimers): { call: () => void; cancel: () => void } {
  let handle: unknown = null;
  let queued = false;
  const release = () => {
    handle = null;
    if (queued) {
      queued = false;
      call();
    }
  };
  const call = () => {
    if (handle !== null) {
      queued = true;
      return;
    }
    fn();
    handle = timers.set(release, ms);
  };
  const cancel = () => {
    if (handle !== null) timers.clear(handle);
    handle = null;
    queued = false;
  };
  return { call, cancel };
}
