import { describe, expect, it } from "vitest";
import { colorFor } from "./colorscale";
import { alignmentConfidence, IDENTITY_SCALE, localIdentity, pirReference, regionLabel, TWILIGHT_IDENTITY } from "./alignConfidence";

describe("identità locale", () => {
  it("finestra centrata di 9 colonne, troncata alle estremità", () => {
    const ref = "AAAAAAAAAAAAAAAAAAAA";
    const other = "AAAAAAAAAACCCCCCCCCC";
    const id = localIdentity(ref, other);
    expect(id[0]).toBe(100); // colonne 0–4
    expect(id[10]).toBeCloseTo(100 * 4 / 9); // colonne 6–14: 4 identiche su 9
    expect(id[19]).toBe(0);
  });
  it("i gap del template contano come non identici, i gap del bersaglio non contano", () => {
    expect(localIdentity("AAAA", "A-A-", 3)).toEqual([50, 200 / 3, 100 / 3, 50]);
    expect(localIdentity("A--A", "AAAA", 3)).toEqual([100, 100, 100, 100]);
    expect(localIdentity("----", "AAAA", 3)).toEqual([null, null, null, null]);
  });
  it("maiuscole e minuscole equivalenti", () => {
    expect(localIdentity("mkt", "MKT", 3)).toEqual([100, 100, 100]);
  });
});

describe("confidenza dell'allineamento", () => {
  // bersaglio di 30 residui: 1–5 senza template, 6–20 identici, 21–30 diversi (zona crepuscolare)
  const target = "MKTAYIAKQRQISFVKSHFSRQLEERLGLI";
  const template = "-----IAKQRQISFVKSHFSWWWWWWWWWW";

  it("zona crepuscolare e residui non coperti, in numerazione del bersaglio", () => {
    const conf = alignmentConfidence([target, template]);
    expect(conf.columns[0]).toMatchObject({ residue: true, targetIndex: 1, covered: false, uncovered: true, twilight: false, best: null });
    expect(conf.columns[10].best).toBe(100);
    expect(conf.columns[29].twilight).toBe(true);
    const labels = conf.regions.map((r) => `${r.kind} ${regionLabel(r)}`);
    expect(labels[0]).toBe("uncovered 1–5");
    const twilight = conf.regions.find((r) => r.kind === "twilight")!;
    expect(twilight.toResidue).toBe(30);
    // la finestra (±4) porta la zona crepuscolare qualche residuo prima del cambio a 21
    expect(twilight.fromResidue).toBeGreaterThanOrEqual(21);
    expect(twilight.fromResidue).toBeLessThanOrEqual(25);
    expect(conf.summary).toMatchObject({ residues: 30, covered: 25, uncovered: 5 });
    expect(conf.summary.twilight).toBe(twilight.toResidue - twilight.fromResidue + 1);
    expect(conf.rows[0]).toBeNull();
  });

  it("la colonna prende il miglior template che la copre", () => {
    const a = "AAAAAAAAAA";
    const t1 = "AAAAACCCCC";
    const t2 = "CCCCCAAAAA";
    const conf = alignmentConfidence([a, t1, t2], 0, 3);
    expect(conf.columns.every((c) => c.best !== null && c.best >= 50)).toBe(true);
    expect(conf.regions).toEqual([]);
  });

  it("il bersaglio può essere una riga qualunque (PIR: voce sequence)", () => {
    const conf = alignmentConfidence([template, target], 1);
    expect(conf.reference).toBe(1);
    expect(conf.rows[1]).toBeNull();
    expect(conf.summary.uncovered).toBe(5);
    expect(pirReference(["structureX", "structureX", "sequence"])).toBe(2);
    expect(pirReference(["structureX"])).toBe(0);
  });

  it("gap del bersaglio non interrompono le regioni", () => {
    const conf = alignmentConfidence(["AA--AA", "------"], 0, 3);
    expect(conf.regions).toEqual([{ kind: "uncovered", fromColumn: 0, toColumn: 5, fromResidue: 1, toResidue: 4 }]);
  });

  it("una sola sequenza: nessun residuo non coperto", () => {
    const conf = alignmentConfidence(["MKT"]);
    expect(conf.summary.uncovered).toBe(0);
    expect(conf.regions).toEqual([]);
  });
});

describe("scala dell'identità", () => {
  it("salto netto alla soglia del 25%", () => {
    expect(TWILIGHT_IDENTITY).toBe(25);
    expect(colorFor(IDENTITY_SCALE, 24.9)).toBe("#ef8a62");
    expect(colorFor(IDENTITY_SCALE, 25)).toBe("#fddc5c");
    expect(colorFor(IDENTITY_SCALE, 100)).toBe("#1a9850");
  });
});
