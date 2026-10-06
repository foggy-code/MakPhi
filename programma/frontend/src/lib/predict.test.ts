import { describe, expect, it } from "vitest";
import type { ConsensusResult, PredictResult } from "../api/types";
import { linkedSources } from "./compare";
import {
  consensusPredictorNote, consentText, ESMFOLD_MAX_LENGTH, ESMFOLD_PUBLIC_URL, esmfoldBlocked, esmfoldService, followedJob, metricsLine, needsConsent, originText,
  pastedSequence, predictionCompareLink, predictionQualityLink, predictionSource, predictPreflight, predictRequest, STATUS_TONE, tooLongForEsmfold, verdictTitle,
} from "./predict";

const UBQ = { type: "template" as const, id: "1UBQ", chain: "A" };

const result = (over: Partial<PredictResult> = {}): PredictResult => ({
  predictor: "esmfold", label: "ESMFold",
  origin: { service: ESMFOLD_PUBLIC_URL, cached: false, scale: "0-1" },
  prediction_file: "prediction_esmfold.pdb", recognized_as: "esmfold", sequence: { name: "1UBQ", length: 76 },
  plddt: { mean: 90, residues: 76, fractions: {} }, accession: null, source: UBQ, model_label: "1UBQ", chain: "A", source_sha256: null,
  comparison: null, plausibility: null, warnings: [], limits: "", ...over,
});

describe("confronto con una predizione (F9.3)", () => {
  it("consenso all'invio: solo per ESMFold e solo se non è già dato (nel dubbio si chiede)", () => {
    expect(needsConsent("esmfold", { esmfold_remote: false })).toBe(true);
    expect(needsConsent("esmfold", null)).toBe(true);
    expect(needsConsent("esmfold", { esmfold_remote: true })).toBe(false);
    expect(needsConsent("alphafold", { esmfold_remote: false })).toBe(false);
    const text = consentText(ESMFOLD_PUBLIC_URL, 76);
    expect(text).toContain("(76 residui)");
    expect(text).toContain(ESMFOLD_PUBLIC_URL);
    expect(text).toContain("Le coordinate del modello non lasciano il computer");
    expect(consentText("https://esm.example.org", null)).toContain("la sequenza della catena viene inviata");
  });

  it("indirizzo del servizio: quello delle Impostazioni o il pubblico", () => {
    expect(esmfoldService({ esmfold_url: "" })).toBe(ESMFOLD_PUBLIC_URL);
    expect(esmfoldService(null)).toBe(ESMFOLD_PUBLIC_URL);
    expect(esmfoldService({ esmfold_url: " https://esm.example.org/fold " })).toBe("https://esm.example.org/fold");
  });

  it("richiesta e controlli preventivi del job predict", () => {
    expect(predictRequest("esmfold", null).error).toBe("Scegli la struttura da confrontare.");
    expect(predictRequest("esmfold", UBQ, { projectId: "p1" })).toEqual({
      body: { project_id: "p1", predictor: "esmfold", source: UBQ, chain: "A" }, error: null,
    });
    expect(predictRequest("esmfold", { type: "job", job_id: "j", file: "m.pdb" }).body?.chain).toBeNull();
    expect(predictRequest("alphafold", UBQ, { accession: " " }).error).toBe("Indica l'accessione UniProt del modello di AlphaFold DB.");
    const af = predictRequest("alphafold", UBQ, { accession: " P0CG48 ", chain: "B" }).body;
    expect(af).toEqual({ predictor: "alphafold", source: UBQ, chain: "B", accession: "P0CG48" });
    expect(predictPreflight(af)).toEqual({ kind: "predict", params: af });
    expect(predictPreflight(null)).toBeNull();
  });

  it("limite di lunghezza di ESMFold come il backend (400 compreso)", () => {
    expect(ESMFOLD_MAX_LENGTH).toBe(400);
    expect(tooLongForEsmfold(400)).toBe(false);
    expect(tooLongForEsmfold(401)).toBe(true);
    expect(tooLongForEsmfold(null)).toBe(false);
  });

  it("oltre il limite niente consenso né avvio con ESMFold: decide la sequenza incollata, poi la catena", () => {
    expect(esmfoldBlocked("esmfold", 450)).toBe(true);
    expect(esmfoldBlocked("esmfold", 400)).toBe(false);
    expect(esmfoldBlocked("esmfold", null)).toBe(false); // lunghezza ignota: decide il backend
    expect(esmfoldBlocked("alphafold", 450)).toBe(false); // AlphaFold DB: va solo l'accessione
    expect(esmfoldBlocked("esmfold", 450, "MQIFVKTLTG".repeat(10))).toBe(false); // un dominio incollato
    expect(esmfoldBlocked("esmfold", 76, "A".repeat(401))).toBe(true);
  });

  it("sequenza completa incollata: FASTA o testo, al posto di quella della catena", () => {
    expect(pastedSequence(">sp|P0CG48 UBC\nmqifv ktltg\r\nKTITL*")).toBe("MQIFVKTLTGKTITL");
    expect(pastedSequence("  ")).toBe("");
    expect(predictRequest("esmfold", UBQ, { sequence: ">x\nMQIFV KTLTG", name: "1UBQ" }).body).toEqual({
      predictor: "esmfold", source: UBQ, chain: "A", sequence: { name: "1UBQ", sequence: "MQIFVKTLTG" },
    });
    expect(predictRequest("esmfold", UBQ, { sequence: " " }).body).not.toHaveProperty("sequence");
    expect(predictRequest("alphafold", UBQ, { accession: "P0CG48", sequence: "MQIFV" }).body).not.toHaveProperty("sequence");
  });

  it("job seguito: quello scelto, poi quello in corso, poi il più recente", () => {
    expect(followedJob("j9", { job_id: "j1", finished_at: null, active_job_id: "j2" })).toBe("j9");
    expect(followedJob(null, { job_id: "j1", finished_at: null, active_job_id: "j2" })).toBe("j2");
    expect(followedJob(null, { job_id: "j1", finished_at: "x", active_job_id: null })).toBe("j1");
    expect(followedJob(null, null)).toBeNull();
  });

  it("esito, metriche e origine", () => {
    expect(STATUS_TONE["divergenze locali"]).toBe("warn");
    expect(STATUS_TONE["ripiegamento diverso"]).toBe("err");
    expect(verdictTitle({ status: "concordano" })).toBe("Il modello concorda con la predizione");
    expect(metricsLine({ tm_score: 0.9271, lddt: 0.8778, lddt_confident: 0.8721, confident_residues: 76, pairs: 76 }))
      .toBe("TM-score 0,927 · lDDT 0,878 (0,872 sui 76 residui su 76 dove la predizione è sicura)");
    expect(metricsLine({ tm_score: 0.3, lddt: 0.4, lddt_confident: null, confident_residues: 0, pairs: 50 })).toBe("TM-score 0,300 · lDDT 0,400");
    expect(originText(result())).toBe(`servizio ${ESMFOLD_PUBLIC_URL} · pLDDT del servizio su 0–1, riportato a 0–100`);
    expect(originText(result({ origin: { service: "x", cached: true, scale: "0-100" } }))).toBe("servizio x · dalla cache di questo computer");
    expect(originText(result({ predictor: "alphafold", origin: { service: "y", cached: false, entry: "AF-P0CG48-F1" } })))
      .toBe("AlphaFold DB, modello AF-P0CG48-F1 (a EBI va solo l'accessione)");
  });

  it("link a Qualità e Confronto: il modello come riferimento (A), la predizione come B", () => {
    const r = result();
    expect(predictionSource("j1", r)).toEqual({ type: "job", job_id: "j1", file: "prediction_esmfold.pdb" });
    expect(predictionQualityLink("j1", r)).toBe("/quality?job=j1&file=prediction_esmfold.pdb");
    const { a, b } = linkedSources(new URLSearchParams(predictionCompareLink("j1", r).split("?")[1]));
    expect(a).toEqual({ type: "template", id: "1UBQ", chain: "A" });
    expect(b).toEqual({ type: "job", job_id: "j1", file: "prediction_esmfold.pdb" });
  });

  it("nota del consenso solo con il confronto incluso", () => {
    expect(consensusPredictorNote(null)).toBeNull();
    expect(consensusPredictorNote({ predictor: undefined } as unknown as ConsensusResult)).toBeNull();
    const c = { predictor: { text: "Stesso ripiegamento…", status: "divergenze locali" } } as unknown as ConsensusResult;
    expect(consensusPredictorNote(c)).toEqual({ text: "Stesso ripiegamento…", tone: "warn" });
  });
});
