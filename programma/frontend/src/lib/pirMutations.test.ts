import { describe, expect, it } from "vitest";
import { parsePir } from "./pir";
import { blosum62, blosumTone, chainIds, columnOfResidue, isStandardAa, originalSequences, pirMutations, residuePositions, residuesOf, revertMutations, startNumber, substitute } from "./pirMutations";

const PIR = `>P1;target
sequence:target:::::::0.00: 0.00
MK-TA/YI*

>P1;1ABC_A
structureX:1ABC_A:5:A:12:A::::
MKSTA/Y-*
`;

describe("blosum62", () => {
  it("valori noti della matrice, simmetrica", () => {
    expect(blosum62("W", "W")).toBe(11);
    expect(blosum62("K", "R")).toBe(2);
    expect(blosum62("R", "K")).toBe(2);
    expect(blosum62("C", "E")).toBe(-4);
    expect(blosum62("i", "v")).toBe(3);
    expect(blosum62("X", "A")).toBeNull();
  });

  it("toni del punteggio", () => {
    expect(blosumTone(2)).toBe("conservative");
    expect(blosumTone(0)).toBe("neutral");
    expect(blosumTone(-3)).toBe("radical");
    expect(blosumTone(null)).toBe("neutral");
  });

  it("solo i 20 amminoacidi standard", () => {
    expect(isStandardAa("k")).toBe(true);
    expect(isStandardAa("B")).toBe(false);
    expect(isStandardAa("-")).toBe(false);
  });
});

describe("substitute", () => {
  it("sostituisce un residuo e lascia stare il resto", () => {
    expect(substitute(["MKTA", "MKSA"], 0, 1, "r")).toEqual(["MRTA", "MKSA"]);
  });

  it("rifiuta gap, marcatori, lettere non standard e residui uguali", () => {
    expect(substitute(["M-/A"], 0, 1, "K")).toBeNull();
    expect(substitute(["M-/A"], 0, 2, "K")).toBeNull();
    expect(substitute(["MKTA"], 0, 1, "B")).toBeNull();
    expect(substitute(["MKTA"], 0, 1, "k")).toBeNull();
  });
});

describe("numerazione", () => {
  it("primo residuo dalla seconda riga PIR", () => {
    expect(startNumber("structureX:1ABC_A:5:A:12:A::::")).toBe(5);
    expect(startNumber("sequence:target:::::::0.00: 0.00")).toBe(1);
    expect(startNumber("structureX:1ABC:FIRST:A:LAST:A::::")).toBe(1);
    expect(startNumber("structureX:1ABC:-3:A:40:A::::")).toBe(-3);
  });

  it("nomi delle catene dalla seconda riga, poi in ordine alfabetico", () => {
    expect(chainIds("structureX:1ABC:1:C:50:D::::", 2)).toEqual(["C", "D"]);
    expect(chainIds("structureX:1ABC:1:C:50:C::::", 3)).toEqual(["C", "D", "E"]);
    expect(chainIds("sequence:t::::::::", 2)).toEqual(["A", "B"]);
  });

  it("posizioni per colonna: gap e '/' senza numero, ogni catena da capo", () => {
    const pos = residuePositions("MK-/TA", "structureX:s:7:A:9:B::::");
    expect(pos.map((p) => (p ? `${p.chain}${p.number}` : null))).toEqual(["A7", "A8", null, null, "B1", "B2"]);
    expect(pos[4]).toMatchObject({ index: 3, segment: 1, multichain: true });
  });

  it("colonna dell'n-esimo residuo, saltando gap e marcatori", () => {
    expect(columnOfResidue("MK-TA/YI", 3)).toBe(3);
    expect(columnOfResidue("MK-TA/YI", 5)).toBe(6);
    expect(columnOfResidue("MK-TA/YI", 9)).toBeNull();
    expect(residuesOf("mk-TA/Y.I")).toBe("MKTAYI");
  });
});

describe("pirMutations", () => {
  const entries = parsePir(PIR);
  const original = originalSequences(entries);

  it("nessuna mutazione se si spostano solo i gap", () => {
    const report = pirMutations(original, entries, ["M-KTA/YI", "MKSTA/Y-"]);
    expect(report.mutations).toEqual([]);
    expect(report.lengthChanged).toEqual([]);
  });

  it("elenca le sostituzioni numerate per catena, con il nome della catena", () => {
    const report = pirMutations(original, entries, ["MR-TA/YV", "MRSTA/W-"]);
    expect(report.mutations.map((m) => m.label)).toEqual(["A:K2R", "B:I2V", "A:K6R", "B:Y1W"]);
    expect(report.mutations[0]).toMatchObject({ row: 0, index: 2, column: 1, wt: "K", mut: "R", blosum: 2 });
    expect(report.mutations[1]).toMatchObject({ row: 0, index: 6, column: 7, chain: "B", number: 2 });
    // template: la prima catena parte da 5 (seconda riga PIR), la seconda da 1
    expect(report.mutations[2]).toMatchObject({ row: 1, index: 2, number: 6, chain: "A" });
    expect(report.mutations[3]).toMatchObject({ row: 1, index: 6, number: 1, chain: "B", column: 6, blosum: 2 });
  });

  it("una sola catena: notazione senza catena, numerazione dalla seconda riga", () => {
    const single = parsePir(">P1;t\nsequence:t::::::::\nMKTAY*\n\n>P1;s\nstructureX:s:10:A:14:A::::\nMKTAY*\n");
    const report = pirMutations(originalSequences(single), single, ["MRTAY", "MKTAW"]);
    expect(report.mutations.map((m) => m.label)).toEqual(["K2R", "Y14W"]);
  });

  it("le voci con un codice ripetuto non si confrontano e non si annullano", () => {
    const dup = parsePir(">P1;x\nsequence:x::::::::\nMKTAY*\n\n>P1;x\nstructureX:x:1:A:5:A::::\nLLLLL*\n");
    const original = originalSequences(dup);
    expect(original).toEqual({});
    const report = pirMutations({ x: "MKTAY" }, dup);
    expect(report).toEqual({ mutations: [], lengthChanged: [], duplicated: ["x"] });
  });

  it("segnala le voci che hanno cambiato lunghezza", () => {
    const report = pirMutations(original, entries, ["MKTA/YI", "MKSTA/Y-"]);
    expect(report.lengthChanged).toEqual([]);
    const longer = pirMutations(original, entries, ["MKKTA/YI", "MKSTA/Y-"]);
    expect(longer.lengthChanged).toEqual(["target"]);
  });

  it("ignora le voci senza originale (nuove o rinominate)", () => {
    expect(pirMutations({}, entries).mutations).toEqual([]);
  });

  it("annulla una mutazione anche dopo che i gap si sono spostati", () => {
    const rows = ["MR-TA/YV", "MKSTA/Y-"];
    const report = pirMutations(original, entries, rows);
    const moved = ["-MRTA/YV", "MKSTA/Y-"];
    expect(revertMutations(moved, [report.mutations[0]])).toEqual(["-MKTA/YV", "MKSTA/Y-"]);
    expect(revertMutations(moved, report.mutations.filter((m) => m.row === 0))).toEqual(["-MKTA/YI", "MKSTA/Y-"]);
    expect(revertMutations(["MK-TA/YI", "MKSTA/Y-"], [report.mutations[0]])).toBeNull();
  });
});
