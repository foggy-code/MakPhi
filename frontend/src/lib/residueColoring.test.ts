import { describe, expect, it } from "vitest";
import type { ConsensusResult, QualityReport, ResidueRow } from "../api/types";
import { colorFor, NO_VALUE_COLOR } from "./colorscale";
import {
  atomKey, colorOptions, coloringMap, CONSENSUS_SCALE, describeResidue, hasMissing, PLDDT_SCALE, QMEAN_SCALE, regionResidues, residueColoring, residueKey,
} from "./residueColoring";

function row(resnum: number, extra: Partial<ResidueRow> = {}): ResidueRow {
  return {
    chain: "A", resnum: String(resnum), resname: "ALA", aa: "A", ss: "H", ss3: "H", rsa: 0.3, bfactor: 20, phi: -60, psi: -45,
    rama: "favored", clashes: 0, dope: -0.04, ...extra,
  };
}

function report(residues: ResidueRow[], kind?: "plddt" | "bfactor"): QualityReport {
  return { residues, structure: { bfactor_kind: kind } } as unknown as QualityReport;
}

function consensusFor(_residues: ResidueRow[], flags: string[][], qmean?: (number | null)[]): ConsensusResult {
  return {
    per_residue: {
      signals: [], flags, counts: flags.map((f) => f.length), values: qmean ? { qmean } : {}, dope_threshold: -0.03,
    },
    regions: [],
  } as unknown as ConsensusResult;
}

describe("chiavi dei residui", () => {
  it("catena + numero con codice di inserzione, uguale per rapporto e atomi di 3Dmol", () => {
    expect(residueKey("A", "52A")).toBe("A:52A");
    expect(atomKey({ chain: "A", resi: 52, icode: "A" })).toBe("A:52A");
    expect(atomKey({ chain: "B", resi: 7, icode: " " })).toBe("B:7");
    expect(atomKey({ chain: "B", resi: 7 })).toBe("B:7");
  });
});

describe("opzioni Colora per", () => {
  it("B-factor oppure pLDDT secondo il tipo di struttura", () => {
    const res = [row(1), row(2)];
    const plain = colorOptions(report(res, "bfactor")).map((o) => o.value);
    expect(plain).toContain("bfactor");
    expect(plain).not.toContain("plddt");
    const af = colorOptions(report(res, "plddt")).map((o) => o.value);
    expect(af).toContain("plddt");
    expect(af).not.toContain("bfactor");
    // rapporti precedenti senza bfactor_kind: B-factor
    expect(colorOptions(report(res)).map((o) => o.value)).toContain("bfactor");
  });
  it("QMEAN e consenso solo se calcolati e allineati", () => {
    const res = [row(1), row(2)];
    const none = colorOptions(report(res));
    expect(none.find((o) => o.value === "qmean")?.available).toBe(false);
    expect(none.find((o) => o.value === "consensus")?.available).toBe(false);
    const withQ = colorOptions(report(res), consensusFor(res, [[], []], [0.9, null]));
    expect(withQ.find((o) => o.value === "qmean")?.available).toBe(true);
    expect(withQ.find((o) => o.value === "consensus")?.available).toBe(true);
    // consenso di un'altra struttura (lunghezze diverse): ignorato
    const other = colorOptions(report(res), consensusFor([row(1)], [[]], [0.9]));
    expect(other.find((o) => o.value === "consensus")?.available).toBe(false);
  });
  it("DOPE non disponibile senza MODELLER", () => {
    const opts = colorOptions(report([row(1, { dope: null })]));
    expect(opts.find((o) => o.value === "dope")).toMatchObject({ available: false, note: "richiede il calcolo DOPE (MODELLER)" });
    expect(residueColoring(report([row(1, { dope: null })]), "dope")).toBeNull();
  });
});

describe("valori per residuo", () => {
  it("Ramachandran categoriale, estremità senza φ/ψ in grigio", () => {
    const rep = report([row(1, { rama: null }), row(2, { rama: "outlier" }), row(3)]);
    const c = residueColoring(rep, "rama")!;
    const colors = coloringMap(c);
    expect(colors.get("A:1")).toBe(NO_VALUE_COLOR);
    expect(colors.get("A:2")).toBe("#ef4444");
    expect(colors.get("A:3")).toBe("#34d399");
    expect(hasMissing(c)).toBe(true);
    expect(describeResidue(c, { chain: "A", resnum: "1" })).toBe("senza φ/ψ");
    expect(describeResidue(c, { chain: "A", resnum: "2" })).toBe("outlier");
    expect(describeResidue(c, { chain: "Z", resnum: "2" })).toBe("—");
  });
  it("pLDDT con la scala ufficiale di AlphaFold DB", () => {
    const rep = report([row(1, { bfactor: 95 }), row(2, { bfactor: 90 }), row(3, { bfactor: 70 }), row(4, { bfactor: 50.0 }), row(5, { bfactor: 12 })], "plddt");
    const c = residueColoring(rep, "plddt")!;
    expect(c.scale).toBe(PLDDT_SCALE);
    expect([...coloringMap(c).values()]).toEqual(["#0053D6", "#65CBF3", "#FFDB13", "#FF7D45", "#FF7D45"]);
    expect(describeResidue(c, { chain: "A", resnum: "1" })).toBe("95,0 · molto alta");
    expect(c.title).toContain("pLDDT");
  });
  it("DOPE su scala dal minimo al massimo, con la soglia dei picchi del consenso", () => {
    const res = [row(1, { dope: -0.05 }), row(2, { dope: -0.01 }), row(3, { dope: -0.03 })];
    const c = residueColoring(report(res), "dope", consensusFor(res, [[], ["dope"], []]))!;
    expect(c.scale.kind).toBe("continuous");
    if (c.scale.kind !== "continuous") return;
    expect(c.scale.stops[0]).toEqual({ value: -0.05, color: "#2166ac" });
    expect(c.scale.stops[c.scale.stops.length - 1]).toEqual({ value: -0.01, color: "#b2182b" });
    expect(c.scale.ticks?.map((t) => t.label ?? t.value)).toEqual([-0.05, "picco", -0.01]);
    expect(colorFor(c.scale, -0.01)).toBe("#b2182b"); // più alto = peggio = rosso
  });
  it("B-factor dal minimo al massimo", () => {
    const c = residueColoring(report([row(1, { bfactor: 10 }), row(2, { bfactor: 40 })]), "bfactor")!;
    expect(coloringMap(c).get("A:1")).toBe("#2166ac");
    expect(coloringMap(c).get("A:2")).toBe("#b2182b");
    expect(describeResidue(c, { chain: "A", resnum: "2" })).toBe("40,0 Å²");
  });
  it("QMEAN locale con salto di colore a 0,6", () => {
    const res = [row(1), row(2), row(3)];
    const c = residueColoring(report(res), "qmean", consensusFor(res, [[], [], []], [0.59, 0.6, null]))!;
    expect(c.scale).toBe(QMEAN_SCALE);
    const colors = [...coloringMap(c).values()];
    expect(colors[0]).not.toBe(colors[1]);
    expect(colors[2]).toBe(NO_VALUE_COLOR);
  });
  it("consenso: numero di metodi e loro nomi nel tooltip", () => {
    const res = [row(1), row(2), row(3), row(4)];
    const c = residueColoring(report(res), "consensus", consensusFor(res, [[], ["dope"], ["rama", "dope"], ["rama", "clash", "dope"]]))!;
    expect(c.scale).toBe(CONSENSUS_SCALE);
    expect([...coloringMap(c).values()]).toEqual(["#e2e8f0", "#fde047", "#fb923c", "#dc2626"]);
    expect(describeResidue(c, { chain: "A", resnum: "3" })).toBe("2 metodi (Ramachandran, DOPE)");
    expect(describeResidue(c, { chain: "A", resnum: "1" })).toBe("0 metodi");
  });
  it("struttura secondaria, clash e RSA", () => {
    const rep = report([row(1, { ss3: "E", clashes: 2, rsa: 1.4 }), row(2, { ss3: "C", clashes: 1, rsa: 0 })]);
    expect([...coloringMap(residueColoring(rep, "ss")!).values()]).toEqual(["#fbbf24", "#94a3b8"]);
    expect([...coloringMap(residueColoring(rep, "clashes")!).values()]).toEqual(["#dc2626", "#f59e0b"]);
    const rsa = coloringMap(residueColoring(rep, "rsa")!);
    expect(rsa.get("A:1")).toBe("#fde725"); // oltre 1 satura come "esposto"
    expect(rsa.get("A:2")).toBe("#440154");
  });
  it("residui di una regione del consenso", () => {
    const rep = report([row(10), row(11), row(12), row(13)]);
    expect(regionResidues(rep, { start_index: 1, end_index: 2 })).toEqual([{ chain: "A", resnum: "11" }, { chain: "A", resnum: "12" }]);
  });
});
