import { describe, expect, it } from "vitest";
import type { StructuralPair } from "../api/types";
import {
  distanceReading, fmtUnit, gdtTone, identityTone, lddtReading, lddtTone, METRIC_GUIDE, numberingText, pairIdentity, pairResidue, pairSpan, percentOf,
  residueChange, residueClass, residueText, rmsdTone, segmentDetail, tmReading, tmTone,
} from "./compareMetrics";
import { tmVerdict } from "./compare";

const pair = (i: number, extra: Partial<StructuralPair> = {}): StructuralPair => ({
  chain_a: "A", resnum_a: String(i + 1), aa_a: "K", chain_b: "B", resnum_b: String(i + 3), aa_b: "K", distance: 1, lddt: 0.9, ...extra,
});

describe("toni delle statistiche", () => {
  it("TM-score come la lettura di tmVerdict", () => {
    expect(tmTone(0.51)).toBe("ok");
    expect(tmVerdict(0.5)).toContain("debole");
    expect(tmTone(0.5)).toBe("warn");
    expect(tmTone(0.17)).toBe("warn");
    expect(tmTone(0.16)).toBe("err");
    expect(tmTone(null)).toBeUndefined();
    expect(tmTone(undefined)).toBeUndefined();
  });
  it("RMSD, GDT, lDDT, identità", () => {
    expect([rmsdTone(1.2), rmsdTone(3), rmsdTone(6)]).toEqual(["ok", "info", "warn"]);
    expect([gdtTone(80), gdtTone(50), gdtTone(40), gdtTone(10)]).toEqual(["ok", "ok", "warn", "err"]);
    expect([lddtTone(0.92), lddtTone(0.7), lddtTone(0.6), lddtTone(0.3)]).toEqual(["ok", "ok", "warn", "err"]);
    expect([identityTone(55), identityTone(30), identityTone(27), identityTone(20)]).toEqual(["ok", "ok", "warn", "err"]);
    expect(lddtTone(Number.NaN)).toBeUndefined();
  });
  it("ogni guida ha soglie dalla migliore e una fonte", () => {
    for (const g of Object.values(METRIC_GUIDE)) {
      expect(g.thresholds.length).toBeGreaterThan(1);
      expect(g.thresholds[0].tone).toBe("ok");
      expect(g.source).not.toBe("");
      expect(g.brief).not.toBe("");
    }
  });
  it("percentuali e valori con unità", () => {
    expect(percentOf(25, 50)).toBe(50);
    expect(percentOf(3, 0)).toBeNull();
    expect(percentOf(undefined, 10)).toBeNull();
    expect(fmtUnit(2.345, 2, "Å")).toBe("2,35 Å");
    expect(fmtUnit(null, 2, "Å")).toBe("—");
  });
});

describe("lettura di una coppia", () => {
  it("distanza e lDDT per fasce", () => {
    expect(distanceReading(0.4).text).toContain("coincidenti");
    expect(distanceReading(1.5).tone).toBe("ok");
    expect(distanceReading(3).tone).toBe("warn");
    expect(distanceReading(5).text).toBe("lontane (≥ 5 Å)");
    expect(lddtReading(null)).toBeNull();
    expect(lddtReading(0.95)?.text).toBe("accordo molto alto");
    expect(lddtReading(0.4)).toEqual({ tone: "err", text: "diverge" });
  });
  it("residui: codice a tre lettere, classe, differenza", () => {
    expect(residueText("K")).toBe("K (LYS)");
    expect(residueText("X")).toBe("X");
    expect(residueClass("i")?.one).toBe("idrofobico");
    expect(residueClass("X")).toBeNull();
    expect(residueChange("K", "k").kind).toBe("identical");
    expect(residueChange("K", "R")).toEqual({ kind: "conservative", text: "sostituzione conservativa (entrambi: positivo)" });
    expect(residueChange("L", "D")).toEqual({ kind: "different", text: "sostituzione: idrofobico → negativo" });
    expect(residueChange("X", "G").text).toBe("sostituzione: X → glicina");
  });
  it("numerazione dei due file ed etichette", () => {
    expect(numberingText({ resnum_a: "10", resnum_b: "12" })).toBe("B = A + 2");
    expect(numberingText({ resnum_a: "10", resnum_b: "7" })).toBe("B = A − 3");
    expect(numberingText({ resnum_a: "52A", resnum_b: "52" })).toBe("stessa numerazione");
    expect(numberingText({ resnum_a: "?", resnum_b: "1" })).toBe("—");
    expect(pairResidue(pair(0), "a")).toBe("K1");
    expect(pairResidue(pair(0), "b", true)).toBe("B:K3");
  });
  it("identità sulle coppie", () => {
    expect(pairIdentity([])).toBeNull();
    expect(pairIdentity([pair(0), pair(1, { aa_b: "R" }), pair(2), pair(3, { aa_b: "D" })])).toBe(50);
  });
});

describe("tratti e intervallo visibile", () => {
  it("dettagli di un tratto: residui di B, medie, residui diversi", () => {
    const pairs = [pair(0), pair(1, { distance: 3, lddt: 0.4, aa_b: "R" }), pair(2, { distance: 5, lddt: null }), pair(3, { distance: 4, lddt: 0.6 })];
    const d = segmentDetail(pairs, { start: 1, end: 3 });
    expect(d).toMatchObject({ count: 3, fromB: "4", toB: "6", chainA: "A", chainB: "B", maxDistance: 5, differing: 1 });
    expect(d.meanDistance).toBeCloseTo(4);
    expect(d.meanLddt).toBeCloseTo(0.5);
    expect(segmentDetail(pairs, { start: 2, end: 2 }).meanLddt).toBeNull();
  });
  it("barra dello zoom: residui di A visibili e conteggio", () => {
    const pairs = Array.from({ length: 50 }, (_, i) => pair(i));
    expect(pairSpan(pairs, 0, 49)).toBe("A: 1–50 · 50 coppie");
    expect(pairSpan(pairs, 9.4, 20.2)).toBe("A: 11–21 · 11 di 50 coppie");
    const multi = [pair(0), pair(1, { chain_a: "C", resnum_a: "7" })];
    expect(pairSpan(multi, 0, 1)).toBe("A: A1–C7 · 2 coppie");
    expect(pairSpan([], 0, 0)).toBe("0 coppie");
  });
});

describe("esito a parole e formattatori comuni", () => {
  it("TM-score: la lettura della sua soglia, senza numeri", () => {
    expect([0.81, 0.3, 0.1].map(tmReading)).toEqual(["stesso ripiegamento", "somiglianza debole", "come due strutture casuali"]);
    expect(tmReading(null)).toBeUndefined();
    expect(tmReading(Number.NaN)).toBeUndefined();
  });
  it("valore con unità da lib/format, riesportato: NaN e mancanti diventano un trattino", () => {
    expect(fmtUnit(2.314, 2, "Å")).toBe("2,31 Å");
    expect(fmtUnit(Number.NaN, 2, "Å")).toBe("—");
    expect(fmtUnit(undefined, 0, "%")).toBe("—");
  });
});
