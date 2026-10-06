import { describe, expect, it } from "vitest";
import { parsePir } from "./pir";
import { formatHeader, headerErrors, parseHeader, setEntryHeader } from "./pirHeader";

describe("seconda riga PIR", () => {
  it("legge e riscrive i dieci campi", () => {
    const line = "structureX:1ABC_A:5:A:120:A:lisozima:Gallus gallus:1.80:0.19";
    const h = parseHeader(line);
    expect(h).toMatchObject({ kind: "structureX", file: "1ABC_A", startRes: "5", startChain: "A", endRes: "120", endChain: "A", resolution: "1.80", rFactor: "0.19" });
    expect(formatHeader(h)).toBe(line);
    expect(formatHeader(parseHeader("sequence:t:::::::0.00: 0.00"))).toBe("sequence:t:::::::0.00:0.00");
  });

  it("controlla i campi", () => {
    const ok = parseHeader("structureX:1ABC_A:52A:A:FIRST:B::::");
    expect(headerErrors("1ABC_A", ok)).toEqual({});
    const bad = { ...ok, kind: "struct", startRes: "5x5", startChain: "AB", resolution: "alta", name: "a:b" };
    expect(Object.keys(headerErrors("1 ABC", bad)).sort()).toEqual(["code", "kind", "name", "resolution", "startChain", "startRes"]);
  });

  it("sostituisce codice e seconda riga senza toccare il resto", () => {
    const text = "C; nota\n>P1;t\nsequence:t::::::::\nMK*\n\n>P1;x\nstructureX:x:1:A:2:A::::\nMK* coda\n";
    const out = setEntryHeader(text, 1, "1ABC_A", "structureX:1ABC_A:3:B:4:B::::");
    expect(out).toBe("C; nota\n>P1;t\nsequence:t::::::::\nMK*\n\n>P1;1ABC_A\nstructureX:1ABC_A:3:B:4:B::::\nMK* coda\n");
    const missing = setEntryHeader(">P1;n\n>P1;t\nsequence:t::::::::\nMK*\n", 0, "n", "sequence:n::::::::");
    expect(parsePir(missing).map((e) => e.header)).toEqual(["sequence:n::::::::", "sequence:t::::::::"]);
  });
});
