import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AnalysisResult, InteractionEnd } from "../api/types";
import { ToastProvider } from "../lib/context";
import { AnalysisView, sourceFileUrl } from "./StructuralAnalysis";

// Markup statico (react-dom/server) della vista del risultato, senza viewer (url null) né browser.
const render = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter><ToastProvider>{node}</ToastProvider></MemoryRouter>);
const end = (chain: string, resnum: string, resname: string, atom: string): InteractionEnd => ({ chain, resnum, resname, atom });

function result(pairs = true): AnalysisResult {
  return {
    source: { type: "template", id: "UBQD" }, label: "UBQD", source_sha256: "x",
    structure: {
      file: "UBQD.pdb", chains: 2, residues: 152, heavy_atoms: 1204,
      chains_detail: ["A", "B"].map((chain) => ({ chain, first: "1", last: "76", residues: 76, sequence: "", breaks: [], ligands: 0 })),
    },
    interactions: {
      version: 1, hydrogens: "placed", criteria: {},
      summary: { residues: 152, chains: 2, hbonds: 1, hbonds_backbone: 0, hbonds_mixed: 1, hbonds_sidechain: 0, salt_bridges: 0, disulfides: 0, pi_stacking: 0, cation_pi: 0, inter_chain: { hbonds: 1, salt_bridges: 0, disulfides: 0, aromatic: 0 } },
      hbonds: [{ a: end("B", "41", "GLN", "NE2"), b: end("A", "27", "LYS", "O"), distance: 3.04, ha_distance: 2.1, angle: 150.7, donor_angle: null, acceptor_angle: 130, hydrogen: "placed", kind: "mixed", inter_chain: true }],
      salt_bridges: [], disulfides: [], aromatic: [],
    },
    interfaces: {
      version: 1, method: "Shrake-Rupley (Biopython), sonda 1,4 Å, 100 punti", chains: ["A", "B"], min_delta: 1, interface_residues: 2,
      residues: [],
      pairs: pairs ? [{
        chains: ["A", "B"], area: 122.9, buried: 245.8, apolar_fraction: 0.3,
        residues: { A: [{ chain: "A", resnum: "60", resname: "ASN", aa: "N", delta: 64.8, apolar_delta: 20 }], B: [{ chain: "B", resnum: "60", resname: "ASN", aa: "N", delta: 67.9, apolar_delta: 20 }] },
        counts: { A: 1, B: 1 }, composition: { counts: { apolari: 0, polari: 2, carichi: 0, altri: 0 }, percent: { apolari: 0, polari: 100, carichi: 0, altri: 0 } },
        interactions: { hbonds: 1, salt_bridges: 0, disulfides: 0, pi_stacking: 0, cation_pi: 0 },
        plausibility: { level: "bassa", text: "area sotto 400 Å²", limits: "Indicatore basato sulla sola area", thresholds: { low: 400, high: 600 } },
      }] : [],
    },
    conservation: null, warnings: ["La struttura ha 2 modelli: analizzato il primo."],
  };
}

describe("vista dell'analisi strutturale", () => {
  it("tabella dei legami idrogeno, interfaccia e avvisi", () => {
    const html = render(<AnalysisView result={result()} url={null} />);
    expect(html).toContain("GLN B41 NE2");
    expect(html).toContain("LYS A27 O");
    expect(html).toContain("fra catene");
    expect(html).toContain("Legami idrogeno (1)");
    expect(html).toContain("plausibilità bassa");
    expect(html).toContain("Catene A – B");
    expect(html).toContain("1 legami H, 0 ponti salini");
    expect(html).toContain("La struttura ha 2 modelli");
    expect(html).toContain("Struttura non disponibile per la visualizzazione 3D.");
    expect(html).not.toContain("Conservazione</h3>");
  });

  it("strutture con più catene che non si toccano: nessuna interfaccia", () => {
    const html = render(<AnalysisView result={result(false)} url={null} />);
    expect(html).toContain("Nessuna coppia di catene si tocca");
  });

  it("file della struttura per il viewer", () => {
    expect(sourceFileUrl({ type: "template", id: "1UBQ" })).toBe("/api/templates/1UBQ/file");
    expect(sourceFileUrl({ type: "job", job_id: "j1", file: "items/0001_a/a.pdb" })).toBe("/api/jobs/j1/files/items/0001_a/a.pdb");
    expect(sourceFileUrl({ type: "upload", file: "x.pdb" })).toBe("/api/files/uploads/x.pdb");
    expect(sourceFileUrl({ type: "job" })).toBeNull();
  });
});
