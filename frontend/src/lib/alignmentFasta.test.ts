import { describe, expect, it } from "vitest";
import { alignmentFasta } from "./alignmentFasta";

describe("FASTA dell'allineamento", () => {
  it("sequenze con i gap, una voce per sequenza", () => {
    expect(alignmentFasta(["bersaglio", "templ"], ["MKT-A", "MKSYA"])).toBe(">bersaglio\nMKT-A\n>templ\nMKSYA\n");
  });

  it("voci PIR: interruzioni di catena tolte, residui BLK come gap", () => {
    const fasta = alignmentFasta(["bersaglio", "templ"], ["MKTA/GHW..", "MK-A/GHWFF"]);
    expect(fasta).toBe(">bersaglio\nMKTAGHW--\n>templ\nMK-AGHWFF\n");
  });

  it("interruzioni non allineate: gap al loro posto, colonne invariate", () => {
    expect(alignmentFasta(["a", "b"], ["MK/T", "MKS/"])).toBe(">a\nMK-T\n>b\nMKS-\n");
  });
});
