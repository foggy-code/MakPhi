import { describe, expect, it } from "vitest";
import type { AssemblySummary, ComplexAssessment, InterfaceComparison, InterfacePair } from "../api/types";
import { coloringMap } from "./residueColoring";
import {
  assemblyChainLabel, assemblyComposition, assemblyImportable, assemblyLabel, CHAIN_COLORS, cleanSequence, comparisonHighlight, complexColoring, complexRequest, complexTemplateRefs,
  compositionLabel, emptyComplex, MAX_COMPLEX_RESIDUES, oligomerLabel, parseResidueLabel, resolvedSequence, rowsForChains, splitPirChains, stoichiometry, symmetryOptions,
  type ComplexRow, type ComplexState,
} from "./complex";
import { parsePir } from "./pir";

// sequenze dei test del backend (tests/conftest.py)
const HUMAN_UB = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG";
const YEAST_UB = "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG";

const chain = (id: string, sequence = HUMAN_UB) => ({ chain: id, sequence, residues: sequence.length });

function dimer(rows?: Partial<ComplexRow>[]): ComplexState {
  const base = rowsForChains([chain("A"), chain("B")]);
  return { ...emptyComplex("UBQD"), rows: base.map((r, i) => ({ ...r, ...(rows?.[i] ?? {}) })) };
}

describe("righe della modalità Complesso", () => {
  it("propone «stessa della catena A» per le catene con la stessa sequenza del template", () => {
    const rows = rowsForChains([chain("A"), chain("B"), chain("C", YEAST_UB.slice(0, 40))]);
    expect(rows.map((r) => [r.chain, r.source, r.sameAs])).toEqual([["A", "paste", ""], ["B", "same", "A"], ["C", "paste", ""]]);
    expect(rows.every((r) => r.included)).toBe(true);
  });

  it("conserva le righe già compilate quando le catene si rileggono", () => {
    const first = rowsForChains([chain("A"), chain("B")]);
    first[0] = { ...first[0], sequence: YEAST_UB, name: "ub1" };
    const again = rowsForChains([chain("A"), chain("B")], first);
    expect(again[0].sequence).toBe(YEAST_UB);
    expect(again[0].name).toBe("ub1");
  });

  it("pulisce le sequenze incollate (FASTA, numeri, spazi)", () => {
    expect(cleanSequence(">sp|P0CG48|UBC\nMQIF VKTL\n10 tgk*")).toBe("MQIFVKTLTGK");
    const state = dimer([{ sequence: "mqif" }]);
    expect(resolvedSequence(state.rows[1], state.rows)).toBe("MQIF");
  });
});

describe("richiesta del complesso", () => {
  it("omodimero: sequenza della catena A e B = stessa della catena A, con i gruppi di simmetria", () => {
    const state = dimer([{ sequence: YEAST_UB, name: " ub1 " }]);
    const check = complexRequest(state);
    expect(check.errors).toEqual([]);
    expect(check.request).toEqual({ name: "complesso", chains: [
      { template_chain: "A", sequence: YEAST_UB, name: "ub1" }, { template_chain: "B", same_as: "A" }] });
    expect(check.residues).toBe(152);
    expect(check.symmetric).toEqual([["A", "B"]]);
    expect(symmetryOptions(state, check)).toEqual({ symmetry: true, symmetry_weight: 1 });
    expect(complexTemplateRefs(state)).toEqual([{ id: "UBQD", chains: ["A", "B"] }]);
  });

  it("eterodimero: nessuna simmetria da inviare anche se richiesta", () => {
    const state = dimer([{ sequence: YEAST_UB }, { source: "paste", sequence: HUMAN_UB }]);
    const check = complexRequest(state);
    expect(check.errors).toEqual([]);
    expect(check.symmetric).toEqual([]);
    expect(symmetryOptions(state, check)).toEqual({ symmetry: false });
  });

  it("errori chiari se la corrispondenza è incompleta", () => {
    expect(complexRequest(dimer()).errors).toEqual(["Manca la sequenza del bersaglio per la catena A."]);
    const one = dimer([{ sequence: YEAST_UB }, { included: false }]);
    expect(complexRequest(one).errors[0]).toMatch(/almeno due catene/);
    const orphan = dimer([{ sequence: YEAST_UB, included: false }]);
    expect(complexRequest(orphan).errors.join(" ")).toMatch(/non è fra quelle scelte/);
    const chainOfRefs = dimer([{ source: "same", sameAs: "B" }, { source: "same", sameAs: "A" }]);
    expect(complexRequest(chainOfRefs).errors.join(" ")).toMatch(/non ha una sequenza propria/);
    expect(complexRequest({ ...dimer([{ sequence: YEAST_UB }]), templateId: "" }).errors[0]).toMatch(/Scegli il template/);
    expect(complexRequest(dimer()).request).toBeNull();
  });

  it("limiti del backend: 12 catene e 3000 residui (compresi)", () => {
    const rows = rowsForChains("ABCDEFGHIJKLM".split("").map((c) => chain(c)));
    rows[0] = { ...rows[0], sequence: "M".repeat(10) };
    expect(complexRequest({ ...emptyComplex("X"), rows }).errors.join(" ")).toMatch(/massimo 12/);
    const big = dimer([{ sequence: "M".repeat(MAX_COMPLEX_RESIDUES / 2) }]);
    expect(complexRequest(big).errors).toEqual([]);
    const over = dimer([{ sequence: "M".repeat(MAX_COMPLEX_RESIDUES / 2 + 1) }]);
    expect(complexRequest(over).errors[0]).toMatch(/3002 residui in tutto/);
  });
});

describe("anteprima dell'allineamento per catena", () => {
  const pir = `>P1;UBQD_AB\nstructureX:UBQD_AB.pdb:1:A:76:B:::-1.00:-1.00\n${HUMAN_UB}/${HUMAN_UB}*\n\n>P1;ubd\nsequence:ubd:::::::0.00:0.00\n${YEAST_UB}/${YEAST_UB}*\n`;

  it("un allineamento per catena con il bersaglio come riferimento", () => {
    const chains = splitPirChains(parsePir(pir), ["Catena A", "Catena B"]);
    expect(chains).toHaveLength(2);
    expect(chains![1]).toEqual({ label: "Catena B", ids: ["UBQD_AB", "ubd"], aligned: [HUMAN_UB, YEAST_UB], reference: 1 });
  });

  it("PIR con un numero di catene diverso fra le voci: nessuna divisione", () => {
    const bad = pir.replace(`${YEAST_UB}/${YEAST_UB}*`, `${YEAST_UB}${YEAST_UB}*`);
    expect(splitPirChains(parsePir(bad))).toBeNull();
    expect(splitPirChains([])).toBeNull();
  });
});

// confronto come lo scrive il backend per il dimero con la catena B spostata di 3 Å (tests/test_complexes.py)
const MODEL_PAIR: InterfacePair = {
  chains: ["A", "B"], area: 211.9, buried: 423.8, apolar_fraction: 0.3,
  residues: {
    A: [{ chain: "A", resnum: "57", resname: "SER", aa: "S", delta: 37.3, apolar_delta: 10 }, { chain: "A", resnum: "8", resname: "LEU", aa: "L", delta: 5, apolar_delta: 5 }],
    B: [{ chain: "B", resnum: "60", resname: "ASN", aa: "N", delta: 64.8, apolar_delta: 20 }],
  },
  counts: { A: 2, B: 1 },
  composition: { counts: { apolari: 1, polari: 2, carichi: 0, altri: 0 }, percent: { apolari: 33, polari: 67, carichi: 0, altri: 0 } },
  interactions: { hbonds: 0, salt_bridges: 0, disulfides: 0, pi_stacking: 0, cation_pi: 0 },
  plausibility: { level: "bassa", text: "area sotto 400 Å²", limits: "…", thresholds: { low: 400, high: 600 } },
};

const COMPARISON: InterfaceComparison = {
  template_chains: ["A", "B"], model_chains: ["A", "B"], status: "media", tone: "ok", area_template: 122.9, area_model: 211.9,
  area_ratio: 172.4, template_residues: 8, conserved: 2, conserved_percent: 25, unaligned: 0, conserved_residues: ["A57", "B60"],
  lost_residues: ["A58", "B62"], fnat: 0.8, irms: 1.37, lrms: 3, dockq: 0.745, dockq_class: "media", native_contacts: 5,
  reproduced_contacts: 4, receptor: "A", ligand: "B", text: "Interfaccia del template in gran parte riprodotta: DockQ 0,74 …",
};

const ASSESSMENT: ComplexAssessment = {
  version: 1, template: "UBQD_AB", chains: [{ template: "A", model: "A" }, { template: "B", model: "B" }],
  interfaces: { version: 1, method: "Shrake-Rupley", chains: ["A", "B"], min_delta: 1, pairs: [MODEL_PAIR], interface_residues: 3 },
  template_interfaces: null, comparison: [COMPARISON], status: "media", tone: "ok", summary: "…", notes: [], limits: "…",
};

describe("3D del complesso", () => {
  it("catene in colori chiari e residui d'interfaccia conservati, nuovi e persi", () => {
    const coloring = complexColoring(ASSESSMENT)!;
    expect(coloring.chainColors).toEqual({ A: CHAIN_COLORS[0], B: CHAIN_COLORS[1] });
    const byKey = Object.fromEntries(coloring.values.map((v) => [`${v.chain}${v.resnum}`, v.value]));
    expect(byKey).toEqual({ A57: "conserved", A8: "new", B60: "conserved", A58: "lost", B62: "lost" });
    expect(coloring.describe("lost")).toBe("Interfaccia del template persa");
    expect(coloring.legendExtra?.map((x) => x.label)).toEqual(["catena A", "catena B"]);
    expect(coloringMap(coloring).get("A:57")).toBe("#15803d");
    expect(complexColoring(null)).toBeNull();
  });

  it("una sola interfaccia: solo i suoi residui, evidenziati e centrati", () => {
    const coloring = complexColoring(ASSESSMENT, 0)!;
    expect(coloring.title).toBe("Interfaccia A–B");
    expect(comparisonHighlight(ASSESSMENT, COMPARISON)).toEqual([
      { chain: "A", resi: 57 }, { chain: "A", resi: 8 }, { chain: "B", resi: 60 }, { chain: "A", resi: 58 }, { chain: "B", resi: 62 }]);
    expect(complexColoring(ASSESSMENT, 3)!.values).toEqual([]);  // indice fuori dal confronto: nessun residuo
  });

  it("etichette dei residui con codice di inserzione", () => {
    expect(parseResidueLabel("A52A")).toEqual({ chain: "A", resnum: "52A" });
  });
});

describe("assemblaggi", () => {
  const dimerAssembly: AssemblySummary = {
    id: "1", details: "author_defined_assembly", method: "", oligomeric_details: "dimeric", oligomeric_count: 2,
    generators: [{ chains: ["A"], operators: "1,2" }],
    chains: [{ chain: "A", source: "A", operator: "1", identity: true, protein: true }, { chain: "B", source: "A", operator: "2", identity: false, protein: true }],
    chain_count: 2, protein_chains: 2, identity_only: false, buildable: true, note: "",
  };

  it("descrizione con lo stato oligomerico in italiano e l'origine delle copie", () => {
    expect(assemblyLabel(dimerAssembly)).toBe("Assemblaggio 1 · dimero · catene A, B (B = A con l'operatore 2) · dall'autore");
    // anche la prima copia trasformata (prodotto con una traslazione): l'operatore si vede
    const moved = { chain: "A", source: "A", operator: "1×3", identity: false, protein: true };
    expect(assemblyChainLabel(moved)).toBe("A = A con l'operatore 1×3");
    expect(assemblyChainLabel(moved, true)).toBe("A=A·1×3");
    expect(assemblyChainLabel(dimerAssembly.chains[0], true)).toBe("A");
    expect(oligomerLabel("", 3)).toBe("3 catene proteiche");
    expect(oligomerLabel("MONOMERIC", 1)).toBe("monomero");
    expect(assemblyLabel({ ...dimerAssembly, details: "software_defined_assembly", method: "PISA" })).toMatch(/dal programma \(PISA\)$/);
  });

  it("composizione: omo/etero, stechiometria e assemblaggi dalle entità del file", () => {
    expect(compositionLabel("monomer", 1)).toBe("monomero");
    expect(compositionLabel("homo", 2)).toBe("omo-dimero");
    expect(compositionLabel("hetero", 4)).toBe("etero-tetramero");
    expect(compositionLabel("homo", 24)).toBe("omo-oligomero di 24 catene");
    expect(stoichiometry([1, 2])).toBe("A2B");
    expect(stoichiometry([1])).toBe("A");
    const homo = { kind: "monomer" as const, chains: 1, stoichiometry: "A", source: "file" as const, entities: [{ name: "UBQ", chains: ["A"], length: 76 }] };
    // dimero generato per simmetria da una sola catena del file: omo-dimero
    expect(assemblyComposition(dimerAssembly, homo)).toEqual({ kind: "homo", chains: 2, stoichiometry: "A2" });
    const hetero = { kind: "hetero" as const, chains: 2, stoichiometry: "AB", source: "file" as const,
      entities: [{ name: "X", chains: ["A"], length: 70 }, { name: "Y", chains: ["B"], length: 80 }] };
    const ab = { ...dimerAssembly, chains: [
      { chain: "A", source: "A", operator: "1", identity: true, protein: true },
      { chain: "B", source: "B", operator: "1", identity: true, protein: true },
    ] };
    expect(assemblyComposition(ab, hetero)).toEqual({ kind: "hetero", chains: 2, stoichiometry: "AB" });
    // catena d'origine sconosciuta o composizione assente: nessuna indicazione
    expect(assemblyComposition(ab, homo)).toBeNull();
    expect(assemblyComposition(ab, undefined)).toBeNull();
  });

  it("da importare solo se costruibile, con almeno due catene e non la sola identità", () => {
    expect(assemblyImportable(dimerAssembly)).toBe(true);
    expect(assemblyImportable({ ...dimerAssembly, identity_only: true })).toBe(false);
    expect(assemblyImportable({ ...dimerAssembly, protein_chains: 1 })).toBe(false);
    expect(assemblyImportable({ ...dimerAssembly, buildable: false })).toBe(false);
  });
});
