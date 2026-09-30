import { describe, expect, it } from "vitest";
import type { JobSummary } from "../api/types";
import {
  backoffDelay, CONNECT_TIMEOUT_MS, FeedHub, type FeedState, isJobSummary, isLive, jobPollInterval, listPollInterval,
  LiveFeed, mergeJob, parseData, polledLogChunk, RecentJobs, REOPEN_MS, sliceLogChunk, type SourceLike, throttle, type Timers,
  wantJobStream,
} from "./live";

/** Timer finti: `advance` esegue in ordine quelli scaduti. */
class FakeTimers implements Timers {
  now = 0;
  private seq = 0;
  private readonly tasks = new Map<number, { at: number; fn: () => void }>();
  set(fn: () => void, ms: number) {
    const id = ++this.seq;
    this.tasks.set(id, { at: this.now + ms, fn });
    return id;
  }
  clear(handle: unknown) {
    this.tasks.delete(handle as number);
  }
  advance(ms: number) {
    const end = this.now + ms;
    for (;;) {
      const due = [...this.tasks.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) break;
      this.tasks.delete(due[0]);
      this.now = due[1].at;
      due[1].fn();
    }
    this.now = end;
  }
  get pending() {
    return this.tasks.size;
  }
}

/** EventSource finto: registra gli ascoltatori e permette di emettere eventi o errori. */
class FakeSource implements SourceLike {
  readonly listeners = new Map<string, ((e: MessageEvent) => void)[]>();
  onerror: ((e: Event) => void) | null = null;
  closed = false;
  constructor(readonly url: string) {}
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown = {}) {
    for (const fn of this.listeners.get(type) ?? []) fn({ data: JSON.stringify(data) } as MessageEvent);
  }
  error() {
    this.onerror?.({} as Event);
  }
}

function setup(opts: { final?: string[]; url?: () => string } = {}) {
  const timers = new FakeTimers();
  const sources: FakeSource[] = [];
  const states: FeedState[] = [];
  const received: [string, unknown][] = [];
  const feed = new LiveFeed({
    url: opts.url ?? (() => "/api/jobs/x/events?offset=0"),
    events: ["status", "log"],
    final: opts.final,
    onEvent: (type, data) => received.push([type, data]),
    onState: (s) => states.push(s),
    source: (url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    },
    timers,
  });
  return { feed, timers, sources, states, received, last: () => sources[sources.length - 1] };
}

describe("scelta fra flusso e polling", () => {
  it("backoff esponenziale fino a 30 s", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 50].map((n) => backoffDelay(n))).toEqual([0, 1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });
  it("il polling si sospende solo mentre il flusso serve gli aggiornamenti", () => {
    expect(isLive("connecting")).toBe(true);
    expect(isLive("open")).toBe(true);
    expect(isLive("retry")).toBe(false);
    expect(isLive("off")).toBe(false);
    const job = { hasJob: true, active: true, watch: false };
    expect(jobPollInterval({ ...job, live: true })).toBeNull();
    expect(jobPollInterval({ ...job, live: false })).toBe(900); // flusso caduto: si torna al polling
    expect(jobPollInterval({ hasJob: true, active: false, watch: true, live: false })).toBe(5000); // riprendibile
    expect(jobPollInterval({ hasJob: true, active: false, watch: false, live: false })).toBeNull();
    expect(jobPollInterval({ hasJob: false, active: true, watch: false, live: false })).toBeNull();
    expect(listPollInterval(true, true, 2000, 8000)).toBeNull();
    expect(listPollInterval(false, true, 2000, 8000)).toBe(2000);
    expect(listPollInterval(false, false, 3000, null)).toBeNull();
  });
  it("il flusso di un job si apre solo per un job attivo, visibile e già caricato", () => {
    const ok = { hasJob: true, active: true, visible: true, loaded: true, supported: true };
    expect(wantJobStream(ok)).toBe(true);
    expect(wantJobStream({ ...ok, active: false })).toBe(false); // terminato: niente flusso (la ripresa lo riapre)
    expect(wantJobStream({ ...ok, visible: false })).toBe(false); // scheda nascosta
    expect(wantJobStream({ ...ok, loaded: false })).toBe(false); // prima serve l'offset del log
    expect(wantJobStream({ ...ok, supported: false })).toBe(false); // senza EventSource
  });
});

describe("sliceLogChunk", () => {
  it("accoda i blocchi contigui e ignora quelli già letti", () => {
    expect(sliceLogChunk(0, { start: 0, offset: 6, text: "riga\n\n" })).toEqual({ text: "riga\n\n", offset: 6 });
    expect(sliceLogChunk(10, { start: 4, offset: 10, text: "vecchi" })).toBeNull();
    expect(sliceLogChunk(10, { start: 10, offset: 10, text: "" })).toBeNull();
  });
  it("taglia la parte già letta in byte, anche con caratteri multibyte", () => {
    // "è" occupa 2 byte: dal byte 3 resta "ok\nnuova\n"
    expect(sliceLogChunk(3, { start: 0, offset: 12, text: "è ok\nnuova\n" })).toEqual({ text: "ok\nnuova\n", offset: 12 });
  });
  it("con un buco non accoda nulla: il flusso riaperto dall'offset giusto lo rimanda", () => {
    expect(sliceLogChunk(5, { start: 9, offset: 20, text: "dopo il buco" })).toBeNull();
    expect(sliceLogChunk(0, null)).toBeNull();
    expect(sliceLogChunk(0, { start: "0", offset: 3, text: "x" } as never)).toBeNull();
  });
});

describe("eventi e elenchi", () => {
  const job = (id: string, over: Partial<JobSummary> = {}) =>
    ({ id, kind: "pipeline", title: id, project_id: "p", status: "running", progress: 0.1, stage: "", error: null,
      created_at: "", started_at: null, finished_at: null, updated_at: "", ...over }) as JobSummary;
  it("legge i dati JSON e riconosce i riepiloghi dei job", () => {
    expect(parseData('{"a":1}')).toEqual({ a: 1 });
    expect(parseData("non json")).toBeNull();
    expect(parseData(undefined)).toBeNull();
    expect(isJobSummary(job("a"))).toBe(true);
    expect(isJobSummary({ id: "a" })).toBe(false);
    expect(isJobSummary(null)).toBe(false);
  });
  it("aggiorna sul posto un job in elenco e chiede di ricaricare per un job nuovo del filtro", () => {
    const list = [job("a"), job("b")];
    const all = () => true;
    const updated = mergeJob(list, job("b", { progress: 0.5, eta_seconds: 120 }), all);
    expect(updated.reload).toBe(false);
    expect(updated.list![1]).toMatchObject({ id: "b", progress: 0.5, eta_seconds: 120 });
    expect(updated.list![0]).toBe(list[0]);
    expect(list[1].progress).toBe(0.1); // l'elenco di partenza non cambia
    expect(mergeJob(list, job("c"), all)).toEqual({ list, reload: true });
    expect(mergeJob(list, job("c", { project_id: "altro" }), (j) => j.project_id === "p")).toEqual({ list, reload: false });
    expect(mergeJob(null, job("c"), all)).toEqual({ list: null, reload: true });
  });
  it("una ricarica lenta non riporta indietro un job già aggiornato dal flusso", () => {
    const recent = new RecentJobs();
    // la ricarica legge "b" in esecuzione; intanto il flusso consegna la fine di "b"
    const stale = [job("a", { updated_at: "2026-09-30T10:00:00+00:00" }), job("b", { updated_at: "2026-09-30T10:00:01+00:00" })];
    recent.remember(job("b", { status: "completed", progress: 1, updated_at: "2026-09-30T10:00:03+00:00" }));
    const fixed = recent.apply(stale);
    expect(fixed).not.toBe(stale);
    expect(fixed[1]).toMatchObject({ id: "b", status: "completed", progress: 1 });
    expect(fixed[0]).toBe(stale[0]);
    // una ricarica successiva, già aggiornata, resta com'è e il riepilogo superato si dimentica
    const fresh = [stale[0], job("b", { status: "completed", progress: 1, updated_at: "2026-09-30T10:00:05+00:00" })];
    expect(recent.apply(fresh)).toBe(fresh);
    expect(recent.size).toBe(0);
  });
  it("a parità di updated_at vale il flusso; niente copie inutili; i job usciti dall'elenco si dimenticano", () => {
    const recent = new RecentJobs();
    const t = "2026-09-30T10:00:03+00:00";
    recent.remember(job("b", { status: "completed", updated_at: t }));
    const list = [job("b", { status: "running", updated_at: t })];
    expect(recent.apply(list)[0].status).toBe("completed");
    const same = [job("b", { status: "completed", updated_at: t })];
    expect(recent.apply(same)).toBe(same); // già uguale: stesso elenco
    recent.remember(job("z", { updated_at: t }));
    recent.apply([job("a")]);
    expect(recent.size).toBe(0);
  });
});

describe("log dal polling", () => {
  it("accoda solo se l'offset non è cambiato durante la richiesta", () => {
    const chunk = { start: 10, offset: 20, text: "riga nuova\n" };
    expect(polledLogChunk(10, 10, chunk)).toEqual({ text: "riga nuova\n", offset: 20 });
    // il flusso riaperto ha già accodato le stesse righe (offset 10 → 20): la risposta del polling si scarta
    expect(polledLogChunk(10, 20, chunk)).toBeNull();
    // log accorciato: il server riparte dalla coda e l'offset si riallinea
    expect(polledLogChunk(500, 500, { offset: 30, text: "coda\n" })).toEqual({ text: "coda\n", offset: 30 });
    expect(polledLogChunk(0, 0, null)).toBeNull();
  });
});

describe("LiveFeed", () => {
  it("apre il flusso, passa allo stato aperto al primo evento e consegna i dati", () => {
    const t = setup();
    t.feed.start();
    expect(t.states).toEqual(["connecting"]);
    expect(t.last().url).toBe("/api/jobs/x/events?offset=0");
    t.last().emit("status", { id: "x", status: "running" });
    t.last().emit("log", { start: 0, offset: 3, text: "ok\n" });
    expect(t.states).toEqual(["connecting", "open"]);
    expect(t.received).toEqual([["status", { id: "x", status: "running" }], ["log", { start: 0, offset: 3, text: "ok\n" }]]);
  });

  it("ricade sul polling se la connessione cade e riprova con backoff crescente, dall'offset aggiornato", () => {
    let offset = 0;
    const t = setup({ url: () => `/api/jobs/x/events?offset=${offset}` });
    t.feed.start();
    t.last().emit("status", {});
    offset = 120;
    t.last().error();
    expect(t.feed.state).toBe("retry");
    expect(t.sources[0].closed).toBe(true);
    t.timers.advance(999);
    expect(t.sources).toHaveLength(1);
    t.timers.advance(1);
    expect(t.sources).toHaveLength(2);
    expect(t.last().url).toBe("/api/jobs/x/events?offset=120");
    t.last().error(); // secondo errore di fila: 2 s
    t.timers.advance(1999);
    expect(t.sources).toHaveLength(2);
    t.timers.advance(1);
    expect(t.sources).toHaveLength(3);
    t.last().emit("status", {}); // riuscita: il backoff si azzera
    expect(t.feed.attempts).toBe(0);
    t.last().error();
    t.timers.advance(1000);
    expect(t.sources).toHaveLength(4);
    expect(t.states).toEqual(["connecting", "open", "retry", "connecting", "retry", "connecting", "open", "retry", "connecting"]);
  });

  it("senza un primo evento entro il timeout la connessione conta come fallita", () => {
    const t = setup();
    t.feed.start();
    t.timers.advance(CONNECT_TIMEOUT_MS);
    expect(t.feed.state).toBe("retry");
    expect(t.sources[0].closed).toBe(true);
  });

  it("dopo l'evento finale si chiude senza riconnettersi", () => {
    const t = setup({ final: ["end"] });
    t.feed.start();
    t.last().emit("status", { status: "running" });
    t.last().emit("end", { status: "completed" });
    expect(t.received.map(([type]) => type)).toEqual(["status", "end"]);
    expect(t.feed.state).toBe("off");
    expect(t.last().closed).toBe(true);
    t.last().error(); // la chiusura del server dopo "end" non è un errore
    t.timers.advance(60_000);
    expect(t.sources).toHaveLength(1);
    expect(t.timers.pending).toBe(0);
  });

  it("'reconnect' riapre subito senza passare dal polling", () => {
    const t = setup();
    t.feed.start();
    t.last().emit("status", {});
    t.last().emit("reconnect");
    expect(t.feed.state).toBe("connecting");
    expect(t.sources[0].closed).toBe(true);
    t.timers.advance(REOPEN_MS);
    expect(t.sources).toHaveLength(2);
    expect(t.states).not.toContain("retry");
    expect(t.feed.attempts).toBe(0);
  });

  it("stop chiude il flusso e annulla i tentativi in sospeso; gli eventi di una connessione chiusa si ignorano", () => {
    const t = setup();
    t.feed.start();
    const first = t.last();
    first.error();
    t.feed.stop();
    expect(t.feed.state).toBe("off");
    t.timers.advance(60_000);
    expect(t.sources).toHaveLength(1);
    first.emit("status", { id: "vecchio" });
    expect(t.received).toEqual([]);
  });

  it("senza EventSource resta spento (solo polling)", () => {
    const states: FeedState[] = [];
    const feed = new LiveFeed({ url: () => "/x", events: ["status"], onEvent: () => undefined, onState: (s) => states.push(s), source: null });
    feed.start();
    expect(feed.state).toBe("off");
    expect(states).toEqual([]);
  });
});

describe("FeedHub", () => {
  it("un solo flusso per tutti gli iscritti, chiuso quando se ne va l'ultimo", () => {
    const sources: FakeSource[] = [];
    const timers = new FakeTimers();
    const hub = new FeedHub((listener) => new LiveFeed({
      url: () => "/api/jobs/events",
      events: ["ready", "job"],
      onEvent: listener.onEvent,
      onState: listener.onState,
      source: (url) => {
        const s = new FakeSource(url);
        sources.push(s);
        return s;
      },
      timers,
    }));
    const a: string[] = [];
    const b: string[] = [];
    const statesB: FeedState[] = [];
    const leaveA = hub.subscribe({ onEvent: (type) => a.push(type) });
    const leaveB = hub.subscribe({ onEvent: (type) => b.push(type), onState: (s) => statesB.push(s) });
    expect(sources).toHaveLength(1);
    expect(statesB).toEqual(["connecting"]); // chi arriva dopo riceve subito lo stato attuale
    sources[0].emit("ready", { active: 0 });
    expect(a).toEqual(["ready"]);
    expect(b).toEqual(["ready"]);
    expect(statesB).toEqual(["connecting", "open"]);
    leaveA();
    leaveA(); // una seconda uscita non conta
    expect(sources[0].closed).toBe(false);
    leaveB();
    expect(sources[0].closed).toBe(true);
    expect(hub.state).toBe("off");
    hub.subscribe({ onEvent: () => undefined });
    expect(sources).toHaveLength(2);
    expect(hub.subscribers).toBe(1);
  });
});

describe("throttle", () => {
  it("esegue subito, poi al massimo una volta per intervallo senza perdere l'ultima richiesta", () => {
    const timers = new FakeTimers();
    let calls = 0;
    const t = throttle(() => (calls += 1), 1000, timers);
    t.call();
    t.call();
    t.call();
    expect(calls).toBe(1);
    timers.advance(1000);
    expect(calls).toBe(2); // una sola ripetizione per le richieste arrivate nel frattempo
    timers.advance(5000);
    expect(calls).toBe(2);
    t.call();
    expect(calls).toBe(3);
    t.call();
    t.cancel();
    timers.advance(5000);
    expect(calls).toBe(3);
  });
});
