import { describe, expect, it } from "vitest";
import type { MutationSite, Pocket, QualityReport } from "../api/types";
import { describeResidue, residueColoring, SS_SCALE } from "./residueColoring";
import { pocketDetail, siteDetail } from "./toolsView";

// Termini unici fra 3D, strisce, tabelle e dettagli: stato DSSP E "filamento β" (come DSSP e il backend), regioni di
// Ramachandran favorita/consentita/outlier; valori mancanti o non numerici come "—".
describe("termini della struttura secondaria e di Ramachandran", () => {
  const report = {
    residues: [
      { chain: "A", resnum: "1", ss3: "E", rama: "outlier" },
      { chain: "A", resnum: "2", ss3: "H", rama: "allowed" },
    ],
  } as unknown as QualityReport;

  it("legenda e suggerimenti della colorazione per struttura secondaria", () => {
    expect(SS_SCALE.categories.find((c) => c.key === "E")?.label).toBe("Filamento β");
    const ss = residueColoring(report, "ss")!;
    expect(describeResidue(ss, { chain: "A", resnum: "1" })).toBe("filamento β");
    expect(describeResidue(ss, { chain: "A", resnum: "2" })).toBe("elica");
    const rama = residueColoring(report, "rama")!;
    expect(describeResidue(rama, { chain: "A", resnum: "2" })).toBe("consentita");
  });

  it("dettaglio di un sito mutato: regioni con le stesse parole", () => {
    const site = {
      rsa_wt: 0.5, rsa_mut: 0.5, core: false, buried_wt: { total: 1, apolar: 1 }, buried_mut: { total: 1, apolar: 1 }, buried_delta: { total: 0, apolar: 0 },
      ss: "E", ss_position: 0, ss_length: 1, rama: { phi: -120, psi: 130, wt: "allowed", mut: "outlier" }, blosum62: 0, helix_propensity: null,
      interactions: { wild_type: 0, mutant: 0, gained: [], lost: [] }, clashes: { wild_type: 0, mutant: 0, worst: null }, interface: null,
    } as unknown as MutationSite;
    const d = Object.fromEntries(siteDetail(site).map((x) => [x.label, x.value]));
    expect(d["Ramachandran (φ, ψ)"]).toBe("-120°, 130° · consentita → outlier");
  });

  it("descrittori mancanti o non numerici della tasca: un solo trattino, senza unità", () => {
    const pocket = { id: 1, rank: 1, volume: Number.NaN, sasa_total: null, center: null, atoms: 3, residues: [], other: {} } as unknown as Pocket;
    const d = Object.fromEntries(pocketDetail(pocket).map((x) => [x.label, x.value]));
    expect(d["Volume"]).toBe("—");
    expect(d["SASA totale"]).toBe("—");
  });
});
