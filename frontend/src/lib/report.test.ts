import { describe, expect, it } from "vitest";
import { base64Bytes, filenameFromDisposition, REPORT_IMAGE_MAX_BYTES, reportFallbackName, reportImage } from "./report";

const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

describe("immagine del viewer nel report", () => {
  it("byte rappresentati dal base64", () => {
    expect(base64Bytes("")).toBe(0);
    expect(base64Bytes("QQ==")).toBe(1);
    expect(base64Bytes("QUI=")).toBe(2);
    expect(base64Bytes("QUJD")).toBe(3);
    expect(base64Bytes("QUJD\nREVG")).toBe(6);
  });
  it("PNG del viewer inviato così com'è", () => {
    expect(reportImage(PNG)).toEqual({ image: PNG, note: null });
  });
  it("senza viewer aperto nessuna immagine e nessun avviso", () => {
    expect(reportImage(null)).toEqual({ image: null, note: null });
    expect(reportImage(undefined)).toEqual({ image: null, note: null });
    expect(reportImage("")).toEqual({ image: null, note: null });
  });
  it("immagini non PNG o vuote non si inviano", () => {
    for (const uri of ["data:image/jpeg;base64,/9j/4AAQ", "data:image/png;base64,", "iVBORw0KGgo", "data:,"]) {
      const out = reportImage(uri);
      expect(out.image).toBeNull();
      expect(out.note).toMatch(/senza immagine/);
    }
  });
  it("oltre il limite del server il report si genera senza immagine", () => {
    const limit = (REPORT_IMAGE_MAX_BYTES / 3) * 4; // base64 di esattamente 3 MB
    expect(reportImage(`data:image/png;base64,${"A".repeat(limit)}`).image).not.toBeNull();
    const big = reportImage(`data:image/png;base64,${"A".repeat(limit + 4)}`);
    expect(big.image).toBeNull();
    expect(big.note).toMatch(/3 MB/);
  });
});

describe("nome del file del report", () => {
  it("dall'intestazione del server", () => {
    expect(filenameFromDisposition('attachment; filename="report_yeast.B99990001.html"', "x.html")).toBe("report_yeast.B99990001.html");
    expect(filenameFromDisposition("inline; filename=report_a.html", "x.html")).toBe("report_a.html");
    expect(filenameFromDisposition("attachment; filename*=UTF-8''report_%C3%A8.html; filename=\"r.html\"", "x.html")).toBe("report_è.html");
  });
  it("mai un percorso, e un nome di riserva se manca", () => {
    expect(filenameFromDisposition('attachment; filename="../../etc/passwd"', "x.html")).toBe("passwd");
    expect(filenameFromDisposition('attachment; filename="..\\\\a\\\\b.html"', "x.html")).toBe("b.html");
    expect(filenameFromDisposition('attachment; filename=".."', "x.html")).toBe("x.html");
    expect(filenameFromDisposition("attachment", "x.html")).toBe("x.html");
    expect(filenameFromDisposition(null, "x.html")).toBe("x.html");
    expect(filenameFromDisposition("attachment; filename*=UTF-8''%E0%A4%A", "x.html")).toBe("x.html");
  });
  it("nome di riserva come quello del server", () => {
    expect(reportFallbackName("yeast.B99990001.pdb", "abc")).toBe("report_yeast.B99990001.html");
    expect(reportFallbackName("items/0003_seq1/seq1.B99990001.pdb", "abc")).toBe("report_seq1.B99990001.html");
    expect(reportFallbackName('y";a<b>.pdb', "abc")).toBe("report_y_a_b.html");
    expect(reportFallbackName(null, "abc123")).toBe("report_qualita_abc123.html");
  });
});
