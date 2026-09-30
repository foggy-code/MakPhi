import { describe, expect, it } from "vitest";
import type { PlddtTrim, TemplateItem } from "../api/types";
import {
  accessionFromName,
  autoAccessionFor,
  clampThreshold,
  followAccession,
  hasPredictedTemplate,
  isAlphaFoldId,
  isUniProtAccession,
  pipelineFastaName,
  plddtBand,
  plddtLabel,
  plddtTone,
  removedRanges,
  sequenceAccession,
  trimSummary,
} from "./alphafold";

const item = (id: string, predicted: boolean): TemplateItem => ({
  id, format: "pdb", size: 1, source: predicted ? "alphafold" : "RCSB PDB", added_at: null, title: "", method: "",
  resolution: null, chains: ["A"], predicted,
});

describe("accessioni UniProt e identificativi AlphaFold", () => {
  it("formato ufficiale UniProtKB", () => {
    expect(isUniProtAccession("P69905")).toBe(true);
    expect(isUniProtAccession(" p0cg48 ")).toBe(true);
    expect(isUniProtAccession("A0A0B4J2F0")).toBe(true);
    for (const bad of ["", "1UBQ", "P0CG48-2", "ABCDEF", "P0CG4", "AF-P69905-F1"]) expect(isUniProtAccession(bad)).toBe(false);
  });
  it("id dei modelli: AF-<ACC>-F<n>", () => {
    expect(isAlphaFoldId("AF-P69905-F1")).toBe(true);
    expect(isAlphaFoldId("af-q8wz42-f2")).toBe(true);
    expect(isAlphaFoldId("AF-P69905-F0")).toBe(false);
    expect(isAlphaFoldId("AF-ABCDEF-F1")).toBe(false);
    expect(isAlphaFoldId("1UBQ")).toBe(false);
  });
  it("accessione precompilata dalla sequenza importata da UniProt", () => {
    expect(sequenceAccession({ meta: { accession: "p69905" } })).toBe("P69905");
    expect(sequenceAccession({ meta: { accession: "non valida" } })).toBe("");
    expect(sequenceAccession({ meta: {} })).toBe("");
    expect(sequenceAccession(null)).toBe("");
  });
  it("accessione dall'intestazione FASTA di UniProt", () => {
    expect(accessionFromName("sp|P69905|HBA_HUMAN")).toBe("P69905");
    expect(accessionFromName("tr|A0A0B4J2F0|X")).toBe("A0A0B4J2F0");
    expect(accessionFromName("sp_P0CG48_UBC_HUMAN")).toBe("P0CG48");
    expect(accessionFromName("HBA_HUMAN")).toBe("");
    expect(accessionFromName("sp|ABCDEF|X")).toBe("");
  });
});

describe("accessione della pagina Ricerca", () => {
  const HBA = { name: "HBA_HUMAN", sequence: "MVLSPADKTN", meta: { accession: "P69905" } };
  const auto = autoAccessionFor(HBA)!;
  it("precompilata da una sequenza del progetto", () => {
    expect(auto).toEqual({ accession: "P69905", sequence: "MVLSPADKTN" });
    expect(autoAccessionFor({ sequence: "MK", meta: {} })).toBeNull();
  });
  it("si toglie quando la sequenza cambia (incollata o da file)", () => {
    expect(followAccession("P69905", auto, { name: "altra", sequence: "MKTAYIAK" })).toEqual({ uniprot: "", auto: null });
    // stessa sequenza (solo il nome cambia, o spazi e minuscole): resta
    expect(followAccession("P69905", auto, { name: "HBA", sequence: "mvlsp adktn" })).toEqual({ uniprot: "P69905", auto });
  });
  it("un'accessione scritta a mano resta", () => {
    expect(followAccession("Q12345", auto, { name: "altra", sequence: "MKTAYIAK" })).toEqual({ uniprot: "Q12345", auto: null });
    expect(followAccession("Q12345", null, { name: "altra", sequence: "MKTAYIAK" })).toEqual({ uniprot: "Q12345", auto: null });
    expect(followAccession("", null, { name: "altra", sequence: "MKTAYIAK" })).toEqual({ uniprot: "", auto: null });
  });
  it("un FASTA incollato con l'intestazione di UniProt la precompila", () => {
    const next = followAccession("P69905", auto, { name: "sp|P0CG48|UBC_HUMAN", sequence: "MQIFVK" });
    expect(next).toEqual({ uniprot: "P0CG48", auto: { accession: "P0CG48", sequence: "MQIFVK" } });
    expect(followAccession("Q12345", null, { name: "sp|P0CG48|UBC_HUMAN", sequence: "MQIFVK" }).uniprot).toBe("Q12345");
  });
});

describe("intestazioni della pipeline", () => {
  it("sequenze importate da UniProt con sp|ACC|NOME (tr| se non revisionate)", () => {
    expect(pipelineFastaName({ name: "HBA_HUMAN", meta: { accession: "P69905" } })).toBe("sp|P69905|HBA_HUMAN");
    expect(pipelineFastaName({ name: "HBA_HUMAN", meta: { accession: "P69905", reviewed: true } })).toBe("sp|P69905|HBA_HUMAN");
    expect(pipelineFastaName({ name: "X_HUMAN", meta: { accession: "A0A0B4J2F0", reviewed: false } })).toBe("tr|A0A0B4J2F0|X_HUMAN");
    // senza accessione, o già con l'intestazione di UniProt: il nome resta
    expect(pipelineFastaName({ name: "mia_proteina", meta: {} })).toBe("mia_proteina");
    expect(pipelineFastaName({ name: "sp|P69905|HBA_HUMAN", meta: { accession: "P69905" } })).toBe("sp|P69905|HBA_HUMAN");
  });
});

describe("fasce di pLDDT", () => {
  it("limiti di AlphaFold DB, estremo inferiore compreso", () => {
    expect(plddtBand(90)).toBe("very_high");
    expect(plddtBand(89.99)).toBe("confident");
    expect(plddtBand(70)).toBe("confident");
    expect(plddtBand(69.9)).toBe("low");
    expect(plddtBand(50)).toBe("low");
    expect(plddtBand(49.9)).toBe("very_low");
  });
  it("etichette e toni", () => {
    expect(plddtLabel(88.62)).toBe("pLDDT 88,6 (confidente)");
    expect(plddtLabel(null)).toBe("pLDDT —");
    expect(plddtTone(95)).toBe("ok");
    expect(plddtTone(30)).toBe("err");
    expect(plddtTone(undefined)).toBeUndefined();
  });
});

describe("opzione della soglia pLDDT", () => {
  const library = [item("1UBQ", false), item("AF-P69905-F1", true), item("MIO_AF", true)];
  it("solo con almeno un template predetto", () => {
    expect(hasPredictedTemplate([{ id: "1UBQ", chains: ["A"] }], library)).toBe(false);
    expect(hasPredictedTemplate([{ id: "1UBQ", chains: ["A"] }, { id: "MIO_AF", chains: ["A"] }], library)).toBe(true);
    // non ancora in libreria: riconosciuto dall'identificativo (verrà scaricato da AlphaFold DB)
    expect(hasPredictedTemplate([{ id: "AF-P0CG48-F1", chains: ["A"] }], library)).toBe(true);
    expect(hasPredictedTemplate([{ id: "AF-P0CG48-F1", chains: ["A"] }], null)).toBe(true);
    expect(hasPredictedTemplate([], library)).toBe(false);
  });
  it("soglia limitata a 0–100", () => {
    expect(clampThreshold(150)).toBe(100);
    expect(clampThreshold(-3)).toBe(0);
    expect(clampThreshold(Number.NaN)).toBe(70);
    expect(clampThreshold(65)).toBe(65);
  });
});

describe("riepilogo del ritaglio", () => {
  const trim: PlddtTrim = { threshold: 70, total: 76, removed: 11, kept: 65, mean_before: 81.97, mean_after: 90, removed_ranges: [["1", "5", "A"], ["71", "76", "A"], ["40", "40", "A"]] };
  it("residui tolti e intervalli", () => {
    expect(trimSummary({ predicted: true, plddt: trim })).toBe("rimossi 11 residui con pLDDT < 70 (restano 65 di 76)");
    expect(trimSummary({ predicted: true, plddt: { ...trim, removed: 1, kept: 75 } })).toBe("rimosso 1 residuo con pLDDT < 70 (restano 75 di 76)");
    expect(trimSummary({ predicted: true, plddt: { ...trim, removed: 0, kept: 76 } })).toBe("nessun residuo con pLDDT < 70");
    expect(trimSummary({ predicted: true, plddt: null })).toBe("modello predetto, usato senza ritaglio pLDDT");
    expect(trimSummary({ predicted: false })).toBeNull();
    // frammenti isolati tolti dopo la soglia
    expect(trimSummary({ predicted: true, plddt: { ...trim, removed: 24, removed_threshold: 20, removed_fragments: 4, min_fragment: 5, kept: 52 } }))
      .toBe("rimossi 20 residui con pLDDT < 70 e 4 in frammenti isolati (< 5 residui) (restano 52 di 76)");
    expect(removedRanges({ plddt: trim })).toBe("1–5:A, 71–76:A, 40:A");
    expect(removedRanges({})).toBe("");
  });
});
