import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AlaScanResult, MutateResult, MutationDope, MutationSite, QualitySummary } from "../api/types";
import { ToastProvider } from "../lib/context";
import { AlaScanResults, MutationResults } from "./Mutagenesis";

// Markup statico (react-dom/server): risultati di un job `mutate` (I30P su 1UBQ, valori del job reale) e di una
// scansione di alanina, senza browser.
const render = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter><ToastProvider>{node}</ToastProvider></MemoryRouter>);

const SITE: MutationSite = {
  site: "A:30", label: "A:I30P", wt: "I", mut: "P", rsa_wt: 0.042, rsa_mut: 0.061, core: true,
  buried_wt: { total: 163.4, apolar: 137.7 }, buried_mut: { total: 123.9, apolar: 91.8 }, buried_delta: { total: -39.5, apolar: -45.9 },
  ss: "H", ss_position: 7, ss_length: 12, rama: { phi: -65.1, psi: -40, wt: "favored", mut: "favored" }, blosum62: -3, helix_propensity: 2.75,
  interactions: { wild_type: 1, mutant: 0, gained: [], lost: [{ type: "hbond", partner: "VAL A26", role: "NH della catena principale", atom: "N", inter_chain: false, distance: 2.97 }] },
  clashes: { wild_type: 0, mutant: 1, worst: 0.42 }, interface: null,
};
const DOPE: MutationDope = {
  wild_type: -7918, mutant: -7612.5, delta: 305.4, local_wild_type: -5576.3, local_mutant: -5270.9, delta_local: 305.4, local_residues: 32,
  local_radius: 8, per_residue: 104.18, delta_eq: 2.93, interchain_wild_type: null, interchain_mutant: null, interchain_delta: null,
  interchain_delta_eq: null, spread: { wild_type: 51.4, mutant: 19.8 },
};
const Q = { grade: "A", grade_score: 98.9, rama_favored: 95.9, rama_outliers: 0, clashscore: 1.66, helix_pct: 23.7, strand_pct: 34.2, dope: -7918, zdope: -2.67, residues: 76 } as QualitySummary;

const RESULT: MutateResult = {
  version: 1, source: { type: "template", id: "1UBQ" }, label: "1UBQ", source_sha256: "x",
  mutations: [{ chain: "A", resnum: "30", wt: "I", mut: "P", label: "A:I30P", wt_name: "ILE", mut_name: "PRO" }], notation: "A:I30P",
  files: { wild_type: "1UBQ_wt.pdb", mutant: "1UBQ_I30P.pdb" },
  protocol: { method: "mutate_model.py della Sali lab", radius: 5, local_radius: 8, models: 3, seeds: [-49837, -49838, -49839], randomize: 4, selection: "modello con il DOPE più basso per variante", wild_type: "selvatico riottimizzato", energy: false, core_rsa: 0.2, thresholds: {} },
  models: [{ variant: "wt", role: "wild_type", seed: -49837, dope: -7918, local: -5576.3, interchain: null, chosen: true, moved_atoms: 69 }],
  dope: DOPE, energy: null, sites: [SITE],
  assessment: {
    code: "destabilizing", label: "probabilmente destabilizzante", summary: "Probabilmente destabilizzante: 2 segnali forti.", score: 242.93, binding: null,
    reasons: ["Prolina in un'α-elica oltre il primo giro (posizione 8 di 12)"],
    signals: [{ level: "strong", code: "proline_helix", text: "Prolina in un'α-elica oltre il primo giro (posizione 8 di 12)" }, { level: "moderate", code: "steric_clash", text: "1 contatto sterico grave" }],
  },
  quality: { wild_type: Q, mutant: { ...Q, clashscore: 3.33 } },
  comparison: [{ key: "clashscore", label: "Clashscore", before: 1.66, after: 3.33, delta: 1.67, verdict: "peggio", higher_is_better: false }],
  rmsd: { outside: 0, outside_atoms: 236, all: 0.035, atoms: 304 }, residues: 76, warnings: ["Gruppi eteroatomici esclusi dal calcolo: ZN A101."],
  caveat: "Stima qualitativa: non è un ΔΔG sperimentale.",
};

describe("risultati della mutagenesi", () => {
  it("classe con le motivazioni pesate, DOPE, siti, confronto, link e avvertenza", () => {
    const html = render(<MutationResults jobId="j1" result={RESULT} />);
    expect(html).toContain("probabilmente destabilizzante");
    expect(html).toContain("+305 (+2,93 residui equivalenti)");
    expect(html).toContain(">forte</span>");
    expect(html).toContain("Prolina in un&#x27;α-elica oltre il primo giro");
    expect(html).toContain("α-elica (8 di 12) · sepolto (RSA 4%) · BLOSUM62 −3");
    expect(html).toContain("VAL A26 (legame H, NH della catena principale)");
    expect(html).toContain("Stima qualitativa: non è un ΔΔG sperimentale.");
    expect(html).toContain("Gruppi eteroatomici esclusi dal calcolo: ZN A101.");
    expect(html).toContain('href="/quality?job=j1&amp;file=1UBQ_I30P.pdb"');
    expect(html).toContain("Confronta con il selvatico");
    expect(html).toContain("non calcolata"); // energia
    expect(html).not.toContain("Rifinito (OpenMM)");
  });

  it("scansione: conteggi, righe nell'ordine del server, esclusi", () => {
    const row = (label: string, resnum: string, code: "destabilizing" | "neutral", delta: number) => ({
      mutation: { chain: "A", resnum, wt: "L", mut: "A", label, wt_name: "LEU", mut_name: "ALA" }, label, site: { ...SITE, label, site: `A:${resnum}` },
      dope: { ...DOPE, delta, delta_eq: delta / 104 }, energy: null, files: { wild_type: "w.pdb", mutant: "m.pdb" },
      assessment: { code, label: code === "neutral" ? "probabilmente neutra" : "probabilmente destabilizzante", summary: "", score: 0, binding: null, reasons: [`motivo ${label}`], signals: [] },
    });
    const scan = {
      version: 1, source: { type: "template", id: "1UBQ" }, label: "1UBQ", source_sha256: "x",
      selection: { mode: "residues", detail: "3 residui indicati" }, protocol: RESULT.protocol,
      rows: [row("A:L43A", "43", "destabilizing", 308.4), row("A:K48A", "48", "neutral", 10.4)],
      skipped: [{ chain: "A", resnum: "10", resname: "GLY", reason: "glicina: esclusa" }], failed: [],
      summary: { residues: 2, skipped: 1, counts: { destabilizing: 1, uncertain: 0, neutral: 1 }, hotspots: 0 }, hotspots: [], interface: false,
      residues: 76, csv: "alascan.csv", warnings: [], caveat: "Stima qualitativa.",
    } as AlaScanResult;
    const html = render(<AlaScanResults jobId="j2" result={scan} />);
    expect(html.indexOf("A:L43A")).toBeLessThan(html.indexOf("A:K48A"));
    expect(html).toContain("3 residui indicati; 1 esclusi");
    expect(html).toContain("motivo A:L43A");
    expect(html).toContain("Esclusi: GLY A10 (glicina)");
    expect(html).toContain("struttura con una sola catena");
    expect(html).toContain('href="/api/jobs/j2/files/alascan.csv?download=true"');
  });
});
