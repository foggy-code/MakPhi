import { describe, expect, it } from "vitest";
import { columnConservation, conservationLevel, hydroClass } from "./pirColors";
import { insertMarkerColumn, trimUncoveredEnds } from "./pirEdit";

describe("colori", () => {
  it("idrofobicità di Kyte e Doolittle", () => {
    expect(["I", "a", "G", "K", "-", "X"].map(hydroClass)).toEqual(["phobic", "phobic", "mid", "philic", null, null]);
  });

  it("conservazione per colonna: il residuo più frequente sul totale delle voci", () => {
    expect(columnConservation(["MKA", "MRA", "M-A", "LKA"])).toEqual([0.75, 0.5, 1]);
    expect(columnConservation(["M-", "M"])).toEqual([1, 0]);
    expect([0.95, 0.75, 0.5, 0.2].map(conservationLevel)).toEqual([3, 2, 1, 0]);
  });
});

describe("marcatori", () => {
  it("il separatore di catena va in tutte le voci, il blocco di ligando solo nelle righe scelte", () => {
    expect(insertMarkerColumn(["MKTA", "MKSA"], 2, "/", new Set([0]))).toEqual(["MK/TA", "MK/SA"]);
    expect(insertMarkerColumn(["MKTA", "MKSA", "MKTA"], 4, ".", new Set([0, 1]))).toEqual(["MKTA.", "MKSA.", "MKTA-"]);
  });
});

describe("ritaglio delle estremità", () => {
  const kinds = ["sequence", "structureX"];

  it("toglie le code del bersaglio senza template e conta i residui tolti", () => {
    expect(trimUncoveredEnds(["MKTAYIAK", "--TAYI--"], kinds)).toEqual({ rows: ["TAYI", "TAYI"], start: 2, end: 2 });
    expect(trimUncoveredEnds(["M-TAYI", "--TAYI"], kinds)).toEqual({ rows: ["TAYI", "TAYI"], start: 1, end: 0 });
  });

  it("non scavalca separatori e ligandi, null se non c'è nulla da togliere", () => {
    expect(trimUncoveredEnds(["MK/TA", "--/TA"], kinds)).toBeNull();
    expect(trimUncoveredEnds(["TAYI", "TAYI"], kinds)).toBeNull();
    expect(trimUncoveredEnds(["--TA", "MKTA"], kinds)).toBeNull();
    expect(trimUncoveredEnds(["MKTA"], ["sequence"])).toBeNull();
  });
});
