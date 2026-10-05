import { describe, expect, it } from "vitest";
import { AMINO, clustalColor, RESIDUE_CLASSES, residueClass } from "./aminoAcids";
import * as compareMetrics from "./compareMetrics";

describe("classi chimiche degli amminoacidi (Clustal X)", () => {
  it("ogni residuo standard in una sola classe; gap e non standard in nessuna", () => {
    for (const aa of Object.keys(AMINO)) expect(RESIDUE_CLASSES.filter((c) => c.letters.includes(aa))).toHaveLength(1);
    expect(residueClass("m")?.label).toBe("Idrofobici");
    expect(residueClass("H")?.label).toBe("His/Tyr");
    expect(residueClass("-")).toBeNull();
    expect(residueClass("X")).toBeNull();
    expect(residueClass("KR")).toBeNull();
  });
  it("colore del residuo rappresentativo, lo stesso dell'allineamento", () => {
    expect(clustalColor("A")).toBe("#80a0f0");
    expect(clustalColor("K")).toBe("#f01505");
    expect(clustalColor("D")).toBe("#c048c0");
    expect(clustalColor("N")).toBe("#15c015");
    expect(clustalColor("G")).toBe("#f09048");
    expect(clustalColor("-")).toBeUndefined();
  });
  it("compareMetrics riesporta la stessa tabella (un'unica fonte)", () => {
    expect(compareMetrics.RESIDUE_CLASSES).toBe(RESIDUE_CLASSES);
    expect(compareMetrics.residueClass).toBe(residueClass);
  });
});
