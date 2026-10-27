import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import type { CleanupPreview, CleanupRequest, CleanupRun, ProjectDeleteResult, ProjectUsage } from "../api/types";
import {
  buildCleanupRequest, CALL_SECONDS, canRun, cleanupRunRequest, cleanupSummary, cleanupTarget, cleanupTargetKey, type CleanupTotals,
  criteriaKey, defaultCriteria, deleteBlockedReason, deleteProjectFlow, dialogPreview, drainRequest, emptyTotals, INVALID_RUN_MESSAGE,
  makePreviewLoop, nameMatches, needsTypedName, plural, type PreviewState, previewLine, progressFraction, progressText, projectDeleteToast,
  resetUnderstood, runCleanup, summaryTone, toggleStatus,
} from "./cleanup";
import type { Timers } from "./live";

/** Timer finti: `advance` esegue in ordine quelli scaduti (come in live.test.ts). */
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

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((ok, ko) => {
    resolve = ok;
    reject = ko;
  });
  return { promise, resolve, reject };
}

/** Lascia completare le promesse già risolte (e i loro `then`/`finally`). */
const flush = () => new Promise((r) => setTimeout(r, 0));

const GB = 1024 ** 3;
const AS_OF = "2026-10-05T10:00:00+00:00";
const preview = (matched: number, extra: Partial<CleanupPreview> = {}): CleanupPreview => ({
  matched, bytes: matched * 1024, bytes_complete: true, by_status: {}, by_kind: {}, protected: 0, resumable: 0, sample: [], as_of: AS_OF,
  ...extra,
});
const run = (o: Partial<CleanupRun>): CleanupRun => ({ deleted: 0, skipped: 0, errors: 0, freed_bytes: 0, leftover: 0, remaining: 0, ...o });
const totals = (o: Partial<CleanupTotals>): CleanupTotals => ({ ...emptyTotals(), ...o });
const reqAll = (days: number | null = 7): CleanupRequest => buildCleanupRequest({ ...defaultCriteria({ scope: "all" }), olderThanDays: days }, null)!;

/** Chiamate in sequenza: risposte (anche senza la forma attesa) o errori nell'ordine; i corpi inviati restano in `bodies`. */
function scripted(responses: unknown[], onCall?: (n: number) => void) {
  const bodies: CleanupRequest[] = [];
  const call = async (req: CleanupRequest) => {
    bodies.push(req);
    onCall?.(bodies.length);
    if (bodies.length > responses.length) throw new Error("chiamata in più");
    const r = responses[bodies.length - 1];
    if (r instanceof Error) throw r;
    return r as CleanupRun;
  };
  return { call, bodies };
}

describe("criteri e richiesta della pulizia", () => {
  it("valori predefiniti sicuri: falliti e annullati, oltre 7 giorni, stella ed etichette conservate", () => {
    const c = defaultCriteria();
    expect(c).toEqual({
      scope: "project", statuses: ["failed", "cancelled"], olderThanDays: 7, kind: null,
      keepFavorites: true, keepTagged: true, includeResumable: false, understood: false,
    });
    expect(defaultCriteria({ scope: "all", kind: "model" })).toMatchObject({ scope: "all", kind: "model" });
    expect(defaultCriteria({ kind: "" }).kind).toBeNull();
  });

  it("toggleStatus mantiene l'ordine e togliere i completati azzera «Ho capito»", () => {
    const withCompleted = toggleStatus(defaultCriteria(), "completed");
    expect(withCompleted.statuses).toEqual(["failed", "cancelled", "completed"]);
    const understood = { ...withCompleted, understood: true };
    expect(toggleStatus(understood, "failed")).toMatchObject({ statuses: ["cancelled", "completed"], understood: true });
    expect(toggleStatus(understood, "completed")).toMatchObject({ statuses: ["failed", "cancelled"], understood: false });
  });

  it("buildCleanupRequest: null senza stati o con «Questo progetto» senza progetto, mai «tutti» al suo posto", () => {
    const c = defaultCriteria();
    expect(buildCleanupRequest(c, null)).toBeNull();
    expect(buildCleanupRequest({ ...c, statuses: [] }, "p1")).toBeNull();
    expect(buildCleanupRequest({ ...c, scope: "all", statuses: [] }, null)).toBeNull();
    expect(buildCleanupRequest(c, "p1")).toEqual({
      scope: "project", project_id: "p1", statuses: ["failed", "cancelled"], older_than_days: 7,
      keep_favorites: true, keep_tagged: true, include_resumable: false,
    });
  });

  it("con «Tutti i progetti» nessun project_id; senza tipo nessun kinds", () => {
    const all = buildCleanupRequest({ ...defaultCriteria(), scope: "all" }, "p1")!;
    expect(all.scope).toBe("all");
    expect("project_id" in all).toBe(false);
    expect("kinds" in all).toBe(false);
    expect(buildCleanupRequest({ ...defaultCriteria(), kind: "model" }, "p1")!.kinds).toEqual(["model"]);
    expect(buildCleanupRequest({ ...defaultCriteria(), olderThanDays: null }, "p1")!.older_than_days).toBeNull();
  });

  it("criteriaKey: stessi criteri, stessa chiave (ordine degli stati, max_seconds e as_of non contano)", () => {
    const a = buildCleanupRequest(defaultCriteria(), "p1")!;
    const b: CleanupRequest = { ...a, statuses: ["cancelled", "failed"], max_seconds: 5, as_of: AS_OF };
    expect(criteriaKey(b)).toBe(criteriaKey(a));
    expect(JSON.parse(criteriaKey(b))).not.toHaveProperty("as_of");
    expect(criteriaKey({ ...a, project_id: "p2" })).not.toBe(criteriaKey(a));
    expect(criteriaKey({ ...a, older_than_days: 30 })).not.toBe(criteriaKey(a));
    expect(criteriaKey({ ...a, keep_tagged: false })).not.toBe(criteriaKey(a));
    expect(JSON.parse(criteriaKey(b))).not.toHaveProperty("max_seconds");
  });
});

describe("quando la pulizia si può avviare", () => {
  const req = buildCleanupRequest(defaultCriteria(), "p1")!;
  const key = criteriaKey(req);
  const ready = (k: string, matched = 3): PreviewState => ({ status: "ready", key: k, data: preview(matched) });
  const base = { request: req, key, completed: false, understood: false };

  it("anteprima pronta per i criteri attuali con almeno un job", () => {
    expect(canRun({ ...base, preview: ready(key) })).toBe(true);
  });
  it("no con anteprima superata, vuota, in corso, in errore o senza richiesta", () => {
    expect(canRun({ ...base, preview: ready(criteriaKey({ ...req, older_than_days: 30 })) })).toBe(false);
    expect(canRun({ ...base, preview: ready(key, 0) })).toBe(false);
    expect(canRun({ ...base, preview: { status: "loading", key } })).toBe(false);
    expect(canRun({ ...base, preview: { status: "waiting", key } })).toBe(false);
    expect(canRun({ ...base, preview: { status: "error", key, message: "x" } })).toBe(false);
    expect(canRun({ ...base, request: null, key: null, preview: ready(key) })).toBe(false);
    expect(canRun({ ...base, key: "altra", preview: ready("altra") })).toBe(false); // la chiave deve essere quella della richiesta
  });
  it("con i completati serve «Ho capito»", () => {
    expect(canRun({ ...base, completed: true, preview: ready(key) })).toBe(false);
    expect(canRun({ ...base, completed: true, understood: true, preview: ready(key) })).toBe(true);
  });
  it("dialogPreview: durante il ricalcolo resta l'ultima anteprima, attenuata, e la conferma è disattivata", () => {
    const old = ready(criteriaKey({ ...req, older_than_days: 30 }));
    const v = dialogPreview({ request: req, loop: { status: "loading", key }, lastReady: old, completed: false, understood: false });
    expect(v).toMatchObject({ key, shown: old, current: false, canRun: false });
    const now = ready(key);
    expect(dialogPreview({ request: req, loop: now, lastReady: now, completed: false, understood: false })).toMatchObject({ current: true, canRun: true });
    // un errore vecchio non resta visibile durante il ricalcolo
    const err: PreviewState = { status: "error", key: "vecchia", message: "x" };
    expect(dialogPreview({ request: req, loop: { status: "waiting", key }, lastReady: err, completed: false, understood: false }).shown.status).toBe("waiting");
  });

  it("l'esecuzione confermata porta l'as_of dell'anteprima pronta per i criteri attuali, mai di un'altra", () => {
    expect(cleanupRunRequest(req, ready(key))).toEqual({ ...req, as_of: AS_OF });
    expect(req).not.toHaveProperty("as_of"); // la richiesta del chiamante non cambia
    const later = "2026-10-05T11:30:00+00:00";
    expect(cleanupRunRequest({ ...req, as_of: later }, ready(key))!.as_of).toBe(AS_OF);
    expect(cleanupRunRequest(req, ready(criteriaKey({ ...req, older_than_days: 30 })))).toBeNull();
    expect(cleanupRunRequest(req, { status: "loading", key })).toBeNull();
    expect(cleanupRunRequest(req, { status: "error", key, message: "x" })).toBeNull();
    expect(cleanupRunRequest(null, ready(key))).toBeNull();
    // anteprima senza as_of (server vecchio): nessun limite superiore, come prima
    const old: PreviewState = { status: "ready", key, data: { ...preview(3), as_of: undefined as unknown as string } };
    expect(cleanupRunRequest(req, old)).toEqual(req);
  });
});

describe("«Questo progetto» fissato all'apertura del dialogo", () => {
  const A = { id: "pa", name: "Chinasi" };
  const B = { id: "pb", name: "Proteasi" };

  it("resta il progetto dell'apertura finché è l'attivo; il nome segue il progetto", () => {
    expect(cleanupTarget({ id: "pa", name: null }, { projectId: "pa", project: A })).toEqual({ id: "pa", name: "Chinasi", changed: false });
    expect(cleanupTarget({ id: "pa", name: "Vecchio nome" }, { projectId: "pa", project: A })).toEqual({ id: "pa", name: "Chinasi", changed: false });
  });

  it("se l'attivo cambia (rilettura dei progetti) non segue il nuovo: non disponibile, con il nome di prima", () => {
    const t = cleanupTarget({ id: "pa", name: "Chinasi" }, { projectId: "pb", project: B });
    expect(t).toEqual({ id: null, name: "Chinasi", changed: true });
    const c = { ...defaultCriteria(), understood: true };
    expect(buildCleanupRequest(c, t.id)).toBeNull(); // nessuna richiesta per l'altro progetto
    expect(cleanupTarget({ id: "pa", name: "Chinasi" }, { projectId: null, project: null })).toMatchObject({ id: null, changed: true });
    // nessun progetto all'apertura, uno attivo dopo: neanche quello
    expect(cleanupTarget({ id: null, name: null }, { projectId: "pb", project: B })).toEqual({ id: null, name: null, changed: true });
  });

  it("elenco dei progetti non ancora letto o nessun progetto: non disponibile, non cambiato", () => {
    expect(cleanupTarget({ id: "pa", name: null }, { projectId: "pa", project: null })).toEqual({ id: null, name: null, changed: false });
    expect(cleanupTarget({ id: null, name: null }, { projectId: null, project: null })).toEqual({ id: null, name: null, changed: false });
  });

  it("«Ho capito» si azzera quando cambia la destinazione della richiesta", () => {
    const c = { ...toggleStatus(defaultCriteria(), "completed"), understood: true };
    expect(cleanupTargetKey("project", "pa")).toBe("project:pa");
    expect(cleanupTargetKey("project", null)).toBe("project:");
    expect(cleanupTargetKey("all", "pa")).toBe("all");
    expect(resetUnderstood(c, "project:pa", "project:pa")).toBe(c);
    expect(resetUnderstood(c, "project:pa", "project:")).toMatchObject({ understood: false, statuses: c.statuses });
    expect(resetUnderstood(c, "project:pa", "all").understood).toBe(false);
    const off = { ...c, understood: false };
    expect(resetUnderstood(off, "project:pa", "all")).toBe(off);
  });
});

describe("anteprima con debounce e una sola richiesta in volo", () => {
  function setup() {
    const timers = new FakeTimers();
    const calls: { req: CleanupRequest; d: ReturnType<typeof deferred<CleanupPreview>> }[] = [];
    const states: PreviewState[] = [];
    const loop = makePreviewLoop({
      call: (req) => {
        const d = deferred<CleanupPreview>();
        calls.push({ req, d });
        return d.promise;
      },
      onState: (s) => states.push(s),
      timers,
      delayMs: 400,
    });
    return { timers, calls, states, loop, last: () => states[states.length - 1] };
  }

  it("tre richieste ravvicinate: una chiamata, con l'ultima", () => {
    const { timers, calls, loop, last } = setup();
    loop.request(reqAll(7));
    timers.advance(100);
    loop.request(reqAll(30));
    timers.advance(100);
    loop.request(reqAll(90));
    expect(last()).toEqual({ status: "waiting", key: criteriaKey(reqAll(90)) });
    timers.advance(399);
    expect(calls).toHaveLength(0);
    timers.advance(1);
    expect(calls.map((c) => c.req.older_than_days)).toEqual([90]);
    expect(last()).toEqual({ status: "loading", key: criteriaKey(reqAll(90)) });
  });

  it("una sola in volo: i criteri cambiati intanto partono alla fine, solo i più recenti", async () => {
    const { timers, calls, states, loop, last } = setup();
    loop.request(reqAll(7));
    timers.advance(400);
    loop.request(reqAll(30));
    timers.advance(400);
    loop.request(reqAll(90));
    timers.advance(400);
    expect(calls).toHaveLength(1); // la prima è ancora in volo
    calls[0].d.resolve(preview(5));
    await flush();
    expect(states.some((s) => s.status === "ready")).toBe(false); // risposta di criteri superati: scartata
    expect(calls.map((c) => c.req.older_than_days)).toEqual([7, 90]);
    calls[1].d.resolve(preview(3));
    await flush();
    expect(last()).toEqual({ status: "ready", key: criteriaKey(reqAll(90)), data: preview(3) });
  });

  it("una risposta superata non tocca lo stato dei criteri nuovi", async () => {
    const { timers, calls, loop, last } = setup();
    loop.request(reqAll(7));
    timers.advance(400);
    loop.request(reqAll(30));
    calls[0].d.resolve(preview(5));
    await flush();
    expect(last()).toEqual({ status: "waiting", key: criteriaKey(reqAll(30)) });
    timers.advance(400);
    expect(calls).toHaveLength(2);
  });

  it("criteri uguali non ripetono la chiamata", async () => {
    const { timers, calls, loop, last } = setup();
    loop.request(reqAll(7));
    timers.advance(400);
    calls[0].d.resolve(preview(2));
    await flush();
    loop.request({ ...reqAll(7) });
    timers.advance(1000);
    expect(calls).toHaveLength(1);
    expect(last().status).toBe("ready");
  });

  it("richiesta null: idle, nessun timer e nessuna chiamata; la risposta in volo si scarta", async () => {
    const { timers, calls, loop, last } = setup();
    loop.request(reqAll(7));
    loop.request(null);
    expect(last()).toEqual({ status: "idle" });
    expect(timers.pending).toBe(0);
    timers.advance(1000);
    expect(calls).toHaveLength(0);
    loop.request(reqAll(30));
    timers.advance(400);
    loop.request(null);
    calls[0].d.resolve(preview(4));
    await flush();
    expect(last()).toEqual({ status: "idle" });
  });

  it("errore, poi «Riprova» subito, senza debounce", async () => {
    const { timers, calls, loop, last } = setup();
    loop.request(reqAll(7));
    timers.advance(400);
    calls[0].d.reject(new ApiError("Errore del server", 500, "http_error"));
    await flush();
    expect(last()).toEqual({ status: "error", key: criteriaKey(reqAll(7)), message: "Errore del server" });
    loop.retry();
    expect(calls).toHaveLength(2);
    calls[1].d.resolve(preview(1));
    await flush();
    expect(last()).toMatchObject({ status: "ready", data: { matched: 1 } });
  });

  it("dopo stop nessuno stato in più", async () => {
    const { timers, calls, states, loop } = setup();
    loop.request(reqAll(7));
    timers.advance(400);
    const before = states.length;
    loop.stop();
    calls[0].d.resolve(preview(1));
    await flush();
    loop.request(reqAll(30));
    timers.advance(1000);
    expect(states.length).toBe(before);
    expect(calls).toHaveLength(1);
  });
});

describe("ciclo della pulizia a tempo", () => {
  const req = buildCleanupRequest(defaultCriteria(), "p1")!;

  it("somma i totali, tiene saltati ed errori dell'ultima chiamata, invia max_seconds 5 e si ferma a remaining 0", async () => {
    const { call, bodies } = scripted([
      run({ deleted: 50, freed_bytes: 1000, skipped: 2, errors: 1, leftover: 1, remaining: 100 }),
      run({ deleted: 100, freed_bytes: 3000, skipped: 1, remaining: 0 }),
    ]);
    const progress: CleanupTotals[] = [];
    const t = await runCleanup(req, call, { onProgress: (p) => progress.push(p) });
    expect(t).toEqual({
      deleted: 150, freedBytes: 4000, leftover: 1, skipped: 1, errors: 0, remaining: 0, total: 151, calls: 2, outcome: "done", error: null,
    });
    expect(bodies).toHaveLength(2);
    expect(bodies.every((b) => b.max_seconds === CALL_SECONDS && CALL_SECONDS === 5)).toBe(true);
    expect(bodies[0]).toEqual({ ...req, max_seconds: 5 });
    expect(req).not.toHaveProperty("max_seconds"); // la richiesta del chiamante non cambia
    expect(progress.map((p) => [p.deleted, p.total])).toEqual([[50, 153], [150, 151]]);
  });

  it("«Interrompi» durante una chiamata: la chiamata finisce e il ciclo si ferma prima della successiva", async () => {
    let stop = false;
    const { call, bodies } = scripted([run({ deleted: 10, remaining: 100 }), run({ deleted: 10, remaining: 90 })], () => {
      stop = true;
    });
    const t = await runCleanup(req, call, { shouldStop: () => stop });
    expect(bodies).toHaveLength(1);
    expect(t).toMatchObject({ outcome: "stopped", deleted: 10, remaining: 100, calls: 1 });
  });

  it("due risposte di fila senza eliminazioni: stalled; remaining 0 ha la precedenza", async () => {
    const stalled = await runCleanup(req, scripted([run({ skipped: 3, remaining: 10 }), run({ skipped: 3, remaining: 10 })]).call);
    expect(stalled).toMatchObject({ outcome: "stalled", calls: 2, skipped: 3 });
    const reset = scripted([run({ remaining: 5 }), run({ deleted: 1, remaining: 4 }), run({ remaining: 4 }), run({ remaining: 4 })]);
    expect(await runCleanup(req, reset.call)).toMatchObject({ outcome: "stalled", calls: 4, deleted: 1 });
    expect(await runCleanup(req, scripted([run({ skipped: 2, remaining: 0 })]).call)).toMatchObject({ outcome: "done", calls: 1 });
  });

  it("un errore non si rilancia: totali parziali con il messaggio", async () => {
    const { call } = scripted([
      run({ deleted: 10, freed_bytes: 100, remaining: 50 }),
      new ApiError("Server MakPhi non raggiungibile. È ancora in esecuzione?", 0, "network"),
    ]);
    const t = await runCleanup(req, call);
    expect(t).toMatchObject({ outcome: "error", deleted: 10, freedBytes: 100, calls: 1, error: "Server MakPhi non raggiungibile. È ancora in esecuzione?" });
  });

  it("risposta senza la forma attesa: esito error con i totali fin lì, mai un'eccezione né un «finito» inventato", async () => {
    for (const bad of [null, "Internal Server Error", "", 42, [], {}, { ...run({ remaining: 5 }), deleted: "3" }, { ...run({}), remaining: -1 },
      { ...run({}), freed_bytes: Number.NaN }, { ...run({}), leftover: null }]) {
      const { call, bodies } = scripted([run({ deleted: 10, freed_bytes: 100, remaining: 50 }), bad, run({ remaining: 0 })]);
      const t = await runCleanup(req, call);
      expect(t).toMatchObject({ outcome: "error", error: INVALID_RUN_MESSAGE, deleted: 10, freedBytes: 100, remaining: 50, calls: 1 });
      expect(bodies).toHaveLength(2); // nessuna chiamata dopo la risposta non valida
    }
    // il testo da solo (Number("…") → 0) non vale come remaining 0 = finito
    expect(await runCleanup(req, scripted(["ok"]).call)).toMatchObject({ outcome: "error", calls: 0 });
    // leftover booleano accettato
    expect(await runCleanup(req, scripted([{ ...run({ deleted: 1 }), leftover: true }]).call)).toMatchObject({ outcome: "done", leftover: 1 });
  });

  it("un'eccezione di una callback non si rilancia: esito error", async () => {
    const t = await runCleanup(req, scripted([run({ deleted: 2, remaining: 5 }), run({ deleted: 1, remaining: 0 })]).call, {
      onProgress: () => {
        throw new Error("render fallito");
      },
    });
    expect(t).toMatchObject({ outcome: "error", error: "render fallito", deleted: 2, calls: 1 });
    const stop = await runCleanup(req, scripted([]).call, {
      shouldStop: () => {
        throw new Error("x");
      },
    });
    expect(stop).toMatchObject({ outcome: "error", error: "x", calls: 0 });
  });

  it("as_of dell'anteprima in ogni chiamata dell'esecuzione", async () => {
    const { call, bodies } = scripted([run({ deleted: 1, remaining: 1 }), run({ deleted: 1, remaining: 0 })]);
    await runCleanup({ ...req, as_of: AS_OF }, call);
    expect(bodies.map((b) => b.as_of)).toEqual([AS_OF, AS_OF]);
  });

  it("tetto di chiamate: capped", async () => {
    const call = async () => run({ deleted: 1, remaining: 1000 });
    expect(await runCleanup(req, call, { maxCalls: 3 })).toMatchObject({ outcome: "capped", calls: 3, deleted: 3 });
  });

  it("avanzamento e riepiloghi per ogni esito", () => {
    expect(progressText(emptyTotals())).toBe("Avvio della pulizia…");
    const partial = totals({ deleted: 120, total: 840, remaining: 720, freedBytes: Math.round(1.4 * GB), calls: 3 });
    expect(progressText(partial)).toBe("Eliminati 120 di 840 job · liberati 1,4 GB");
    expect(progressFraction(partial)).toBeCloseTo(120 / 840);
    expect(progressFraction(emptyTotals())).toBe(0);
    expect(progressFraction(totals({ deleted: 5, skipped: 1, total: 6, outcome: "done" }))).toBe(1);

    expect(cleanupSummary(totals({ deleted: 840, freedBytes: Math.round(3.2 * GB), total: 840, remaining: 0, calls: 4, outcome: "done" })))
      .toBe("Eliminati 840 job, liberati 3,2 GB.");
    expect(cleanupSummary(totals({ deleted: 1, freedBytes: 12 * 1024, calls: 1, outcome: "done" }))).toBe("Eliminato 1 job, liberati 12 KB.");
    expect(cleanupSummary(totals({ calls: 1, outcome: "done" }))).toBe("Nessun job eliminato.");
    expect(cleanupSummary({ ...partial, outcome: "stopped" })).toBe("Interrotto: eliminati 120, ne restano 720; ripeti la pulizia per continuare.");
    expect(cleanupSummary(totals({ outcome: "stopped" }))).toBe("Interrotto prima di iniziare: nessun job eliminato.");
    // saltati: in uso, ripresi, già eliminati altrove o non più corrispondenti (stella o etichetta aggiunta nel frattempo)
    expect(cleanupSummary(totals({ deleted: 5, skipped: 3, calls: 2, outcome: "done" }))).toBe(
      "Eliminati 5 job, liberati 0 B. 3 job saltati: in uso, ripresi, già eliminati altrove o non più corrispondenti ai criteri "
      + "(per esempio per una stella o un'etichetta aggiunta nel frattempo).",
    );
    expect(cleanupSummary(totals({ deleted: 5, skipped: 1, calls: 2, outcome: "done" }))).toContain(
      "1 job saltato: in uso, ripreso, già eliminato altrove o non più corrispondente ai criteri (per esempio per una stella o un'etichetta",
    );
    expect(cleanupSummary(totals({ deleted: 5, skipped: 3, calls: 2, outcome: "done" }))).not.toContain("non eliminabil");
    expect(cleanupSummary(totals({ deleted: 5, errors: 2, calls: 2, outcome: "done" }))).toContain("2 job non eliminati per un errore imprevisto");
    expect(cleanupSummary(totals({ deleted: 5, leftover: 2, calls: 2, outcome: "done" }))).toContain(
      "2 cartelle non rimosse del tutto (file in uso?): chiudi i programmi che le usano e premi «Pulisci file temporanei» in Impostazioni",
    );
    expect(cleanupSummary(totals({ deleted: 5, leftover: 1, calls: 2, outcome: "done" }))).toContain("1 cartella non rimossa del tutto");
    const failed = cleanupSummary(totals({ deleted: 120, freedBytes: 2048, calls: 3, outcome: "error", error: "Progetto non trovato." }));
    expect(failed).toBe("La pulizia si è interrotta per un errore: Progetto non trovato. Eliminati finora 120 job, liberati 2 KB.");
    expect(cleanupSummary(totals({ deleted: 9, total: 12, skipped: 3, calls: 2, outcome: "stalled" }))).toBe(
      "Interrotto: eliminati 9, ne restano 3; ripeti la pulizia per continuare. 3 job saltati: in uso, ripresi, già eliminati altrove "
      + "o non più corrispondenti ai criteri (per esempio per una stella o un'etichetta aggiunta nel frattempo).",
    );
    expect(cleanupSummary(totals({ deleted: 1, freedBytes: 1024, calls: 2, outcome: "error", error: INVALID_RUN_MESSAGE }))).toBe(
      "La pulizia si è interrotta per un errore: Risposta del server non valida per la pulizia. Eliminati finora 1 job, liberati 1 KB.",
    );
  });

  it("tono del riepilogo", () => {
    expect(summaryTone(totals({ outcome: "done", deleted: 3 }))).toBe("ok");
    expect(summaryTone(totals({ outcome: "done", deleted: 3, leftover: 1 }))).toBe("warn");
    expect(summaryTone(totals({ outcome: "stopped" }))).toBe("warn");
    expect(summaryTone(totals({ outcome: "error", error: "x" }))).toBe("err");
  });

  it("riga dell'anteprima", () => {
    expect(previewLine(preview(42, { bytes: Math.round(1.4 * GB) }))).toBe("42 job, 1,4 GB");
    expect(previewLine(preview(42, { bytes: Math.round(1.4 * GB), bytes_complete: false }))).toBe("42 job, almeno 1,4 GB");
    expect(previewLine(preview(0))).toBe("Nessun job corrisponde");
  });
});

describe("eliminazione dei progetti", () => {
  const usage = (o: Partial<ProjectUsage> = {}, jobs: Partial<ProjectUsage["jobs"]> = {}): ProjectUsage => ({
    id: "p1", name: "Chinasi", created_at: "2026-01-01T00:00:00+00:00", updated_at: "2026-01-02T00:00:00+00:00", sequences: 0, documents: 0,
    jobs: { total: 0, active: 0, starred: 0, tagged: 0, resumable: 0, by_status: {}, ...jobs }, bytes: 0, bytes_complete: true, ...o,
  });
  const result = (o: Partial<ProjectDeleteResult> = {}): ProjectDeleteResult => ({
    deleted: "p1", jobs_deleted: 0, freed_bytes: 0, leftover: 0, replacement: null, ...o,
  });

  it("plural", () => {
    expect(plural(1, "documento", "documenti")).toBe("1 documento");
    expect(plural(3, "sequenza", "sequenze")).toBe("3 sequenze");
    expect(plural(0, "job", "job")).toBe("0 job");
  });

  it("nameMatches: spazi, maiuscole e forme Unicode non contano; vuoto mai", () => {
    expect(nameMatches("  chinasi   DI lievito ", "Chinasi di lievito")).toBe(true);
    expect(nameMatches("Caffè", "Caffè")).toBe(true); // NFD contro NFC
    expect(nameMatches("Chinasi", "Chinasi di lievito")).toBe(false);
    expect(nameMatches("", "")).toBe(false);
    expect(nameMatches("   ", "   ")).toBe(false);
  });

  it("nome da scrivere solo per un progetto con contenuto; job attivi bloccano", () => {
    expect(needsTypedName(usage())).toBe(false);
    expect(needsTypedName(usage({ sequences: 1 }))).toBe(true);
    expect(needsTypedName(usage({ documents: 1 }))).toBe(true);
    expect(needsTypedName(usage({}, { total: 1 }))).toBe(true);
    expect(deleteBlockedReason(usage({}, { total: 5 }))).toBeNull();
    expect(deleteBlockedReason(usage({}, { total: 5, active: 2 })))
      .toBe("Il progetto ha 2 job in coda o in esecuzione: annullali dalla pagina Job prima di eliminarlo.");
    expect(deleteBlockedReason(usage({}, { total: 5, active: 1 })))
      .toBe("Il progetto ha 1 job in coda o in esecuzione: annullalo dalla pagina Job prima di eliminarlo.");
  });

  it("drainRequest: tutto il progetto, senza eccezioni per stelle, etichette o pipeline riprendibili", () => {
    expect(drainRequest("p1")).toEqual({
      scope: "project", project_id: "p1", statuses: ["failed", "cancelled", "completed"], older_than_days: null,
      keep_favorites: false, keep_tagged: false, include_resumable: true,
    });
  });

  it("notifica di eliminazione, con e senza progetto sostitutivo", () => {
    expect(projectDeleteToast("Chinasi", result({ jobs_deleted: 12, freed_bytes: Math.round(1.4 * GB) })))
      .toBe("Progetto «Chinasi» eliminato (12 job, 1,4 GB liberati).");
    const replacement = { id: "p2", name: "Nuovo progetto", description: "", created_at: "", updated_at: "" };
    expect(projectDeleteToast("Chinasi", result({ jobs_deleted: 12, freed_bytes: Math.round(1.4 * GB), replacement }))).toBe(
      "Progetto «Chinasi» eliminato (12 job, 1,4 GB liberati). Ne ho creato uno vuoto, «Nuovo progetto»: MakPhi ne richiede almeno uno.",
    );
    expect(projectDeleteToast("Vuoto", result())).toBe("Progetto «Vuoto» eliminato.");
    // i job eliminati dallo svuotamento contano
    expect(projectDeleteToast("Chinasi", result({ jobs_deleted: 2, freed_bytes: 1024 }), totals({ deleted: 10, freedBytes: 1024, outcome: "done" })))
      .toBe("Progetto «Chinasi» eliminato (12 job, 2 KB liberati).");
    expect(projectDeleteToast("Chinasi", result({ jobs_deleted: 1, freed_bytes: 1024, leftover: 1 }))).toContain("1 cartella non rimossa del tutto");
  });

  function deps(cleanup: unknown[], remove: () => Promise<ProjectDeleteResult>) {
    const log: string[] = [];
    const s = scripted(cleanup, () => log.push("cleanup"));
    return {
      log,
      bodies: s.bodies,
      deps: {
        cleanup: s.call,
        remove: (id: string) => {
          log.push(`remove ${id}`);
          return remove();
        },
      },
    };
  }

  it("senza job: solo il DELETE", async () => {
    const d = deps([], async () => result());
    const r = await deleteProjectFlow("p1", 0, d.deps);
    expect(r).toEqual({ kind: "deleted", result: result(), totals: null });
    expect(d.log).toEqual(["remove p1"]);
  });

  it("con job: prima lo svuotamento a tempo, poi il DELETE", async () => {
    const d = deps([run({ deleted: 200, freed_bytes: 2000, remaining: 10 }), run({ deleted: 10, freed_bytes: 100, remaining: 0 })], async () => result());
    const progress: CleanupTotals[] = [];
    const r = await deleteProjectFlow("p1", 210, { ...d.deps, onProgress: (t) => progress.push(t) });
    expect(d.log).toEqual(["cleanup", "cleanup", "remove p1"]);
    expect(d.bodies[0]).toEqual({ ...drainRequest("p1"), max_seconds: 5 });
    expect(r.kind).toBe("deleted");
    if (r.kind === "deleted") expect(r.totals).toMatchObject({ deleted: 210, freedBytes: 2100, outcome: "done" });
    expect(progress).toHaveLength(2);
  });

  it("svuotamento bloccato o interrotto: incomplete, il DELETE non parte", async () => {
    const stalled = deps([run({ deleted: 5, skipped: 2, remaining: 3 }), run({ skipped: 2, remaining: 3 }), run({ skipped: 2, remaining: 3 })], async () => result());
    const r = await deleteProjectFlow("p1", 10, stalled.deps);
    expect(r.kind).toBe("incomplete");
    expect(stalled.log).not.toContain("remove p1");
    if (r.kind === "incomplete") {
      expect(r.totals.outcome).toBe("stalled");
      expect(r.message).toBe(
        "Il progetto è stato svuotato in parte: eliminati 5 job (0 B liberati); 2 job saltati (in uso, ripresi o già eliminati altrove). "
        + "Il progetto resta: riprova per completare l'eliminazione.",
      );
    }
    const one = deps([run({ skipped: 1, errors: 1, remaining: 3 }), run({ skipped: 1, errors: 1, remaining: 3 })], async () => result());
    const o = await deleteProjectFlow("p1", 5, one.deps);
    expect(o.kind === "incomplete" && o.message).toBe(
      "Nessun job del progetto è stato eliminato; 1 job saltato (in uso, ripreso o già eliminato altrove) e 1 job non eliminato per un "
      + "errore imprevisto. Il progetto resta: riprova per completare l'eliminazione.",
    );
    const stopped = deps([run({ deleted: 5, remaining: 3 })], async () => result());
    const s = await deleteProjectFlow("p1", 8, { ...stopped.deps, shouldStop: () => stopped.log.length > 0 });
    expect(s).toMatchObject({ kind: "incomplete", totals: { outcome: "stopped", deleted: 5 } });
    expect(stopped.log).toEqual(["cleanup"]);
  });

  it("«Interrompi» durante l'ultima chiamata dello svuotamento: tutti i job eliminati, ma il DELETE non parte", async () => {
    let stop = false;
    const log: string[] = [];
    const s = scripted([run({ deleted: 5, freed_bytes: 2048, remaining: 3 }), run({ deleted: 3, freed_bytes: 1024, remaining: 0 })], (n) => {
      log.push("cleanup");
      if (n === 2) stop = true; // premuto mentre l'ultima chiamata è in volo: la chiamata finisce con remaining 0
    });
    const r = await deleteProjectFlow("p1", 8, {
      cleanup: s.call,
      remove: async (id) => {
        log.push(`remove ${id}`);
        return result();
      },
      shouldStop: () => stop,
    });
    expect(log).toEqual(["cleanup", "cleanup"]);
    expect(r).toMatchObject({ kind: "incomplete", totals: { outcome: "done", deleted: 8, remaining: 0 } });
    if (r.kind === "incomplete") {
      expect(r.message).toBe(
        "Tutti i job del progetto sono stati eliminati (8 job, 3 KB liberati), ma hai interrotto prima dell'eliminazione: il progetto, "
        + "le sue sequenze e i suoi documenti restano. Riprova per completare l'eliminazione.",
      );
    }
    // con job saltati non dice «tutti»
    let stop2 = false;
    const partial = scripted([run({ deleted: 4, skipped: 1, remaining: 0 })], () => {
      stop2 = true;
    });
    const p = await deleteProjectFlow("p1", 5, { cleanup: partial.call, remove: async () => result(), shouldStop: () => stop2 });
    expect(p.kind === "incomplete" && p.message).toMatch(/^Il progetto è stato svuotato in parte: eliminati 4 job \(0 B liberati\), ma hai interrotto/);
  });

  it("risposta non valida durante lo svuotamento: incomplete con l'errore, il DELETE non parte", async () => {
    const d = deps([run({ deleted: 5, remaining: 3 }), "<html>proxy</html>"], async () => result());
    const r = await deleteProjectFlow("p1", 8, d.deps);
    expect(d.log).toEqual(["cleanup", "cleanup"]);
    expect(r).toMatchObject({ kind: "incomplete", totals: { outcome: "error", deleted: 5 } });
    if (r.kind === "incomplete") expect(r.message).toContain("poi un errore: Risposta del server non valida per la pulizia.");
  });

  it("409 sul DELETE: refused con codice e messaggio del server", async () => {
    const message = "Il progetto ha 1 job in coda o in esecuzione: annullali prima di eliminarlo.";
    const d = deps([], async () => {
      throw new ApiError(message, 409, "project_active_jobs", { active: 1 });
    });
    expect(await deleteProjectFlow("p1", 0, d.deps)).toEqual({ kind: "refused", status: 409, code: "project_active_jobs", message, totals: null });
    // dopo uno svuotamento il messaggio lo dice
    const drained = deps([run({ deleted: 5, remaining: 0 })], async () => {
      throw new ApiError("Eliminati 0 job, ma un job è in uso: riprova tra poco.", 409, "project_busy", { jobs_deleted: 0 });
    });
    const r = await deleteProjectFlow("p1", 5, drained.deps);
    expect(r).toMatchObject({ kind: "refused", code: "project_busy" });
    if (r.kind === "refused") {
      expect(r.message).toBe("Il progetto è stato svuotato in parte (eliminati 5 job), ma non eliminato: Eliminati 0 job, ma un job è in uso: riprova tra poco.");
    }
  });

  it("503 shutting_down sul DELETE: esito certo (il progetto resta), refused con il messaggio del server", async () => {
    const message = "Arresto del server in corso: eliminati 3 job, il progetto resta. Riprova dopo il riavvio.";
    const shutdown = () => {
      throw new ApiError(message, 503, "shutting_down");
    };
    const d = deps([], async () => shutdown());
    expect(await deleteProjectFlow("p1", 0, d.deps)).toEqual({ kind: "refused", status: 503, code: "shutting_down", message, totals: null });
    // dopo uno svuotamento il messaggio lo premette
    const drained = deps([run({ deleted: 7, remaining: 0 })], async () => shutdown());
    const r = await deleteProjectFlow("p1", 7, drained.deps);
    expect(r).toMatchObject({ kind: "refused", status: 503, code: "shutting_down" });
    if (r.kind === "refused") expect(r.message).toBe(`Il progetto è stato svuotato in parte (eliminati 7 job), ma non eliminato: ${message}`);
  });

  it("rete (status 0) o errore generico del server (5xx) sul DELETE: esito sconosciuto, da verificare", async () => {
    for (const err of [new ApiError("Server MakPhi non raggiungibile. È ancora in esecuzione?", 0, "network"), new ApiError("Errore 500", 500, "http_error"),
      new ApiError("Errore 503", 503, "http_error"), new TypeError("Failed to fetch")]) {
      const d = deps([], async () => {
        throw err;
      });
      const r = await deleteProjectFlow("p1", 0, d.deps);
      expect(r.kind).toBe("unknown");
      if (r.kind === "unknown") expect(r.message).toMatch(/^Non so se il progetto sia stato eliminato: /);
    }
  });
});
