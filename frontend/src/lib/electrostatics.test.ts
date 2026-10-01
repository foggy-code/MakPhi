import { describe, expect, it } from "vitest";
import type { ElectrostaticsResult } from "../api/types";
import { colorFor, legendFor, NO_VALUE_COLOR } from "./colorscale";
import {
  chargeBreakdown, chargeText, extremeResidues, MV_PER_KT_E, POTENTIAL_LIMIT, POTENTIAL_SCALE, potentialColoring, potentialStripRows, potentialText, toMillivolt,
} from "./electrostatics";
import { coloringMap, describeResidue, hasMissing } from "./residueColoring";

// valori del calcolo reale su 1UBQ (PARSE, pH 7; test del backend): Lys6 +2,42, Glu24 −2,09, Ile3 sepolta
const RESULT = {
  source: { type: "template", id: "1UBQ" }, label: "1UBQ", source_sha256: "x", forcefield: "PARSE", ph: 7, net_charge: 0,
  charges: { net_charge: 0, positive: 12, negative: 12, by_type: { "ARG+": 4, "ASP−": 5, "C-term−": 1, "GLU−": 6, "LYS+": 7, "N-term+": 1 }, charged: [] },
  scale: { unit: "kT/e", limit: 5, mv_per_unit: 25.69 },
  residues: [
    { chain: "A", resnum: "3", resname: "ILE", potential: null, exposed_atoms: 0, points: 0, charge: 0 },
    { chain: "A", resnum: "6", resname: "LYS", potential: 2.418, exposed_atoms: 4, points: 77, charge: 1 },
    { chain: "A", resnum: "24", resname: "GLU", potential: -2.091, exposed_atoms: 6, points: 108, charge: -1 },
    { chain: "A", resnum: "42", resname: "ARG", potential: 7.2, exposed_atoms: 10, points: 100, charge: 1 },
    { chain: "A", resnum: "58", resname: "ASP", potential: -1.082, exposed_atoms: 6, points: 79, charge: -1 },
    { chain: "A", resnum: "60", resname: "ASN", potential: 0, exposed_atoms: 8, points: 109, charge: 0 },
  ],
} as unknown as ElectrostaticsResult;

describe("elettrostatica", () => {
  it("scala divergente rosso-bianco-blu saturata a ±5 kT/e", () => {
    expect(POTENTIAL_LIMIT).toBe(5);
    expect(colorFor(POTENTIAL_SCALE, -5)).toBe("#b2182b");
    expect(colorFor(POTENTIAL_SCALE, -12)).toBe("#b2182b");
    expect(colorFor(POTENTIAL_SCALE, 0)).toBe("#f7f7f7");
    expect(colorFor(POTENTIAL_SCALE, 5)).toBe("#2166ac");
    expect(colorFor(POTENTIAL_SCALE, 30)).toBe("#2166ac");
    expect(colorFor(POTENTIAL_SCALE, null)).toBe(NO_VALUE_COLOR);
    const legend = legendFor(POTENTIAL_SCALE, "x");
    expect(legend.kind === "gradient" && legend.ticks.map((t) => t.label)).toEqual(["≤ −5", "0", "≥ +5 kT/e"]);
  });

  it("kT/e in millivolt a 298,15 K", () => {
    expect(toMillivolt(1)).toBeCloseTo(MV_PER_KT_E);
    expect(potentialText(2.418)).toBe("+2,42 kT/e (+62 mV)");
    expect(potentialText(-2.091)).toBe("-2,09 kT/e (-54 mV)");
    expect(potentialText(null)).toBe("sepolto (nessun atomo esposto)");
    expect(chargeText(1)).toBe("+1");
    expect(chargeText(-0.5)).toBe("-0,5");
    expect(chargeText(0)).toBe("0");
  });

  it("colorazione per residuo: positivi blu, negativi rossi, sepolti grigi", () => {
    const c = potentialColoring(RESULT)!;
    expect(c.key).toBe("potential");
    expect(c.title).toContain("PARSE");
    const map = coloringMap(c);
    expect(map.get("A:3")).toBe(NO_VALUE_COLOR);
    expect(map.get("A:42")).toBe("#2166ac");
    expect(map.get("A:60")).toBe("#f7f7f7");
    expect(hasMissing(c)).toBe(true);
    expect(describeResidue(c, { chain: "A", resnum: "6" })).toBe("+2,42 kT/e (+62 mV) (carica +1)");
    expect(describeResidue(c, { chain: "A", resnum: "60" })).toBe("0,00 kT/e (0 mV)");
    expect(potentialColoring(null)).toBeNull();
  });

  it("residui estremi e cariche per tipo", () => {
    const e = extremeResidues(RESULT, 2);
    expect(e.positive.map((r) => r.resnum)).toEqual(["42", "6"]);
    expect(e.negative.map((r) => r.resnum)).toEqual(["24", "58"]);
    // un residuo quasi sepolto (Ile13 di 1UBQ: un atomo, un punto, +3,8 kT/e) non compare fra gli estremi
    const noisy = { ...RESULT, residues: [...RESULT.residues, { chain: "A", resnum: "13", resname: "ILE", potential: 3.84, exposed_atoms: 1, points: 1, charge: 0 }] };
    expect(extremeResidues(noisy, 2).positive.map((r) => r.resnum)).toEqual(["42", "6"]);
    expect(chargeBreakdown(RESULT)).toBe("7 LYS+, 4 ARG+, 1 N-term+ · 6 GLU−, 5 ASP−, 1 C-term−");
    expect(chargeBreakdown(null)).toBe("nessun residuo carico");
    expect(potentialStripRows(RESULT).map((r) => `${r.chain}${r.resnum}`)).toEqual(["A3", "A6", "A24", "A42", "A58", "A60"]);
  });
});
