import { describe, expect, it } from "vitest";
import { parsePir } from "./pir";
import { deleteGaps, emptyHistory, insertGaps, isRagged, record, redo, removeGapColumns, rewritePir, slideBlock, squareUp, undo } from "./pirEdit";

const sel = (r0: number, c0: number, r1 = r0, c1 = c0) => ({ r0, r1, c0, c1 });
const letters = (s: string) => s.replace(/[^A-Z]/g, "");
const lines = (...l: string[]) => l.join("\n");

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
    const rows = insertGaps(["MKTAYIAK", "MKSAYIAK"], sel(0, 3, 1, 3), 3, 2)!;
    expect(rows.map(letters)).toEqual(["MKTAYIAK", "MKSAYIAK"]);
    expect(isRagged(rows)).toBe(false);
  });

  it("null se non cambia nulla: gap in coda, gap da togliere in coda", () => {
    expect(insertGaps(["MKTA--", "MKTAYI"], sel(0, 5), 5)).toBeNull();
    expect(deleteGaps(["MKTA--", "MKTAYI"], sel(0, 5), 5)).toBeNull();
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

  it("null se il blocco è fatto di soli gap in coda", () => {
    expect(slideBlock(["MK--", "MKTA"], sel(0, 3), 1)).toBeNull();
  });
});

describe("removeGapColumns", () => {
  it("toglie le colonne di soli gap, anche interne", () => {
    expect(removeGapColumns(["A-B", "C-D"])).toEqual(["AB", "CD"]);
    expect(removeGapColumns(["AB", "CD"])).toBeNull();
  });
});

describe("rewritePir", () => {
  const text = lines(">P1;t", "sequence:t:::::::0.00: 0.00", "MK-A/YI*", "", ">P1;1ABC_A", "structureX:1ABC_A:1:A:6:A::::", "MKSA/Y-*", "");

  it("sostituisce le sequenze, lascia le seconde righe e si rilegge uguale", () => {
    const entries = parsePir(text);
    const out = rewritePir(text, ["MKA-/YI", "MKSA/Y-"]);
    expect(parsePir(out).map((e) => e.sequence)).toEqual(["MKA-/YI", "MKSA/Y-"]);
    expect(parsePir(out).map((e) => e.header)).toEqual(entries.map((e) => e.header));
    expect(rewritePir(text, entries.map((e) => e.sequence))).toBe(text);
  });

  it("conserva commenti, testo dopo l'asterisco, minuscole e a capo CRLF", () => {
    const src = ["C; allineamento di prova", ">P1;t", "sequence:t::::::::", "MKw-AY* nota", "R; riga dopo", "", ">P1;s", "structureX:s:1:A:5:A::::", "MKWAY-*", ""];
    const out = rewritePir(src.join("\r\n"), ["MKW-AF", "MKWA-Y"]);
    const expected = src.slice();
    expected[3] = "MKw-AF* nota";
    expected[8] = "MKWA-Y*";
    expect(out).toBe(expected.join("\r\n"));
  });

  it("va a capo come il testo originale, 75 caratteri se la sequenza era su una riga", () => {
    const one = rewritePir(lines(">P1;t", "sequence:t::::::::", "A*", ""), ["A".repeat(80)]);
    expect(one.split("\n")[2]).toHaveLength(75);
    const wrapped = rewritePir(lines(">P1;t", "sequence:t::::::::", "AAAAAAAAAA", "AAAAA*", ""), ["AAAAAAAAAAAAAAA-"]);
    expect(wrapped.split("\n").slice(2, 4)).toEqual(["AAAAAAAAAA", "AAAAA-*"]);
  });

  it("una voce senza seconda riga ne riceve una minima, senza perdere la sequenza", () => {
    const out = rewritePir(lines(">P1;new", ">P1;t", "sequence:t::::::::", "MK*", ""), ["--", "MK"]);
    const entries = parsePir(out);
    expect(entries.map((e) => [e.code, e.header, e.sequence])).toEqual([["new", "sequence:new::::::::", "--"], ["t", "sequence:t::::::::", "MK"]]);
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
