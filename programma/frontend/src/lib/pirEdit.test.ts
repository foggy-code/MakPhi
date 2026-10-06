import { describe, expect, it } from "vitest";
import { parsePir } from "./pir";
import { deleteGaps, emptyHistory, insertGaps, isRagged, pirResidueNumber, record, redo, removeGapColumns, serializePir, slideBlock, squareUp, undo } from "./pirEdit";

const sel = (r0: number, c0: number, r1 = r0, c1 = c0) => ({ r0, r1, c0, c1 });
const letters = (s: string) => s.replace(/[^A-Z]/g, "");

describe("squareUp", () => {
  it("pareggia con gap in coda e toglie le colonne finali di soli gap", () => {
    expect(squareUp(["ABC", "A"])).toEqual(["ABC", "A--"]);
    expect(squareUp(["AB--", "A---"])).toEqual(["AB", "A-"]);
  });
});

describe("insertGaps / deleteGaps", () => {
  it("inserisce un gap e sposta a destra il resto della riga", () => {
    expect(insertGaps(["ABCD", "ABCD"], sel(0, 1), 1)).toEqual(["A-BCD", "ABCD-"]);
  });

  it("inserisce nelle righe selezionate senza allargare se la coda è libera", () => {
    expect(insertGaps(["ABC-", "AB--"], sel(0, 0, 1, 0), 0)).toEqual(["-ABC", "-AB-"]);
  });

  it("toglie solo gap, mai residui", () => {
    expect(deleteGaps(["A-BC", "ABCD"], sel(0, 1, 1, 1), 1)).toEqual(["ABC-", "ABCD"]);
    expect(deleteGaps(["ABCD"], sel(0, 1), 1)).toBeNull();
  });

  it("i residui restano nello stesso ordine", () => {
    const rows = insertGaps(["MKTAYIAK", "MKSAYIAK"], sel(0, 3, 1, 3), 3, 2);
    expect(rows.map(letters)).toEqual(["MKTAYIAK", "MKSAYIAK"]);
    expect(isRagged(rows)).toBe(false);
  });
});

describe("slideBlock", () => {
  it("sposta il blocco a sinistra sopra un gap", () => {
    expect(slideBlock(["A-BC", "XYZW"], sel(0, 2, 0, 3), -1)).toEqual(["ABC-", "XYZW"]);
  });

  it("sposta a destra anche oltre la fine, allargando l'allineamento", () => {
    expect(slideBlock(["AB", "XY"], sel(0, 1), 1)).toEqual(["A-B", "XY-"]);
  });

  it("non si muove contro un residuo o il bordo", () => {
    expect(slideBlock(["ABC"], sel(0, 1), -1)).toBeNull();
    expect(slideBlock(["ABC"], sel(0, 0), -1)).toBeNull();
    expect(slideBlock(["A-C", "ABC"], sel(0, 2, 1, 2), -1)).toBeNull();
  });
});

describe("removeGapColumns", () => {
  it("toglie le colonne di soli gap, anche interne", () => {
    expect(removeGapColumns(["A-B", "C-D"])).toEqual(["AB", "CD"]);
    expect(removeGapColumns(["AB", "CD"])).toBeNull();
  });
});

describe("serializePir", () => {
  it("riscrive il PIR con le intestazioni invariate e lo rilegge uguale", () => {
    const text = ">P1;t\nsequence:t:::::::0.00: 0.00\nMK-A/YI*\n\n>P1;1ABC_A\nstructureX:1ABC_A:1:A:6:A::::\nMKSA/Y-*\n";
    const entries = parsePir(text);
    const out = serializePir(entries, ["MKA-/YI", "MKSA/Y-"]);
    expect(parsePir(out).map((e) => e.sequence)).toEqual(["MKA-/YI", "MKSA/Y-"]);
    expect(parsePir(out).map((e) => e.header)).toEqual(entries.map((e) => e.header));
  });

  it("va a capo ogni 75 caratteri", () => {
    const out = serializePir([{ code: "t", kind: "sequence", header: "sequence:t::::::::", sequence: "A".repeat(80) }]);
    expect(out.split("\n")[2]).toHaveLength(75);
  });
});

describe("pirResidueNumber", () => {
  it("conta le lettere, non gap e separatori", () => {
    expect(pirResidueNumber("A-B/C", 4)).toBe(3);
    expect(pirResidueNumber("A-B/C", 1)).toBeNull();
    expect(pirResidueNumber("A-B/C", 3)).toBeNull();
  });
});

describe("cronologia", () => {
  it("annulla e ripristina", () => {
    let h = record(emptyHistory(), "a");
    h = record(h, "b");
    const u = undo(h, "c")!;
    expect(u.value).toBe("b");
    const r = redo(u.history, u.value)!;
    expect(r.value).toBe("c");
    expect(undo(emptyHistory(), "x")).toBeNull();
  });

  it("una nuova modifica cancella il ramo da ripristinare", () => {
    const u = undo(record(emptyHistory(), "a"), "b")!;
    expect(record(u.history, "a").future).toEqual([]);
  });
});
