import { describe, expect, it } from "vitest";
import { cachedLabel, searchCacheHitsLabel } from "./format";

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
