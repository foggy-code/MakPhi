import { describe, expect, it } from "vitest";
import { safeFilename, toCsv } from "./exportImage";

describe("esportazione dei grafici", () => {
  it("safeFilename: niente accenti, spazi, percorsi o caratteri riservati", () => {
    expect(safeFilename("Ramachandran · 3IU0 (generale)", "png")).toBe("Ramachandran_3IU0_generale.png");
    expect(safeFilename("../../etc/passwd", ".csv")).toBe("etc_passwd.csv");
    expect(safeFilename("qualità: φ/ψ", "png")).toBe("qualita.png");
    expect(safeFilename("", "png")).toBe("grafico.png");
    expect(safeFilename("a".repeat(300), "png")).toHaveLength(124);
  });
  it("toCsv: intestazione, virgolette, vuoti, numeri col punto, CRLF", () => {
    const csv = toCsv({
      columns: [{ key: "res", label: "Residuo" }, { key: "v", label: "Valore, Å", num: true }, { key: "note", label: "Nota" }],
      rows: [
        { res: "ALA A12", v: -1.25, note: 'dice "sì"' },
        { res: "GLY A13", v: null, note: undefined },
        { res: "-3", v: Number.NaN, note: "=SOMMA(A1)" },
      ],
    });
    expect(csv).toBe('Residuo,"Valore, Å",Nota\r\nALA A12,-1.25,"dice ""sì"""\r\nGLY A13,,\r\n-3,,\'=SOMMA(A1)\r\n');
  });
});
