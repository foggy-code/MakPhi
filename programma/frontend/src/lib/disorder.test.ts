import { describe, expect, it } from "vitest";
import type { CoverageAssessment, CoverageCheck, DisorderPrediction, Sequence, TargetDomain } from "../api/types";
import {
  assessmentMap, coverageBadge, coverageFootnote, disorderSummary, featureTracks, hitAssessment, hitRanges, lowComplexitySummary, modelingAccession,
  modelingCoverageRequest, sortedWarnings, span, targetAccession, TRACK_COLOR,
} from "./disorder";

// Risposte di /api/bio/disorder come le calcola il backend (tests/test_disorder.py): α-sinucleina (P37840) e ubiquitina
function prediction(extra: Partial<DisorderPrediction> = {}): DisorderPrediction {
  return {
    method: "FoldIndex (Prilusky et al. 2005)", length: 140, window: 51,
    scale: { name: "FoldIndex", threshold: 0, disordered: "below", formula: "2,785·⟨H⟩ − |⟨R⟩| − 1,151", min: -0.6862, max: 0.3732 },
    scores: [],
    regions: [{ start: 98, end: 140, length: 43, disordered: 43, mean_score: -0.3795 }],
    fraction: 0.3071, below_fraction: 0.3071,
    params: { window: 51, threshold: 0, min_region: 30, max_gap: 3 },
    low_complexity: {
      method: "SEG (Wootton & Federhen 1993)", params: { window: 12, k1: 2.2, k2: 2.5 }, fraction: 0.214,
      regions: [{ start: 10, end: 23, length: 14, composition: "A 4, K 4, E 2" }, { start: 63, end: 78, length: 16, composition: "V 6, A 3, G 3" }],
    },
    limits: ["Predizione dalla sola sequenza"],
    ...extra,
  };
}

const ubiquitin = () => prediction({ length: 76, regions: [], fraction: 0, low_complexity: { ...prediction().low_complexity, regions: [], fraction: 0 } });
const domain = (start: number, end: number, name = "Ubiquitin-like domain"): TargetDomain => ({ accession: "IPR000626", accessions: ["IPR000626", "IPR019956"], name, type: "domain", start, end });

describe("tracce della mappa delle caratteristiche", () => {
  it("disordine e bassa complessità sempre, domini solo se noti, GOR con il colore dello stato", () => {
    const tracks = featureTracks({ disorder: prediction(), domains: null, gor: [{ type: "helix", start: 5, end: 20 }, { type: "strand", start: 40, end: 45 }] });
    expect(tracks.map((t) => t.label)).toEqual(["Disordine (FoldIndex)", "Bassa complessità (SEG)", "Str. secondaria (GOR)"]);
    expect(tracks.every((t) => t.label.length <= 24)).toBe(true); // FeatureMap taglia le etichette a 24 caratteri
    const [dis, lcr, gor] = tracks;
    expect(dis.items).toEqual([{ start: 98, end: 140, title: "Regione disordinata predetta 98–140 (43 residui, FoldIndex medio -0,38)" }]);
    expect(dis.color).toBe(TRACK_COLOR.disorder);
    expect(lcr.items.map((i) => [i.start, i.end, i.title])).toEqual([[10, 23, "Bassa complessità 10–23: A 4, K 4, E 2"], [63, 78, "Bassa complessità 63–78: V 6, A 3, G 3"]]);
    expect(gor.items.map((i) => i.color)).toEqual([TRACK_COLOR.helix, TRACK_COLOR.strand]);
    expect(gor.items[1].title).toBe("Filamento β predetto 40–45");
  });
  it("tracce vuote con il loro testo; i domini con tutte le voci InterPro fuse", () => {
    const tracks = featureTracks({ disorder: ubiquitin(), domains: [domain(1, 76)] });
    expect(tracks.map((t) => [t.label, t.items.length, t.empty])).toEqual([
      ["Disordine (FoldIndex)", 0, "nessuna regione di almeno 30 residui"],
      ["Bassa complessità (SEG)", 0, "nessuna"],
      ["Domini (InterPro)", 1, "nessun dominio annotato"],
    ]);
    expect(tracks[2].items[0].title).toBe("Ubiquitin-like domain (IPR000626, IPR019956)");
    expect(featureTracks({ disorder: null, domains: [], gor: null }).map((t) => t.label)).toEqual(["Domini (InterPro)"]);
    expect(featureTracks({})).toEqual([]);
  });
  it("sintesi leggibili", () => {
    expect(disorderSummary(prediction())).toBe("Regione disordinata predetta: 98–140 (31% dei residui).");
    expect(disorderSummary(ubiquitin())).toBe("Nessuna regione disordinata lunga (almeno 30 residui con FoldIndex < 0).");
    const two = prediction({ regions: [{ start: 1, end: 40, length: 40, disordered: 38, mean_score: -0.2 }, { start: 98, end: 140, length: 43, disordered: 43, mean_score: -0.38 }], fraction: 0.593 });
    expect(disorderSummary(two)).toBe("2 regioni disordinate predette: 1–40, 98–140 (59% dei residui).");
    expect(lowComplexitySummary(prediction())).toBe("Bassa complessità: 10–23 (A 4, K 4, E 2), 63–78 (V 6, A 3, G 3).");
    expect(lowComplexitySummary(ubiquitin())).toBe("Nessuna regione a bassa complessità.");
    expect(span(5, 5)).toBe("5");
  });
});

describe("richieste di verifica", () => {
  const SNCA = "MDVFMKGLSKAKEGVVAAAEKTKQGVAEAAGKTKEGVLYVGSKTKEGVVHGVATVAEKTKEQV";
  const seqs = [
    { sequence: "MQIFVKTLTG", meta: {} },
    { sequence: `${SNCA.slice(0, 30)}\n${SNCA.slice(30)}`, meta: { accession: "p37840" } },
  ] as Pick<Sequence, "sequence" | "meta">[];
  it("accessione dalla sequenza del progetto uguale, poi dall'intestazione UniProt del nome", () => {
    expect(targetAccession({ name: "target", sequence: SNCA.toLowerCase() }, seqs)).toBe("P37840");
    expect(targetAccession({ name: "sp|P0CG48|UBC_HUMAN", sequence: "MQIFVKTLTG" }, seqs)).toBe("P0CG48");
    expect(targetAccession({ name: "target", sequence: "MQIFVKTLTG" }, seqs)).toBe("");
    expect(targetAccession({ name: "x", sequence: "" }, null)).toBe("");
  });
  it("modellazione con un PIR: l'accessione è quella del bersaglio del documento, non del selettore nascosto", () => {
    const pir = `>P1;1ABC_A\nstructureX:1ABC_A:1:A:+64:A:::-1.00:-1.00\n${SNCA}*\n\n>P1;target\nsequence:target:::::::0.00:0.00\n`
      + `${SNCA.slice(0, 30)}-\n${SNCA.slice(30)}*\n`;
    // arrivo dall'Editor PIR: selettore vuoto, bersaglio del PIR = sequenza del progetto importata da UniProt
    expect(modelingAccession({ mode: "pir", target: { name: "", sequence: "" }, pirText: pir }, seqs)).toBe("P37840");
    // una sequenza scelta prima in modalità automatica (un'altra proteina) non conta in modalità PIR
    const other = { name: "sp|P0CG48|UBC_HUMAN", sequence: "MQIFVKTLTG" };
    expect(modelingAccession({ mode: "pir", target: other, pirText: pir }, seqs)).toBe("P37840");
    expect(modelingAccession({ mode: "auto", target: other, pirText: pir }, seqs)).toBe("P0CG48");
    // intestazione UniProt nel codice del bersaglio; nessun PIR: nessuna accessione
    const named = ">P1;sp|P0CG48|UBC_HUMAN\nsequence:x:::::::0.00:0.00\nMQIF*\n";
    expect(modelingAccession({ mode: "pir", target: other, pirText: named }, [])).toBe("P0CG48");
    expect(modelingAccession({ mode: "pir", target: other, pirText: null }, seqs)).toBe("");
  });
  it("modellazione: sequenza e template con catene, oppure il PIR; null finché non è pronta", () => {
    const base = { mode: "auto" as const, target: { name: "t", sequence: "mqif vktl\n" }, pirText: null, accession: "", plddtMin: null };
    expect(modelingCoverageRequest({ ...base, templates: [{ id: "1UBQ", chains: ["A"], hetatm: true }, { id: "2ABC", chains: [] }] }))
      .toEqual({ sequence: "MQIFVKTL", accession: null, templates: [{ id: "1UBQ", chains: ["A"] }], plddt_min: null });
    expect(modelingCoverageRequest({ ...base, templates: [{ id: "2ABC", chains: [] }] })).toBeNull();
    expect(modelingCoverageRequest({ ...base, target: { name: "t", sequence: "" }, templates: [{ id: "1UBQ", chains: ["A"] }] })).toBeNull();
    expect(modelingCoverageRequest({ ...base, templates: [{ id: "AF-P0CG48-F1", chains: ["A"] }], accession: "P0CG48", plddtMin: 70 }))
      .toEqual({ sequence: "MQIFVKTL", accession: "P0CG48", templates: [{ id: "AF-P0CG48-F1", chains: ["A"] }], plddt_min: 70 });
    const pir = ">P1;t\nsequence:t:::::::0.00:0.00\nMQIF*\n";
    expect(modelingCoverageRequest({ ...base, mode: "pir", templates: [], pirText: pir })).toEqual({ alignment: pir, accession: null, plddt_min: null });
    expect(modelingCoverageRequest({ ...base, mode: "pir", templates: [], pirText: "" })).toBeNull();
    expect(modelingCoverageRequest({ ...base, mode: "pir", templates: [], pirText: "non un PIR" })).toBeNull();
  });
  it("ricerca: un intervallo per hit, scartati quelli non validi", () => {
    expect(hitRanges([
      { template_id: "1XQ8_A", query_start: 1, query_end: 95 },
      { template_id: "bad", query_start: 40, query_end: 10 },
      { template_id: "nan", query_start: Number.NaN, query_end: 10 },
    ])).toEqual([{ label: "1XQ8_A", start: 1, end: 95 }]);
  });
});

describe("avvisi di copertura", () => {
  const assessment: CoverageAssessment = {
    coverage: 67.9, covered: [[1, 95]], suggestion: "Valuta di escludere…", domains: [],
    warnings: [
      { kind: "disorder_covered", tone: "info", start: 1, end: 30, text: "coperta" },
      { kind: "domain_partial", tone: "warn", start: 80, end: 121, text: "in parte" },
      { kind: "disorder_uncovered", tone: "warn", start: 98, end: 140, text: "scoperta" },
      { kind: "domain_uncovered", tone: "warn", start: 150, end: 200, text: "dominio scoperto" },
      { kind: "domain_uncovered", tone: "warn", start: 210, end: 260, text: "altro dominio" },
    ],
  };
  it("prima gli avvisi, poi le informazioni, per posizione", () => {
    expect(sortedWarnings(assessment).map((w) => w.text)).toEqual(["in parte", "scoperta", "dominio scoperto", "altro dominio", "coperta"]);
    expect(sortedWarnings(null)).toEqual([]);
  });
  it("etichetta breve per la riga dell'hit, senza ripetizioni; nulla con le sole informazioni", () => {
    expect(coverageBadge(assessment)).toEqual({ label: "dominio in parte · disordine scoperto · dominio scoperto", tone: "warn", title: "in parte\nscoperta\ndominio scoperto\naltro dominio" });
    expect(coverageBadge({ warnings: [assessment.warnings[0]] })).toBeNull();
    expect(coverageBadge(undefined)).toBeNull();
  });
  it("valutazione di un hit per etichetta, esclusi i template non valutabili; riga di sintesi", () => {
    const check: CoverageCheck = {
      length: 140, note: null, disorder: prediction(), domains: [domain(1, 76)],
      domains_info: { source: "InterPro", accession: "P37840", available: true, note: null },
      templates: [{ label: "1XQ8_A", ...assessment }, { label: "9ZZZ", error: "Template 9ZZZ non presente nella libreria." }],
      combined: null,
    };
    expect(hitAssessment(check, "1XQ8_A")?.coverage).toBe(67.9);
    expect(hitAssessment(check, "9ZZZ")).toBeUndefined();
    expect(hitAssessment(null, "1XQ8_A")).toBeUndefined();
    const map = assessmentMap(check);
    expect([...map.keys()]).toEqual(["1XQ8_A"]);
    expect(map.get("1XQ8_A")).toBe(hitAssessment(check, "1XQ8_A"));
    expect(assessmentMap(null).size).toBe(0);
    expect(coverageFootnote(check)).toBe("Regione disordinata predetta: 98–140 (31% dei residui). Metodo: FoldIndex (Prilusky et al. 2005), dalla sola sequenza (indicativo). Domini da InterPro (P37840): Ubiquitin-like domain 1–76.");
    const noAcc = { ...check, domains: [], domains_info: { source: null, accession: null, available: false, note: "Nessuna accessione UniProt: i domini non sono noti." } };
    expect(coverageFootnote(noAcc)).toContain("Nessuna accessione UniProt");
  });
});
