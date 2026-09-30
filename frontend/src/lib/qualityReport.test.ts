import { describe, expect, it } from "vitest";
import type { ConsensusResult, LigandGroupCheck, QualityReport, ResidueRow, StructureSource } from "../api/types";
import { alignedPerResidue, clashHighlight, isProblemResidue, ligandHighlight, problemResidues, residuesHighlight, shownReportSource } from "./qualityReport";

function row(resnum: number, extra: Partial<ResidueRow> = {}): ResidueRow {
  return {
    chain: "A", resnum: String(resnum), resname: "ALA", aa: "A", ss: "H", ss3: "H", rsa: 0.3, bfactor: 20, phi: -60, psi: -45,
    rama: "favored", clashes: 0, dope: -0.04, ...extra,
  };
}

const consensus = (counts: number[]) => ({ per_residue: { counts, flags: counts.map(() => []), signals: [], values: {}, dope_threshold: null } }) as unknown as ConsensusResult;

describe("residui problematici", () => {
  // nessun outlier né clash, ma 2–4 segnalati da DOPE e QMEAN
  const residues = [row(1), row(2), row(3), row(4), row(5, { rama: "outlier" }), row(6, { clashes: 1 })];
  const report = { residues } as Pick<QualityReport, "residues">;
  it("stessa definizione per la tabella e per il 3D: outlier, clash o almeno 2 metodi del consenso", () => {
    const c = consensus([0, 2, 3, 2, 1, 0]);
    expect(problemResidues(report, c).map((r) => r.resnum)).toEqual(["2", "3", "4", "5", "6"]);
    const per = alignedPerResidue(report, c);
    expect(residues.map((r, i) => isProblemResidue(r, i, per))).toEqual([false, true, true, true, true, true]);
  });
  it("senza consenso, o con un consenso di un'altra struttura, solo outlier e clash", () => {
    expect(problemResidues(report, null).map((r) => r.resnum)).toEqual(["5", "6"]);
    expect(alignedPerResidue(report, consensus([2, 2]))).toBeNull();
    expect(problemResidues(report, consensus([2, 2])).map((r) => r.resnum)).toEqual(["5", "6"]);
  });
});

describe("struttura del rapporto mostrato", () => {
  const model: StructureSource = { type: "job", job_id: "j1", file: "model.pdb" };
  it("rapporto salvato di un modello: la sua struttura, non quella scelta nel selettore", () => {
    expect(shownReportSource(null, undefined, { source: model })).toEqual(model);
    expect(shownReportSource(null, undefined, null)).toBeUndefined();
  });
  it("job di qualità: la struttura analizzata dal job", () => {
    const template: StructureSource = { type: "template", id: "1UBQ" };
    expect(shownReportSource("q1", template, { source: model })).toEqual(template);
    expect(shownReportSource("q1", undefined, null)).toBeUndefined();
  });
});

describe("evidenza nel 3D", () => {
  // Zn di ubq_zn.pdb coordinato da His68 (valori del backend)
  const zinc = {
    resname: "ZN", chain: "A", resnum: "101", atoms: 1, class: "metal", label: "metallo", status: "da controllare", summary: "", reasons: [],
    contacts: [], clashes: [], links: [],
    metals: [{
      atom: "ZN", element: "ZN", symbol: "Zn", coord: [20.137, 35.131, 16.359], coordination_number: 1, geometry: null, cn_range: [4, 6], reference: true,
      donors: [{ chain: "A", resnum: "68", resname: "HIS", atom: "NE2", protein: true, label: "His A68 NE2", category: "N_his", donor: "N (His)", distance: 2.1,
        expected: 2.03, status: "nella norma", water: false, same_group: false, coord: [22.045, 35.455, 17.173] }],
    }],
  } as unknown as LigandGroupCheck;

  it("solo la scelta di un ligando tiene linee e zoom sul sito; scontri e residui problematici lo tolgono", () => {
    const site = ligandHighlight(zinc);
    expect(site.ligand).toBe(zinc);
    expect(site.lines).toHaveLength(1);
    expect(site.focus).toEqual([{ chain: "A", resi: 101 }, { chain: "A", resi: 68 }]);
    // clic su una riga dei contatti sterici dopo il ligando: i due residui, senza linee né ligando
    expect(clashHighlight({ atom1: "A 43 LEU CB", atom2: "A 50 LEU CD1" })).toEqual({
      focus: [{ chain: "A", resi: 43 }, { chain: "A", resi: 50 }], selected: { chain: "A", resnum: "43" }, ligand: null, lines: [],
    });
    // «Evidenzia tutti i residui problematici»: il residuo scelto resta, il ligando no
    const problems = residuesHighlight([{ chain: "A", resi: 5 }, { chain: "A", resi: 9 }], { chain: "A", resnum: "5" });
    expect(problems).toEqual({ focus: [{ chain: "A", resi: 5 }, { chain: "A", resi: 9 }], selected: { chain: "A", resnum: "5" }, ligand: null, lines: [] });
    expect(residuesHighlight([])).toEqual({ focus: [], selected: null, ligand: null, lines: [] });
  });
});
