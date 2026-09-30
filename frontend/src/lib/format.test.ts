import { describe, expect, it } from "vitest";
import { cachedLabel, fmtCpuSlots, fmtEta, fmtQueuePosition, queueLabel, searchCacheHitsLabel } from "./format";

describe("fmtCpuSlots", () => {
  it("slot in uso e processi in attesa", () => {
    expect(fmtCpuSlots({ total: 7, used: 2, waiting: 0 })).toBe("2 di 7 slot di calcolo in uso");
    expect(fmtCpuSlots({ total: 2, used: 2, waiting: 1 })).toBe("2 di 2 slot di calcolo in uso, 1 processo in attesa");
    expect(fmtCpuSlots({ total: 2, used: 2, waiting: 3 })).toBe("2 di 2 slot di calcolo in uso, 3 processi in attesa");
  });
  it("capacità ridotta mentre i processi girano: mai oltre il totale", () => {
    expect(fmtCpuSlots({ total: 2, used: 4, waiting: 0 })).toBe("2 di 2 slot di calcolo in uso");
  });
  it("nessuna nota con un server che non riporta il budget", () => {
    expect(fmtCpuSlots(undefined)).toBeNull();
    expect(fmtCpuSlots(null)).toBeNull();
    expect(fmtCpuSlots({ total: 0, used: 0, waiting: 0 })).toBeNull();
  });
});

describe("fmtQueuePosition", () => {
  it("posizione in coda dal server, da 1", () => {
    expect(fmtQueuePosition(1)).toBe("1° in coda");
    expect(fmtQueuePosition(2)).toBe("2° in coda");
    expect(fmtQueuePosition(12)).toBe("12° in coda");
  });
  it("nessuna nota senza una posizione valida", () => {
    expect(fmtQueuePosition(null)).toBeNull();
    expect(fmtQueuePosition(undefined)).toBeNull();
    expect(fmtQueuePosition(0)).toBeNull();
    expect(fmtQueuePosition(-1)).toBeNull();
    expect(fmtQueuePosition(1.5)).toBeNull();
    expect(fmtQueuePosition(Number.NaN)).toBeNull();
  });
  it("solo per i job ancora in coda", () => {
    expect(queueLabel({ status: "queued", queue_position: 3 })).toBe("3° in coda");
    expect(queueLabel({ status: "queued", queue_position: null })).toBeNull(); // ripresa in preparazione
    expect(queueLabel({ status: "queued" })).toBeNull(); // server precedente, senza il campo
    expect(queueLabel({ status: "running", queue_position: 1 })).toBeNull(); // appena partito: riepilogo vecchio
    expect(queueLabel(null)).toBeNull();
  });
});

describe("fmtEta", () => {
  it("nessuna nota senza stima", () => {
    expect(fmtEta(null)).toBeNull();
    expect(fmtEta(undefined)).toBeNull();
    expect(fmtEta(Number.NaN)).toBeNull();
    expect(fmtEta(-5)).toBeNull();
  });
  it("sotto il minuto, a minuti e oltre l'ora", () => {
    expect(fmtEta(0)).toBe("meno di un minuto rimanente");
    expect(fmtEta(55)).toBe("meno di un minuto rimanente");
    expect(fmtEta(60)).toBe("circa 1 min rimanente");
    expect(fmtEta(90)).toBe("circa 2 min rimanenti");
    expect(fmtEta(180)).toBe("circa 3 min rimanenti");
    expect(fmtEta(3540)).toBe("circa 59 min rimanenti");
    expect(fmtEta(3600)).toBe("circa 1 h rimanente");
    expect(fmtEta(4800)).toBe("circa 1 h 20 min rimanenti");
    expect(fmtEta(3 * 3600 + 100)).toBe("circa 3 h rimanenti"); // passi di 5 minuti oltre l'ora
  });
  it("una stima di quasi un'ora non diventa '60 min'", () => {
    expect(fmtEta(3580)).toBe("circa 1 h rimanente");
  });
});

describe("searchCacheHitsLabel", () => {
  it("nessuna nota senza ricerche riusate", () => {
    expect(searchCacheHitsLabel(0)).toBeNull();
    expect(searchCacheHitsLabel(undefined)).toBeNull();
    expect(searchCacheHitsLabel(null)).toBeNull();
  });
  it("singolare e plurale", () => {
    expect(searchCacheHitsLabel(1)).toBe("1 ricerca di template dalla cache");
    expect(searchCacheHitsLabel(2)).toBe("2 ricerche di template dalla cache");
  });
});

describe("cachedLabel", () => {
  it("nessuna nota per i risultati calcolati ora o dei job precedenti alla cache", () => {
    expect(cachedLabel(null)).toBeNull();
    expect(cachedLabel(undefined)).toBeNull();
    expect(cachedLabel({})).toBeNull();
    expect(cachedLabel({ cached: false, cached_at: "2026-09-30T12:00:00+00:00" })).toBeNull();
  });
  it("indica la data del calcolo riusato", () => {
    const label = cachedLabel({ cached: true, cached_at: "2026-09-28T12:30:00+00:00" });
    expect(label).toMatch(/^dalla cache \(.+\)$/);
    expect(label).toContain("28");
  });
  it("senza data valida resta una nota generica", () => {
    expect(cachedLabel({ cached: true })).toBe("dalla cache");
    expect(cachedLabel({ cached: true, cached_at: "non una data" })).toBe("dalla cache");
  });
});
