import { describe, expect, it } from "vitest";
import { colorFor } from "../../lib/colorscale";
import { contrastRatio, parseColor } from "../../lib/contrast";
import { centeredRange, firstResnum, IDENTITY_SCALE, inView, integerTicks, manyChains, residueSpan, residueTicks, runs, textOn, tickDigits } from "./linearHelpers";

const chainA = Array.from({ length: 300 }, (_, i) => ({ chain: "A", resnum: String(i + 1) }));

describe("asse dei residui", () => {
  it("residui con il centro nell'intervallo visibile", () => {
    expect(centeredRange({ lo: 0, hi: 299 }, 300)).toEqual([0, 299]);
    expect(centeredRange({ lo: -0.5, hi: 299.5 }, 300)).toEqual([0, 299]); // celle da i − 0,5 a i + 0,5
    expect(centeredRange({ lo: 11.2, hi: 47.6 }, 300)).toEqual([12, 47]);
  });
  it("intervallo per la barra dello zoom, con la catena solo se ce n'è più d'una", () => {
    // numeri dei residui e totale distinti: "12–48 di 300" sembrerebbe una posizione su 300 (numerazione PDB)
    expect(residueSpan(chainA, { lo: 11, hi: 47 }, false)).toBe("residui 12–48 (300 in tutto)");
    const two = [...chainA.slice(0, 2), { chain: "B", resnum: "1" }];
    expect(manyChains(two)).toBe(true);
    expect(manyChains(chainA)).toBe(false);
    expect(residueSpan(two, { lo: 0, hi: 2 }, true)).toBe("residui A1–B1 (3 in tutto)");
  });
  it("tacche intere e tonde nella numerazione da 1", () => {
    expect(integerTicks(1, 300, 6)).toEqual([50, 100, 150, 200, 250, 300]);
    expect(integerTicks(3, 7, 10)).toEqual([3, 4, 5, 6, 7]); // mai mezzi residui
    expect(residueTicks({ lo: 0, hi: 299 }, 6, 300)).toEqual([49, 99, 149, 199, 249, 299]);
    expect(residueTicks({ lo: 5, hi: 3 }, 6, 300)).toEqual([]);
  });
  it("tacche sui numeri di residuo tondi quando la numerazione non parte da 1", () => {
    const from25 = Array.from({ length: 300 }, (_, i) => ({ chain: "A", resnum: String(i + 25) }));
    expect(firstResnum(from25)).toBe(25);
    expect(firstResnum(chainA)).toBe(1);
    const ticks = residueTicks({ lo: 0, hi: 299 }, 6, 300, 25);
    expect(ticks.map((i) => from25[i].resnum)).toEqual(["50", "100", "150", "200", "250", "300"]);
    expect(residueTicks({ lo: 0, hi: 299 }, 6, 300, 1)).toEqual(residueTicks({ lo: 0, hi: 299 }, 6, 300));
  });
  it("tacche per posizione con più catene, lacune o codici d'inserzione", () => {
    expect(firstResnum([{ chain: "A", resnum: "1" }, { chain: "B", resnum: "2" }])).toBeNull();
    expect(firstResnum([{ chain: "A", resnum: "10" }, { chain: "A", resnum: "12" }])).toBeNull();
    expect(firstResnum([{ chain: "A", resnum: "52" }, { chain: "A", resnum: "52A" }])).toBeNull();
    expect(firstResnum([{ chain: "A", resnum: "-2" }, { chain: "A", resnum: "-1" }, { chain: "A", resnum: "0" }])).toBe(-2);
    expect(firstResnum([])).toBeNull();
  });
  it("decimali delle etichette dal passo delle tacche", () => {
    expect(tickDigits([0, 0.2, 0.4])).toBe(1);
    expect(tickDigits([-0.05, 0, 0.05])).toBe(2);
    expect(tickDigits([0, 50, 100])).toBe(0);
    expect(tickDigits([0.35])).toBe(2); // serie costante: una tacca sola
    expect(tickDigits([250])).toBe(0);
  });
});

describe("tratti consecutivi", () => {
  it("un tratto per valori uguali, anche da una stringa", () => {
    expect(runs([true, true, false, true])).toEqual([
      { start: 0, end: 1, value: true }, { start: 2, end: 2, value: false }, { start: 3, end: 3, value: true },
    ]);
    expect(runs("HHHCCE").map((r) => `${r.value}${r.start}-${r.end}`)).toEqual(["H0-2", "C3-4", "E5-5"]);
    expect(runs([])).toEqual([]);
  });
  it("visibile se il tratto entra anche solo in parte", () => {
    expect(inView({ start: 10, end: 20 }, { lo: 20.4, hi: 40 })).toBe(true);
    expect(inView({ start: 10, end: 20 }, { lo: 20.6, hi: 40 })).toBe(false);
    expect(inView({ start: 41, end: 41 }, { lo: 20, hi: 40.6 })).toBe(true);
  });
});

describe("testo sulle celle della matrice di identità", () => {
  it("nero o bianco con contrasto ≥ 4,5:1 su ogni colore della scala", () => {
    for (let v = 0; v <= 100; v += 0.5) {
      const fill = colorFor(IDENTITY_SCALE, v);
      const ink = textOn(fill);
      expect(["#000000", "#ffffff"]).toContain(ink);
      expect(contrastRatio(parseColor(ink)!, parseColor(fill)!), `${v}% (${fill})`).toBeGreaterThanOrEqual(4.5);
    }
  });
  it("chiaro sulle celle scure, scuro sulle chiare; un colore non esadecimale usa il testo del tema", () => {
    expect(textOn(colorFor(IDENTITY_SCALE, 0))).toBe("#ffffff");
    expect(textOn(colorFor(IDENTITY_SCALE, 100))).toBe("#000000");
    expect(textOn("var(--accent)")).toBe("var(--text)");
  });
});
