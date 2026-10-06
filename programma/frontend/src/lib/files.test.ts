import { describe, expect, it } from "vitest";
import { decodeText, extractModellerKey, fileStem, formatRestraints, parseRestraints, recordsToFasta } from "./files";

describe("decodeText", () => {
  it("riconosce BOM UTF-8 e UTF-16 e normalizza i fine riga", () => {
    expect(decodeText(new Uint8Array([0xef, 0xbb, 0xbf, 0x3e, 0x61, 0x0d, 0x0a, 0x4d]))).toBe(">a\nM");
    const utf16 = new Uint8Array([0xff, 0xfe, 0x3e, 0x00, 0x61, 0x00, 0x0d, 0x00, 0x0a, 0x00]);
    expect(decodeText(utf16)).toBe(">a\n");
  });
  it("ripiega su Windows-1252 e rifiuta i file binari", () => {
    expect(decodeText(new Uint8Array([0x63, 0x61, 0x66, 0xe8]))).toBe("cafè");
    expect(() => decodeText(new Uint8Array([0x00, 0x01, 0x02]))).toThrow(/binario/);
  });
});

describe("extractModellerKey", () => {
  it("legge config.py, variabili d'ambiente, email e file con la sola chiave", () => {
    expect(extractModellerKey("install_dir = r'/usr'\nlicense = r'MODELABCDE'\n")).toBe("MODELABCDE");
    expect(extractModellerKey("MODELLER_KEY=MODELXYZ12\n")).toBe("MODELXYZ12");
    expect(extractModellerKey("Dear user,\nyour license key is: MODELQWERTY\nregards")).toBe("MODELQWERTY");
    expect(extractModellerKey("  MODELSOLO  \n")).toBe("MODELSOLO");
    expect(extractModellerKey("license = r'XXXX'")).toBeNull();
    expect(extractModellerKey("testo qualunque senza chiave")).toBeNull();
  });
});

describe("vincoli da file", () => {
  it("legge i tre tipi, i nomi italiani, CSV e commenti", () => {
    const { restraints, errors } = parseRestraints(
      "tipo,inizio,fine\nhelix 10:a 20:a\nfoglietto;30:A;35:A # commento\ndistanza CA:10:A CA:50:A 8.5\n\nboh 1 2\n");
    expect(errors).toEqual(["Riga 6: tipo \"boh\" sconosciuto (usa helix, strand o distance)."]);
    expect(restraints).toEqual([
      { type: "helix", start: "10:A", end: "20:A" },
      { type: "strand", start: "30:A", end: "35:A" },
      { type: "distance", atom1: "CA:10:A", atom2: "CA:50:A", mean: 8.5, stdev: 0.5 },
    ]);
  });
  it("esporta e rilegge gli stessi vincoli", () => {
    const rows = [{ type: "helix" as const, start: "1:A", end: "9:A" }, { type: "distance" as const, atom1: "CA:2:A", atom2: "CA:8:B", mean: 6, stdev: 1 }];
    expect(parseRestraints(formatRestraints(rows)).restraints).toEqual(rows);
  });
  it("segnala righe incomplete", () => {
    expect(parseRestraints("helix 10:A\ndistance CA:1:A CA:2:A x").errors).toHaveLength(2);
  });
});

describe("recordsToFasta e fileStem", () => {
  it("rende univoci i nomi e spezza le righe", () => {
    const fasta = recordsToFasta([{ name: "a b", sequence: "M".repeat(65) }, { name: "a b", sequence: "MK" }]);
    expect(fasta).toBe(`>a_b\n${"M".repeat(60)}\nMMMMM\n>a_b_2\nMK\n`);
  });
  it("ricava un nome pulito dal file", () => {
    expect(fileStem("mia proteina.fasta.gz")).toBe("mia_proteina");
    expect(fileStem("allineamento.ali")).toBe("allineamento");
  });
});
