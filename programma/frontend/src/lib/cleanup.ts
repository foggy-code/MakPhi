// Pulizia dei job ed eliminazione dei progetti: logica senza React (criteri e richiesta, anteprima con debounce, ciclo
// a tempo delle chiamate, testi), testata con vitest con chiamate e timer finti. JobCleanupDialog e ProjectsDialog
// ricevono tutto da qui. Regole di sicurezza: ambito sempre esplicito (mai un id sconosciuto che diventa «tutti i
// progetti»), mai stati attivi, stella ed etichette conservate per impostazione predefinita, completati solo con
// la conferma «Ho capito».
import { ApiError } from "../api/client";
import type { CleanupPreview, CleanupRequest, CleanupRun, CleanupStatus, ProjectDeleteResult, ProjectUsage } from "../api/types";
import { fmtBytes, fmtInt } from "./format";
import { realTimers, type Timers } from "./live";

export type CleanupScope = CleanupRequest["scope"];

/** Stati eliminabili, nell'ordine dell'interfaccia (i completati per ultimi: richiedono «Ho capito»). */
export const CLEANUP_STATUSES: CleanupStatus[] = ["failed", "cancelled", "completed"];

export const STATUS_LABEL: Record<CleanupStatus, string> = {
  failed: "Falliti",
  cancelled: "Annullati",
  completed: "Completati",
};

export const DEFAULT_AGE_DAYS = 7;

/** Età minima dei job da eliminare; un job fallito da pochi minuti è probabilmente ancora in diagnosi. */
export const AGE_OPTIONS: { value: number | null; label: string }[] = [
  { value: 7, label: "oltre 7 giorni" },
  { value: 30, label: "oltre 30 giorni" },
  { value: 90, label: "oltre 90 giorni" },
  { value: null, label: "qualsiasi età" },
];

/** Criteri scelti nel dialogo (diventano una `CleanupRequest` con `buildCleanupRequest`). */
export interface CleanupCriteria {
  scope: CleanupScope;
  statuses: CleanupStatus[];
  olderThanDays: number | null;
  /** un tipo di job, null = tutti */
  kind: string | null;
  keepFavorites: boolean;
  keepTagged: boolean;
  /** anche le pipeline fallite o annullate con elementi già completati (riprendibili) */
  includeResumable: boolean;
  /** conferma «Ho capito», obbligatoria con i completati */
  understood: boolean;
}

/** Valori sicuri a ogni apertura: falliti e annullati, oltre 7 giorni, stella ed etichette conservate. */
export function defaultCriteria(initial: { scope?: CleanupScope; kind?: string | null } = {}): CleanupCriteria {
  return {
    scope: initial.scope ?? "project",
    statuses: ["failed", "cancelled"],
    olderThanDays: DEFAULT_AGE_DAYS,
    kind: initial.kind || null,
    keepFavorites: true,
    keepTagged: true,
    includeResumable: false,
    understood: false,
  };
}

/** Stato aggiunto o tolto, nell'ordine di CLEANUP_STATUSES; togliere i completati azzera «Ho capito». */
export function toggleStatus(c: CleanupCriteria, s: CleanupStatus): CleanupCriteria {
  const on = c.statuses.includes(s);
  const statuses = CLEANUP_STATUSES.filter((x) => (x === s ? !on : c.statuses.includes(x)));
  return { ...c, statuses, understood: s === "completed" && on ? false : c.understood };
}

export function completedIncluded(c: CleanupCriteria): boolean {
  return c.statuses.includes("completed");
}

/**
 * Corpo delle rotte di pulizia; null se manca qualcosa: nessuno stato, oppure ambito «Questo progetto» senza un
 * progetto noto (mai una richiesta per tutti i progetti al posto di un id sconosciuto). Con «Tutti i progetti»
 * nessun `project_id`; senza tipo nessun `kinds`.
 */
export function buildCleanupRequest(c: CleanupCriteria, projectId: string | null): CleanupRequest | null {
  if (!c.statuses.length) return null;
  if (c.scope !== "project" && c.scope !== "all") return null;
  if (c.scope === "project" && !projectId) return null;
  const req: CleanupRequest = {
    scope: c.scope,
    statuses: CLEANUP_STATUSES.filter((s) => c.statuses.includes(s)),
    older_than_days: c.olderThanDays,
    keep_favorites: c.keepFavorites,
    keep_tagged: c.keepTagged,
    include_resumable: c.includeResumable,
  };
  if (c.scope === "project" && projectId) req.project_id = projectId;
  if (c.kind) req.kinds = [c.kind];
  return req;
}

/**
 * Chiave dei criteri di una richiesta (JSON a chiavi in ordine fisso, senza `max_seconds` né `as_of`): anteprima ed
 * esecuzione coincidono.
 */
export function criteriaKey(r: CleanupRequest): string {
  return JSON.stringify({
    scope: r.scope,
    project_id: r.project_id ?? null,
    statuses: [...r.statuses].sort(),
    kinds: r.kinds ? [...r.kinds].sort() : null,
    older_than_days: r.older_than_days ?? null,
    keep_favorites: r.keep_favorites,
    keep_tagged: r.keep_tagged,
    include_resumable: r.include_resumable,
  });
}

// ---------------------------------------------------------------------------------------------- anteprima

/** Stato dell'anteprima: `key` = chiave dei criteri a cui si riferisce (una risposta vecchia non vale per i nuovi). */
export type PreviewState =
  | { status: "idle" }
  | { status: "waiting" | "loading"; key: string }
  | { status: "ready"; key: string; data: CleanupPreview }
  | { status: "error"; key: string; message: string };

export interface PreviewLoop {
  /** nuovi criteri (null = richiesta non valida: stato `idle`, nessuna chiamata) */
  request(req: CleanupRequest | null): void;
  /** di nuovo, subito, l'ultima richiesta (dopo un errore) */
  retry(): void;
  /** fine: nessuna chiamata né stato in più */
  stop(): void;
}

/**
 * Anteprima che segue i criteri: debounce di `delayMs`, una sola richiesta in volo (il calcolo dei byte può durare
 * secondi) e, se intanto i criteri cambiano, alla fine parte la più recente; le risposte di criteri superati si
 * scartano. Una richiesta null annulla il timer e torna a `idle`; un errore dà `error` (si ripete con `retry`).
 */
export function makePreviewLoop(o: {
  call: (req: CleanupRequest) => Promise<CleanupPreview>;
  onState: (s: PreviewState) => void;
  timers?: Timers;
  delayMs?: number;
}): PreviewLoop {
  const timers = o.timers ?? realTimers;
  const delayMs = o.delayMs ?? 400;
  let wanted: { req: CleanupRequest; key: string } | null = null;
  let timer: unknown = null;
  let busy = false; // una richiesta in volo
  let queued = false; // il debounce è scaduto mentre c'era una richiesta in volo
  let stopped = false;
  const emit = (s: PreviewState) => {
    if (!stopped) o.onState(s);
  };
  const clearTimer = () => {
    if (timer !== null) timers.clear(timer);
    timer = null;
  };
  // risposta per i criteri ancora voluti: vale, e il timer o la coda per gli stessi criteri non servono più
  const settle = (key: string, s: PreviewState) => {
    if (stopped || wanted?.key !== key) return;
    clearTimer();
    queued = false;
    emit(s);
  };
  const start = () => {
    const w = wanted;
    if (!w || stopped) return;
    busy = true;
    emit({ status: "loading", key: w.key });
    let pending: Promise<CleanupPreview>;
    try {
      pending = o.call(w.req);
    } catch (e) {
      pending = Promise.reject(e);
    }
    pending
      .then(
        (data) => settle(w.key, { status: "ready", key: w.key, data }),
        (e) => settle(w.key, { status: "error", key: w.key, message: errorMessage(e) }),
      )
      .finally(() => {
        busy = false;
        if (queued && !stopped) {
          queued = false;
          start();
        }
      });
  };
  const fire = () => {
    timer = null;
    if (busy) queued = true;
    else start();
  };
  return {
    request(req) {
      if (stopped) return;
      if (!req) {
        clearTimer();
        queued = false;
        wanted = null;
        emit({ status: "idle" });
        return;
      }
      const key = criteriaKey(req);
      if (wanted?.key === key) return; // stessi criteri: nulla da rifare
      wanted = { req, key };
      clearTimer();
      queued = false; // ci pensa il nuovo debounce
      emit({ status: "waiting", key });
      timer = timers.set(fire, delayMs);
    },
    retry() {
      if (stopped || !wanted) return;
      clearTimer();
      if (busy) queued = true;
      else start();
    },
    stop() {
      stopped = true;
      clearTimer();
      queued = false;
      wanted = null;
    },
  };
}

/** Byte di un'anteprima o dell'uso di un progetto: «almeno» se il calcolo non è finito in tempo. */
export function bytesText(bytes: number, complete: boolean): string {
  return `${complete ? "" : "almeno "}${fmtBytes(bytes)}`;
}

/** «42 job, 1,4 GB», «42 job, almeno 1,4 GB», «Nessun job corrisponde». */
export function previewLine(p: CleanupPreview): string {
  if (!p.matched) return "Nessun job corrisponde";
  return `${fmtInt(p.matched)} job, ${bytesText(p.bytes, p.bytes_complete)}`;
}

/**
 * La pulizia si può avviare: richiesta valida, anteprima pronta per i criteri attuali (non per quelli di prima),
 * almeno un job da eliminare e, con i completati, la conferma «Ho capito».
 */
export function canRun(o: { request: CleanupRequest | null; preview: PreviewState; key: string | null; completed: boolean; understood: boolean }): boolean {
  if (!o.request || !o.key || o.key !== criteriaKey(o.request)) return false;
  if (o.preview.status !== "ready" || o.preview.key !== o.key || !(o.preview.data.matched > 0)) return false;
  return !o.completed || o.understood;
}

/**
 * Anteprima da mostrare e stato della conferma, come li calcola il dialogo: mentre si ricalcola (`waiting`, `loading`)
 * resta visibile, attenuata, l'ultima anteprima pronta (mai un errore vecchio); `current` = è quella dei criteri attuali.
 */
export function dialogPreview(o: {
  request: CleanupRequest | null;
  loop: PreviewState;
  lastReady: PreviewState | null;
  completed: boolean;
  understood: boolean;
}): {
  key: string | null;
  shown: PreviewState;
  current: boolean;
  canRun: boolean;
} {
  const key = o.request ? criteriaKey(o.request) : null;
  const transient = o.loop.status === "waiting" || o.loop.status === "loading";
  const shown = transient && o.lastReady?.status === "ready" ? o.lastReady : o.loop;
  const current = (shown.status === "ready" || shown.status === "error") && shown.key === key;
  return { key, shown, current, canRun: canRun({ request: o.request, preview: o.loop, key, completed: o.completed, understood: o.understood }) };
}

/**
 * Richiesta dell'esecuzione confermata: i criteri attuali con l'`as_of` dell'anteprima pronta per quei criteri (quella
 * che l'utente ha visto), così un job finito dopo l'anteprima non si elimina; null se l'anteprima non è pronta per
 * questi criteri. Senza `as_of` nella risposta (server vecchio) la richiesta resta senza limite superiore.
 */
export function cleanupRunRequest(request: CleanupRequest | null, preview: PreviewState): CleanupRequest | null {
  if (!request || preview.status !== "ready" || preview.key !== criteriaKey(request)) return null;
  const { as_of: _ignored, ...base } = request;
  const asOf = preview.data.as_of;
  return typeof asOf === "string" && asOf ? { ...base, as_of: asOf } : base;
}

/** Progetto di «Questo progetto» visto dal dialogo della pulizia. */
export interface CleanupTarget {
  /** id per la richiesta; null se «Questo progetto» non è disponibile */
  id: string | null;
  /** nome da mostrare, anche quando non è più disponibile */
  name: string | null;
  /** il progetto attivo non è più quello dell'apertura: «Questo progetto» resta disattivato (mai un altro progetto al suo posto) */
  changed: boolean;
}

/**
 * «Questo progetto» = il progetto attivo all'apertura del dialogo (`pinned`), mai quello che diventa attivo dopo (per
 * esempio quando la rilettura dei progetti, al ritorno sulla scheda, cambia il progetto attivo): se cambia, l'ambito
 * non è più disponibile. Finché l'elenco dei progetti non è letto l'id è noto ma il progetto no: non disponibile, non cambiato.
 */
export function cleanupTarget(
  pinned: { id: string | null; name: string | null },
  live: { projectId: string | null; project: { id: string; name: string } | null },
): CleanupTarget {
  if (live.projectId !== pinned.id) return { id: null, name: pinned.name, changed: true };
  if (!pinned.id || !live.project || live.project.id !== pinned.id) return { id: null, name: pinned.name, changed: false };
  return { id: pinned.id, name: live.project.name, changed: false };
}

/** Destinazione effettiva della richiesta («all», «project:<id>», «project:» se non disponibile): quando cambia, «Ho capito» si azzera. */
export function cleanupTargetKey(scope: CleanupScope, projectId: string | null): string {
  return scope === "all" ? "all" : `project:${projectId ?? ""}`;
}

/** Criteri con «Ho capito» azzerato se la destinazione è cambiata (la conferma valeva per altri job). */
export function resetUnderstood(c: CleanupCriteria, prevTarget: string, nextTarget: string): CleanupCriteria {
  return prevTarget !== nextTarget && c.understood ? { ...c, understood: false } : c;
}

// ---------------------------------------------------------------------------------------------- esecuzione

/** Durata di ogni chiamata della pulizia (secondi): l'interfaccia resta reattiva e «Interrompi» arriva presto. */
export const CALL_SECONDS = 5;
/** Risposte consecutive senza eliminazioni dopo le quali il ciclo si ferma (job in uso, ripresi). */
export const NO_PROGRESS_LIMIT = 2;
/** Tetto di sicurezza delle chiamate di un ciclo. */
export const MAX_CALLS = 10000;

export interface CleanupTotals {
  /** somme di tutte le chiamate */
  deleted: number;
  freedBytes: number;
  leftover: number;
  /** dell'ultima chiamata: i saltati e i job in errore si ritrovano a ogni chiamata */
  skipped: number;
  errors: number;
  /** null prima della prima risposta */
  remaining: number | null;
  /** eliminati finora + saltati + errori + non ancora esaminati */
  total: number | null;
  calls: number;
  outcome: "done" | "stopped" | "stalled" | "error" | "capped" | null;
  error: string | null;
}

export function emptyTotals(): CleanupTotals {
  return { deleted: 0, freedBytes: 0, leftover: 0, skipped: 0, errors: 0, remaining: null, total: null, calls: 0, outcome: null, error: null };
}

const count = (v: unknown) => Number(v) || 0;

/** Messaggio di una risposta della pulizia senza la forma attesa. */
export const INVALID_RUN_MESSAGE = "Risposta del server non valida per la pulizia.";

/**
 * Risposta di una chiamata della pulizia controllata: un oggetto con conteggi numerici (finiti, non negativi) e
 * `leftover` numero o booleano; null altrimenti (un corpo di testo o vuoto non diventa mai «remaining 0» = finito).
 */
function parseRun(r: unknown): CleanupRun | null {
  if (!r || typeof r !== "object" || Array.isArray(r)) return null;
  const o = r as Record<string, unknown>;
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null);
  const deleted = num(o.deleted);
  const skipped = num(o.skipped);
  const errors = num(o.errors);
  const freed = num(o.freed_bytes);
  const remaining = num(o.remaining);
  const leftover = typeof o.leftover === "boolean" ? Number(o.leftover) : num(o.leftover);
  if (deleted === null || skipped === null || errors === null || freed === null || remaining === null || leftover === null) return null;
  return { deleted, skipped, errors, freed_bytes: freed, leftover, remaining };
}

/**
 * Pulizia completa come ciclo di chiamate brevi (`max_seconds` = `seconds`), strettamente una dopo l'altra. Finisce
 * con `done` (nessun job rimasto da esaminare: ha la precedenza), `stopped` (`shouldStop()`, controllato solo fra
 * una chiamata e l'altra), `stalled` (NO_PROGRESS_LIMIT risposte di fila senza eliminazioni), `capped` (`maxCalls`)
 * o `error` (chiamata fallita, risposta senza la forma attesa o eccezione di una callback): l'errore non si rilancia,
 * si restituiscono i totali fin lì con il messaggio.
 */
export async function runCleanup(
  request: CleanupRequest,
  call: (req: CleanupRequest) => Promise<CleanupRun>,
  opts: { seconds?: number; shouldStop?: () => boolean; onProgress?: (t: CleanupTotals) => void; maxCalls?: number } = {},
): Promise<CleanupTotals> {
  const body: CleanupRequest = { ...request, max_seconds: opts.seconds ?? CALL_SECONDS };
  const maxCalls = opts.maxCalls ?? MAX_CALLS;
  const t = emptyTotals();
  let idle = 0;
  while (!t.outcome) {
    try {
      if (opts.shouldStop?.()) {
        t.outcome = "stopped";
        break;
      }
      if (t.calls >= maxCalls) {
        t.outcome = "capped";
        break;
      }
      const r = parseRun(await call(body));
      if (!r) {
        t.outcome = "error";
        t.error = INVALID_RUN_MESSAGE;
        break;
      }
      t.calls += 1;
      t.deleted += r.deleted;
      t.freedBytes += r.freed_bytes;
      t.leftover += r.leftover;
      t.skipped = r.skipped;
      t.errors = r.errors;
      t.remaining = r.remaining;
      t.total = t.deleted + t.skipped + t.errors + t.remaining;
      idle = r.deleted > 0 ? 0 : idle + 1;
      if (t.remaining === 0) t.outcome = "done";
      else if (idle >= NO_PROGRESS_LIMIT) t.outcome = "stalled";
      opts.onProgress?.({ ...t });
    } catch (e) {
      t.outcome = "error";
      t.error = errorMessage(e);
      break;
    }
  }
  return t;
}

/** Frazione eliminata (0–1) per la barra di avanzamento. */
export function progressFraction(t: CleanupTotals): number {
  if (t.outcome === "done") return 1;
  if (!t.total) return 0;
  return Math.min(1, t.deleted / t.total);
}

/** «Eliminati 120 di 840 job · liberati 1,4 GB». */
export function progressText(t: CleanupTotals): string {
  if (t.total === null) return "Avvio della pulizia…";
  return `Eliminati ${fmtInt(t.deleted)} di ${fmtInt(t.total)} job · liberati ${fmtBytes(t.freedBytes)}`;
}

/** Riepilogo a fine pulizia, qualunque esito, con i job saltati, quelli in errore e le cartelle rimaste in parte. */
export function cleanupSummary(t: CleanupTotals): string {
  const parts: string[] = [];
  const freed = `liberati ${fmtBytes(t.freedBytes)}`;
  if (t.outcome === "error") {
    parts.push(`La pulizia si è interrotta per un errore: ${clause(t.error ?? "")}. Eliminati finora ${fmtInt(t.deleted)} job, ${freed}`);
  } else if (t.outcome === "done") {
    parts.push(t.deleted ? `${t.deleted === 1 ? "Eliminato" : "Eliminati"} ${fmtInt(t.deleted)} job, ${freed}` : "Nessun job eliminato");
  } else if (!t.calls) {
    parts.push("Interrotto prima di iniziare: nessun job eliminato");
  } else {
    const left = Math.max(0, (t.total ?? 0) - t.deleted);
    parts.push(`Interrotto: eliminati ${fmtInt(t.deleted)}, ne restano ${fmtInt(left)}; ripeti la pulizia per continuare`);
  }
  if (t.skipped > 0) parts.push(skippedText(t.skipped));
  if (t.errors > 0) parts.push(`${plural(t.errors, "job non eliminato", "job non eliminati")} per un errore imprevisto`);
  if (t.leftover > 0) parts.push(leftoverText(t.leftover));
  return `${parts.join(". ")}.`;
}

/**
 * Job saltati dal server: non è un errore e il motivo non si conosce job per job (in uso, ripreso, già eliminato
 * altrove o non più corrispondente ai criteri, per esempio per una stella o un'etichetta aggiunta nel frattempo).
 */
function skippedText(n: number): string {
  return n === 1
    ? "1 job saltato: in uso, ripreso, già eliminato altrove o non più corrispondente ai criteri (per esempio per una stella o un'etichetta aggiunta nel frattempo)"
    : `${fmtInt(n)} job saltati: in uso, ripresi, già eliminati altrove o non più corrispondenti ai criteri (per esempio per una stella o un'etichetta aggiunta nel frattempo)`;
}

/** Tono del riepilogo: errore, avviso (interrotto, saltati, resti: non tutto ciò che corrispondeva è stato eliminato) o tutto a posto. */
export function summaryTone(t: CleanupTotals): "ok" | "warn" | "err" {
  if (t.outcome === "error") return "err";
  return t.outcome === "done" && !t.skipped && !t.errors && !t.leftover ? "ok" : "warn";
}

function leftoverText(n: number): string {
  return n === 1
    ? "1 cartella non rimossa del tutto (file in uso?): chiudi i programmi che la usano e premi «Pulisci file temporanei» in Impostazioni"
    : `${fmtInt(n)} cartelle non rimosse del tutto (file in uso?): chiudi i programmi che le usano e premi «Pulisci file temporanei» in Impostazioni`;
}

// ---------------------------------------------------------------------------------------------- progetti

/** «1 documento», «3 sequenze». */
export function plural(n: number, one: string, many: string): string {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

const normName = (s: string) => (s ?? "").normalize("NFC").trim().replace(/\s+/g, " ").toLowerCase();

/** Il nome scritto per confermare coincide con quello del progetto (spazi e maiuscole a parte); vuoto mai. */
export function nameMatches(typed: string, name: string): boolean {
  const a = normName(typed);
  return a !== "" && a === normName(name);
}

/** Progetto con contenuto (sequenze, documenti o job): per eliminarlo se ne scrive il nome. */
export function needsTypedName(u: ProjectUsage): boolean {
  return u.sequences + u.documents + u.jobs.total > 0;
}

/** Perché il progetto non si può eliminare ora (job attivi), null se si può. */
export function deleteBlockedReason(u: ProjectUsage): string | null {
  const n = u.jobs.active;
  if (!(n > 0)) return null;
  return `Il progetto ha ${fmtInt(n)} job in coda o in esecuzione: ${n === 1 ? "annullalo" : "annullali"} dalla pagina Job prima di eliminarlo.`;
}

/** Svuotamento del progetto prima del DELETE: tutti i job terminati, senza eccezioni (l'utente elimina il progetto). */
export function drainRequest(projectId: string): CleanupRequest {
  return {
    scope: "project",
    project_id: projectId,
    statuses: [...CLEANUP_STATUSES],
    older_than_days: null,
    keep_favorites: false,
    keep_tagged: false,
    include_resumable: true,
  };
}

/**
 * Notifica dopo l'eliminazione: «Progetto «X» eliminato (12 job, 1,4 GB liberati).», con il progetto vuoto creato al
 * posto dell'ultimo. `drained` = totali dello svuotamento fatto prima del DELETE (i job eliminati lì contano).
 */
export function projectDeleteToast(name: string, r: ProjectDeleteResult, drained?: CleanupTotals | null): string {
  const jobs = count(r.jobs_deleted) + (drained?.deleted ?? 0);
  const freed = count(r.freed_bytes) + (drained?.freedBytes ?? 0);
  const leftover = count(r.leftover) + (drained?.leftover ?? 0);
  let text = `Progetto «${name}» eliminato${jobs || freed ? ` (${fmtInt(jobs)} job, ${fmtBytes(freed)} liberati)` : ""}.`;
  if (r.replacement) text += ` Ne ho creato uno vuoto, «${r.replacement.name}»: BioLoom ne richiede almeno uno.`;
  if (leftover > 0) text += ` ${leftoverText(leftover)}.`;
  return text;
}

export type ProjectFlowResult =
  | { kind: "deleted"; result: ProjectDeleteResult; totals: CleanupTotals | null }
  /**
   * svuotamento interrotto (anche durante l'ultima chiamata, finita con tutti i job eliminati), bloccato o in errore:
   * il progetto resta e il DELETE non parte
   */
  | { kind: "incomplete"; totals: CleanupTotals; message: string }
  /**
   * il server ha rifiutato il DELETE con un esito certo (409, 404…, 503 `shutting_down`: il progetto resta): messaggio
   * da mostrare nel dialogo, uso da rileggere
   */
  | { kind: "refused"; status: number; code: string; message: string; totals: CleanupTotals | null }
  /** rete o errore generico del server sul DELETE: l'esito va verificato rileggendo elenco e uso, mai dato per fallito */
  | { kind: "unknown"; message: string; totals: CleanupTotals | null };

/** Errore del DELETE con esito certo: 4xx, o 503 `shutting_down` (arresto del server: il progetto resta, il messaggio dice quanti job sono andati). */
function definiteRefusal(e: unknown): e is ApiError {
  if (!(e instanceof ApiError)) return false;
  return (e.status >= 400 && e.status < 500) || (e.status === 503 && e.code === "shutting_down");
}

/**
 * Eliminazione di un progetto dall'interfaccia: prima lo svuota con la pulizia a tempo (un progetto con migliaia di
 * job non blocca una sola richiesta per minuti, e «Interrompi» funziona), poi chiama il DELETE, che resta completo
 * e autorevole (controlla di nuovo i job attivi e elimina quelli rimasti). Senza job si chiama subito il DELETE.
 * Se l'utente ha chiesto di interrompere, il DELETE non parte mai, nemmeno quando l'ultima chiamata dello svuotamento
 * (che non si interrompe a metà) ha eliminato tutti i job: sequenze e documenti restano, come chiesto.
 */
export async function deleteProjectFlow(
  projectId: string,
  jobsTotal: number,
  deps: {
    cleanup: (req: CleanupRequest) => Promise<CleanupRun>;
    remove: (id: string) => Promise<ProjectDeleteResult>;
    shouldStop?: () => boolean;
    onProgress?: (t: CleanupTotals) => void;
  },
): Promise<ProjectFlowResult> {
  let totals: CleanupTotals | null = null;
  if (jobsTotal > 0) {
    totals = await runCleanup(drainRequest(projectId), deps.cleanup, { shouldStop: deps.shouldStop, onProgress: deps.onProgress });
    const stopped = !!deps.shouldStop?.();
    if (stopped || totals.outcome !== "done") return { kind: "incomplete", totals, message: incompleteMessage(totals, stopped) };
  }
  try {
    return { kind: "deleted", result: await deps.remove(projectId), totals };
  } catch (e) {
    if (definiteRefusal(e)) {
      const message = totals?.deleted
        ? `Il progetto è stato svuotato in parte (eliminati ${fmtInt(totals.deleted)} job), ma non eliminato: ${e.message}`
        : e.message;
      return { kind: "refused", status: e.status, code: e.code, message, totals };
    }
    return { kind: "unknown", message: `Non so se il progetto sia stato eliminato: ${clause(errorMessage(e))}.`, totals };
  }
}

/**
 * Perché il progetto resta dopo lo svuotamento. `stopped` = l'utente ha chiesto di interrompere (anche se l'ultima
 * chiamata ha finito lo svuotamento). Nello svuotamento stella ed etichette non proteggono (keep_* falsi): un job
 * saltato è in uso, ripreso o già eliminato altrove.
 */
function incompleteMessage(t: CleanupTotals, stopped = false): string {
  if (stopped && t.outcome === "done") {
    const head = t.skipped + t.errors > 0
      ? `Il progetto è stato svuotato in parte: eliminati ${fmtInt(t.deleted)} job (${fmtBytes(t.freedBytes)} liberati)`
      : t.deleted
        ? `Tutti i job del progetto sono stati eliminati (${fmtInt(t.deleted)} job, ${fmtBytes(t.freedBytes)} liberati)`
        : "Il progetto non ha più job da eliminare";
    return `${head}, ma hai interrotto prima dell'eliminazione: il progetto, le sue sequenze e i suoi documenti restano. Riprova per completare l'eliminazione.`;
  }
  const head = t.deleted
    ? `Il progetto è stato svuotato in parte: eliminati ${fmtInt(t.deleted)} job (${fmtBytes(t.freedBytes)} liberati)`
    : "Nessun job del progetto è stato eliminato";
  const blocked = [
    t.skipped > 0 ? plural(t.skipped, "job saltato (in uso, ripreso o già eliminato altrove)", "job saltati (in uso, ripresi o già eliminati altrove)") : "",
    t.errors > 0 ? `${plural(t.errors, "job non eliminato", "job non eliminati")} per un errore imprevisto` : "",
  ].filter(Boolean);
  const why = t.outcome === "error" ? `poi un errore: ${clause(t.error ?? "")}`
    : t.outcome === "stalled" ? (blocked.length ? blocked.join(" e ") : "nessun progresso nelle ultime chiamate")
    : t.outcome === "capped" ? "troppe chiamate senza finire"
    : "interrotto prima della fine";
  return `${head}; ${why}. Il progetto resta: riprova per completare l'eliminazione.`;
}

/** Messaggio senza il punto finale, da inserire in una frase. */
function clause(text: string): string {
  return text.trim().replace(/[.\s]+$/, "");
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
