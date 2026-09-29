import { describe, expect, it } from "vitest";
import { fmtEvalue, fmtNum } from "./format";
import { parsePir, pirIdentities } from "./pir";

const PIR = `>P1;target
sequence:target:::::::0.00: 0.00
MKTA-YIAK
QR*

>P1;1ABC_A
structureX:1ABC_A:1:A:11:A::::
MKSAWYIAK
QR*
`;

describe("parsePir", () => {
  it("legge codici, tipi e sequenze su più righe", () => {
    const entries = parsePir(PIR);
    expect(entries.map((e) => e.code)).toEqual(["target", "1ABC_A"]);
    expect(entries[0].kind).toBe("sequence");
    expect(entries[1].kind).toBe("structureX");
    expect(entries[0].sequence).toBe("MKTA-YIAKQR");
  });

  it("calcola identità e copertura del bersaglio", () => {
    const [ident] = pirIdentities(parsePir(PIR));
    expect(ident.code).toBe("1ABC_A");
    expect(ident.identity).toBeCloseTo(90, 0);
    expect(ident.coverage).toBeCloseTo(100, 0);
  });
});

describe("format", () => {
  it("formatta numeri ed E-value", () => {
    expect(fmtNum(null)).toBe("—");
    expect(fmtEvalue(0)).toBe("0");
    expect(fmtEvalue(2e-30)).toContain("10^");
  });
});
