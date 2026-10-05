import { describe, expect, it } from "vitest";
import { dataUriToBlob, viewerPngName } from "./viewerImage";

describe("nome del PNG della vista 3D", () => {
  it("dal file del job, senza estensione della struttura", () => {
    expect(viewerPngName("/api/jobs/abc/files/models/target.B99990001.pdb")).toBe("target.B99990001_3d.png");
    expect(viewerPngName("/api/jobs/abc/files/refined.cif?download=true")).toBe("refined_3d.png");
    expect(viewerPngName("/api/jobs/abc/files/predetta.pdb.gz")).toBe("predetta_3d.png");
  });
  it("dai template prende l'id; caratteri codificati decodificati e resi sicuri", () => {
    expect(viewerPngName("/api/templates/1ABC_A/file")).toBe("1ABC_A_3d.png");
    expect(viewerPngName("/api/jobs/abc/files/modello%20finale.pdb")).toBe("modello_finale_3d.png");
    expect(viewerPngName("/api/jobs/abc/files/rotto%E0.pdb")).toBe("rotto_E0_3d.png");
  });
  it("senza URL un nome generico", () => {
    expect(viewerPngName(null)).toBe("struttura_3d.png");
    expect(viewerPngName("")).toBe("struttura_3d.png");
  });
});

describe("data URI in Blob", () => {
  it("decodifica il base64 con il tipo", async () => {
    const blob = dataUriToBlob("data:image/png;base64,iVBORw0KGgo=");
    expect(blob.type).toBe("image/png");
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  });
  it("rifiuta ciò che non è un data URI in base64", () => {
    expect(() => dataUriToBlob("data:,ciao")).toThrow();
    expect(() => dataUriToBlob("/img.png")).toThrow();
  });
});
