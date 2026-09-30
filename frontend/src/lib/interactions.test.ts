import { describe, expect, it } from "vitest";
import type { AnalysisResult, ConservationResult, InterfacePair, InterfacesResult, ParsedFile } from "../api/types";
import { colorFor, legendFor, NO_VALUE_COLOR } from "./colorscale";
import {
  analysisColoring, analysisColorOptions, chainFilterOptions, CONSERVATION_SCALE, conservationColoring, endLabel, endMatches, fastaFromParsed, filterRows,
  followedAnalysis, highlightFor, initialPanelState, interactionCounts, interactionRows, INTERFACE_SCALE, interfaceColoring, interfaceHighlight, msaRequest,
  parseResidueQuery, residueFilterText,
} from "./interactions";
import { coloringMap, describeResidue, hasMissing } from "./residueColoring";

const end = (chain: string, resnum: string, resname: string, atom: string) => ({ chain, resnum, resname, atom });

// valori dei test del backend: K27–D52 a 2,90 Å su 1UBQ; interfaccia di ubq_dimer.pdb su 57, 58, 60, 62
const PAIR: InterfacePair = {
  chains: ["A", "B"], area: 122.9, buried: 245.8, apolar_fraction: 0.3,
  residues: {
    A: [{ chain: "A", resnum: "57", resname: "SER", aa: "S", delta: 37.3, apolar_delta: 10 }, { chain: "A", resnum: "60", resname: "ASN", aa: "N", delta: 64.8, apolar_delta: 20 }],
    B: [{ chain: "B", resnum: "57", resname: "SER", aa: "S", delta: 37.9, apolar_delta: 10 }],
  },
  counts: { A: 2, B: 1 },
  composition: { counts: { apolari: 0, polari: 3, carichi: 0, altri: 0 }, percent: { apolari: 0, polari: 100, carichi: 0, altri: 0 } },
  interactions: { hbonds: 0, salt_bridges: 0, disulfides: 0, pi_stacking: 0, cation_pi: 0 },
  plausibility: { level: "bassa", text: "area sotto 400 Å²", limits: "…", thresholds: { low: 400, high: 600 } },
};

const IFACE: InterfacesResult = {
  version: 1, method: "Shrake-Rupley", chains: ["A", "B"], min_delta: 1, pairs: [PAIR], interface_residues: 3,
  residues: [
    { chain: "A", resnum: "56", resname: "LEU", aa: "L", delta: 0.4, apolar_delta: 0.4 },
    { chain: "A", resnum: "57", resname: "SER", aa: "S", delta: 37.3, apolar_delta: 10 },
    { chain: "A", resnum: "60", resname: "ASN", aa: "N", delta: 64.8, apolar_delta: 20 },
    { chain: "A", resnum: "62", resname: "GLN", aa: "Q", delta: 2.1, apolar_delta: 1 },
    { chain: "B", resnum: "57", resname: "SER", aa: "S", delta: 37.9, apolar_delta: 10 },
  ],
};

const CONS: ConservationResult = {
  version: 1, source: "test", sequences: 12, columns: 76, method: "…", gap_cutoff: 0.3,
  chains: [{ chain: "A", row: "s0", identity: 100, coverage: 100, residues: 3, mapped: 2, scored: 1, mean: 1 }], unmatched: [], warnings: [],
  residues: [
    { chain: "A", resnum: "1", aa: "M", column: 1, row_aa: "M", score: 1, entropy: 0, gap_fraction: 0 },
    { chain: "A", resnum: "20", aa: "S", column: 20, row_aa: "T", score: null, entropy: null, gap_fraction: 0.5 },
    { chain: "A", resnum: "21", aa: "D", column: null, row_aa: null, score: null, entropy: null, gap_fraction: null },
  ],
};

function result(extra: Partial<AnalysisResult> = {}): AnalysisResult {
  return {
    source: { type: "template", id: "1UBQ" }, label: "1UBQ", source_sha256: "x",
    structure: { file: "1UBQ.pdb", chains: 2, residues: 152, heavy_atoms: 1204, chains_detail: [] },
    interactions: {
      version: 1, hydrogens: "placed", criteria: {},
      summary: { residues: 152, chains: 2, hbonds: 3, hbonds_backbone: 1, hbonds_mixed: 1, hbonds_sidechain: 1, salt_bridges: 1, disulfides: 1, pi_stacking: 1, cation_pi: 1, inter_chain: { hbonds: 1, salt_bridges: 0, disulfides: 1, aromatic: 1 } },
      hbonds: [
        { a: end("A", "27", "ALA", "N"), b: end("A", "23", "ILE", "O"), distance: 3.0, ha_distance: 2.02, angle: 165.9, donor_angle: null, acceptor_angle: 156.9, hydrogen: "placed", kind: "backbone", inter_chain: false },
        { a: end("A", "27", "LYS", "NZ"), b: end("A", "52", "ASP", "OD2"), distance: 2.9, ha_distance: null, angle: null, donor_angle: 92.8, acceptor_angle: 120, hydrogen: null, kind: "sidechain", inter_chain: false },
        { a: end("B", "41", "GLN", "NE2"), b: end("A", "27", "LYS", "O"), distance: 3.04, ha_distance: 2.1, angle: 150.7, donor_angle: null, acceptor_angle: 130, hydrogen: "explicit", kind: "mixed", inter_chain: true },
      ],
      salt_bridges: [{ a: end("A", "27", "LYS", "NZ"), b: end("A", "52", "ASP", "OD2"), distance: 2.9, pairs: 1, his: false, inter_chain: false }],
      disulfides: [{ a: end("A", "3", "CYS", "SG"), b: end("B", "40", "CYS", "SG"), distance: 2.05, ca_distance: 5.6, chi3: -87.2, inter_chain: true }],
      aromatic: [
        { type: "pi_tshaped", a: end("A", "4", "PHE", "anello"), b: end("B", "45", "PHE", "anello"), distance: 5.0, angle: 90, offset: 0, inter_chain: true },
        { type: "cation_pi", a: end("A", "11", "LYS", "NZ"), b: end("A", "45", "PHE", "anello"), distance: 4, angle: 0, offset: 0, inter_chain: false },
      ],
    },
    interfaces: IFACE, conservation: CONS, warnings: [], ...extra,
  };
}

describe("righe delle interazioni", () => {
  it("legami idrogeno, ponti salini, disolfuri e aromatiche in righe uniformi", () => {
    const hb = interactionRows(result(), "hbonds");
    expect(hb.map((r) => r.type)).toEqual(["scheletro–scheletro", "catene laterali", "scheletro–catena laterale"]);
    expect(hb[0].detail).toBe("H…A 2,02 Å · D–H…A 166° (H posizionato)");
    expect(hb[1].detail).toBe("senza H: AA–D…A 93°");
    expect(hb[2].detail).toBe("H…A 2,10 Å · D–H…A 151°");
    expect(new Set(hb.map((r) => r.key)).size).toBe(3);
    const sb = interactionRows(result(), "salt_bridges");
    expect(sb[0]).toMatchObject({ distance: 2.9, type: "ionico", detail: "1 coppia N–O entro 4 Å" });
    expect(interactionRows(result(), "disulfides")[0].detail).toBe("Cα–Cα 5,60 Å · χ3 -87°"); // come fmtNum
    const ar = interactionRows(result(), "aromatic");
    expect(ar.map((r) => r.type)).toEqual(["π-stacking a T", "catione-π"]);
    expect(ar[1].detail).toBe("angolo con la normale 0° · spostamento 0,00 Å");
    expect(interactionRows(null, "hbonds")).toEqual([]);
    expect(interactionCounts(result())).toEqual({ hbonds: 3, salt_bridges: 1, disulfides: 1, aromatic: 2 });
  });

  it("etichette dei residui", () => {
    expect(endLabel(end("A", "27", "LYS", "NZ"))).toBe("LYS A27 NZ");
    expect(endLabel(end("A", "27", "LYS", "NZ"), false)).toBe("LYS A27");
  });
});

describe("filtri per catena e per residuo", () => {
  it("interpreta il testo del filtro", () => {
    expect(parseResidueQuery("27")).toEqual({ resnum: "27" });
    expect(parseResidueQuery(" k 27 ")).toEqual({ letter: "K", resnum: "27" });
    expect(parseResidueQuery("lys27")).toEqual({ name: "LYS", resnum: "27" });
    expect(parseResidueQuery("B:41")).toEqual({ chain: "B", resnum: "41" });
    expect(parseResidueQuery("A:LYS27")).toEqual({ chain: "A", name: "LYS", resnum: "27" });
    expect(parseResidueQuery("52A")).toEqual({ resnum: "52A" });
    expect(parseResidueQuery("nz")).toEqual({ text: "NZ" });
    expect(parseResidueQuery("  ")).toBeNull();
  });

  it("una lettera vale come catena o come amminoacido", () => {
    const k27 = end("A", "27", "LYS", "NZ");
    expect(endMatches(k27, parseResidueQuery("K27")!)).toBe(true);
    expect(endMatches(k27, parseResidueQuery("A27")!)).toBe(true);
    expect(endMatches(k27, parseResidueQuery("B27")!)).toBe(false);
    expect(endMatches(k27, parseResidueQuery("A:27")!)).toBe(true);
    expect(endMatches(k27, parseResidueQuery("B:27")!)).toBe(false);
    expect(endMatches(k27, parseResidueQuery("GLU27")!)).toBe(false);
    expect(endMatches(k27, parseResidueQuery("27A")!)).toBe(false);
    expect(endMatches(k27, parseResidueQuery("lys")!)).toBe(true);
  });

  it("filtra le righe su una delle due estremità", () => {
    const rows = interactionRows(result(), "hbonds");
    expect(filterRows(rows, "all", "").length).toBe(3);
    expect(filterRows(rows, "all", "K27").map((r) => r.b.atom)).toEqual(["OD2", "O"]); // donatore NZ e accettore O di Lys27
    expect(filterRows(rows, "all", "27").length).toBe(3);
    expect(filterRows(rows, "inter", "").map((r) => r.a.atom)).toEqual(["NE2"]);
    expect(filterRows(rows, "chain:B", "").length).toBe(1);
    expect(filterRows(rows, "chain:A", "D52").length).toBe(1);
    expect(filterRows(rows, "chain:A", residueFilterText({ chain: "A", resnum: "23" })).length).toBe(1);
  });

  it("opzioni del filtro per catena", () => {
    expect(chainFilterOptions(["A"]).map((o) => o.value)).toEqual(["all", "chain:A"]);
    expect(chainFilterOptions(["A", "B"]).map((o) => o.value)).toEqual(["all", "inter", "chain:A", "chain:B"]);
  });
});

describe("evidenza nel 3D", () => {
  it("i due residui di un'interazione, uno solo se coincidono", () => {
    expect(highlightFor({ a: end("A", "27", "LYS", "NZ"), b: end("A", "52", "ASP", "OD2") })).toEqual([{ chain: "A", resi: 27 }, { chain: "A", resi: 52 }]);
    expect(highlightFor({ a: end("A", "52A", "LYS", "NZ"), b: end("A", "52A", "LYS", "O") })).toEqual([{ chain: "A", resi: 52 }]);
    expect(interfaceHighlight(PAIR)).toEqual([{ chain: "A", resi: 57 }, { chain: "A", resi: 60 }, { chain: "B", resi: 57 }]);
  });
});

describe("colorazioni", () => {
  it("conservazione: scala continua 0–1, grigio per i residui senza punteggio", () => {
    const c = conservationColoring(CONS)!;
    expect(c.key).toBe("conservation");
    const map = coloringMap(c);
    expect(map.get("A:1")).toBe("#a02560");
    expect(map.get("A:20")).toBe(NO_VALUE_COLOR);
    expect(colorFor(CONSERVATION_SCALE, 0)).toBe("#10c8d1");
    expect(colorFor(CONSERVATION_SCALE, 0.5)).toBe("#ffffff");
    expect(hasMissing(c)).toBe(true);
    expect(describeResidue(c, { chain: "A", resnum: "20" })).toBe("— (colonna 20: oltre il 30% di gap)");
    expect(describeResidue(c, { chain: "A", resnum: "21" })).toBe("— (non allineato alla riga)");
    expect(describeResidue(c, { chain: "A", resnum: "1" })).toBe("1,00 (colonna 1)");
    expect(conservationColoring(null)).toBeNull();
    const legend = legendFor(c.scale, c.title);
    expect(legend.kind).toBe("gradient");
  });

  it("interfaccia: fasce di ΔSASA con la soglia di 1 Å² esclusa, come il backend", () => {
    const c = interfaceColoring(IFACE)!;
    const map = coloringMap(c);
    expect(map.get("A:56")).toBe("#cbd5e1");
    expect(map.get("A:62")).toBe("#facc15");
    expect(map.get("A:57")).toBe("#f97316");
    expect(map.get("A:60")).toBe("#b91c1c");
    expect(colorFor(INTERFACE_SCALE, 1)).toBe("#cbd5e1");
    expect(colorFor(INTERFACE_SCALE, 1.01)).toBe("#facc15");
    expect(describeResidue(c, { chain: "A", resnum: "57" })).toBe("ΔSASA 37,3 Å² · interfaccia");
    expect(describeResidue(c, { chain: "A", resnum: "56" })).toBe("ΔSASA 0,4 Å²");
    expect(interfaceColoring({ ...IFACE, pairs: [], residues: [] })).toBeNull();
  });

  it("opzioni di colorazione secondo il risultato", () => {
    const opts = analysisColorOptions(result());
    expect(opts.map((o) => [o.value, o.available])).toEqual([["none", true], ["interface", true], ["conservation", true]]);
    const single = analysisColorOptions(result({ interfaces: { ...IFACE, pairs: [], residues: [] }, conservation: null }));
    expect(single.map((o) => o.available)).toEqual([true, false, false]);
    expect(analysisColoring(result(), "none")).toBeNull();
    expect(analysisColoring(result(), "interface")?.key).toBe("interface");
    expect(analysisColoring(result(), "conservation")?.key).toBe("conservation");
  });
});

describe("allineamento per la conservazione", () => {
  it("richiesta da job o da testo, con i motivi di blocco", () => {
    expect(msaRequest("none", "", "", "")).toEqual({ msa: null, error: null });
    expect(msaRequest("job", "", "", "").error).toBe("Scegli un job di allineamento.");
    expect(msaRequest("job", "j1", "", " s0 ")).toEqual({ msa: { job_id: "j1", row: "s0" }, error: null });
    expect(msaRequest("text", "", " ", "").error).toMatch(/Incolla/);
    expect(msaRequest("text", "", ">a\nAAA\n", "").error).toBe("Servono almeno due sequenze.");
    expect(msaRequest("text", "", ">a\nAAA\n>b\nAAC\n", "")).toEqual({ msa: { text: ">a\nAAA\n>b\nAAC\n", row: undefined }, error: null });
    expect(msaRequest("text", "", "CLUSTAL W\n\na AAA\nb AAC\n", "").error).toBeNull();
  });

  it("scheda Interazioni: stato conservato cambiando scheda, per la stessa struttura", () => {
    const memory = { sourceKey: "template:1UBQ", jobId: "j1", msaMode: "text" as const, msaJob: "", msaText: ">a\nAAA\n>b\nAAC\n", row: "a" };
    expect(initialPanelState("template:1UBQ", memory)).toEqual(memory);
    // un'altra struttura: niente job né allineamento della precedente; un link a un job vale sempre
    expect(initialPanelState("template:2XYZ", memory)).toEqual({ sourceKey: "template:2XYZ", jobId: null, msaMode: "none", msaJob: "", msaText: "", row: "" });
    expect(initialPanelState("template:1UBQ", memory, "j9").jobId).toBe("j9");
    expect(initialPanelState("template:1UBQ", null).jobId).toBeNull();
  });

  it("job seguito: quello già seguito, poi l'analisi in corso, poi l'ultima completata", () => {
    expect(followedAnalysis("j1", { job_id: "j0", active_job_id: "j2" })).toBe("j1");
    expect(followedAnalysis(null, { job_id: "j0", active_job_id: "j2" })).toBe("j2");
    expect(followedAnalysis(null, { job_id: "j0", active_job_id: null })).toBe("j0");
    expect(followedAnalysis(null, { job_id: null })).toBeNull();
  });

  it("FASTA da un file letto dal server, con i gap delle righe allineate", () => {
    const parsed: ParsedFile = {
      format: "clustal", format_label: "Clustal", aligned: true, warnings: [], filename: "x.aln",
      records: [{ name: "a b", description: "", sequence: "AAC", aligned: "AA-C", length: 3 }, { name: "c", description: "", sequence: "AAGC", aligned: "AAGC", length: 4 }],
    };
    expect(fastaFromParsed(parsed)).toBe(">a_b\nAA-C\n>c\nAAGC\n");
  });
});
