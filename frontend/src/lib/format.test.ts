import { describe, expect, it } from "vitest";
import { cachedLabel, fmtEta, searchCacheHitsLabel } from "./format";

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
