import { describe, expect, it } from "vitest";
import { parsePir } from "./pir";
import { columnCodes, columnStates, conflictCells, conflictText, gapConflicts, secondaryKey, toSS3 } from "./pirSecondary";

const kinds = ["sequence", "structureX"];
const dssp = [null, "dssp"];

describe("stati per colonna", () => {
  it("DSSP a 8 stati → 3", () => {
    expect(["H", "G", "I", "E", "B", "T", "S", "-", "?", undefined].map(toSS3)).toEqual(["H", "H", "H", "E", "E", "C", "C", "C", "?", "?"]);
  });

  it("la k-esima lettera prende il k-esimo stato, gap e separatori restano vuoti", () => {
    expect(columnStates("M-K/T.", "HGE")).toEqual(["H", " ", "H", " ", "E", " "]);
    expect(columnStates("MKT", "H")).toEqual(["H", "?", "?"]);
    expect(columnCodes("M-K", "GT")).toEqual(["G", " ", "T"]);
  });

  it("la chiave non cambia spostando i gap, cambia con i residui", () => {
    const a = parsePir(">P1;t\nsequence:t::::::::\nMK-T*\n");
    const b = parsePir(">P1;t\nsequence:t::::::::\n-MKT*\n");
    const c = parsePir(">P1;t\nsequence:t::::::::\nMRT*\n");
    expect(secondaryKey(a, [])).toBe(secondaryKey(b, []));
    expect(secondaryKey(a, [])).not.toBe(secondaryKey(c, []));
  });
});

describe("gapConflicts", () => {
  it("inserzione del bersaglio dentro un'elica del template, colonne unite", () => {
    const rows = ["MKTAYIA", "MKT--IA"];
    const states = [null, columnStates(rows[1], "HHHHH")];
    expect(gapConflicts(rows, kinds, states, dssp)).toEqual([{ row: 1, kind: "insertion", element: "H", from: 3, to: 4 }]);
  });

  it("nessun conflitto se il gap è al confine dell'elica o fuori", () => {
    const rows = ["MKTAYIA", "MKT--IA"];
    expect(gapConflicts(rows, kinds, [null, columnStates(rows[1], "HHHCC")], dssp)).toEqual([]);
    expect(gapConflicts(rows, kinds, [null, columnStates(rows[1], "CCCCC")], dssp)).toEqual([]);
  });

  it("delezione del bersaglio di fronte a un filamento", () => {
    const rows = ["MK--IA", "MKTAIA"];
    const states = [null, columnStates(rows[1], "CEEEEC")];
    expect(gapConflicts(rows, kinds, states, dssp)).toEqual([{ row: 1, kind: "deletion", element: "E", from: 2, to: 3 }]);
  });

  it("non scavalca le catene e ignora predizioni e voci senza struttura secondaria", () => {
    const rows = ["MKT/AYI", "MK-/-YI"];
    expect(gapConflicts(rows, kinds, [null, columnStates(rows[1], "HHHH")], dssp)).toEqual([]);
    const ins = ["MKTAY", "MK-AY"];
    expect(gapConflicts(ins, kinds, [null, columnStates(ins[1], "HHHH")], [null, "gor"])).toEqual([]);
    expect(gapConflicts(ins, kinds, [null, null], dssp)).toEqual([]);
    expect(gapConflicts(ins, ["structureX", "structureX"], [null, columnStates(ins[1], "HHHH")], dssp)).toEqual([]);
  });

  it("testo e celle", () => {
    expect(conflictText({ kind: "insertion", element: "H" })).toBe("Inserzione del bersaglio dentro un'elica");
    expect(conflictText({ kind: "deletion", element: "E" })).toBe("Delezione del bersaglio in un filamento β");
    expect([...conflictCells([{ row: 1, kind: "deletion", element: "E", from: 2, to: 3 }])]).toEqual(["1:2", "1:3"]);
  });
});
