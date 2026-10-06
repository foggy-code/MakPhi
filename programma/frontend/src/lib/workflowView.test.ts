import { describe, expect, it } from "vitest";
import type { SearchHit, TemplateItem } from "../api/types";
import {
  compositionParts, compositionRows, hitTracks, numberedSequence, optionsSummary, rankContributions, residueRanges, resolutionNote, resolutionTone,
  ssLabel, ssParts, stepSummary, templateLabel, templateMatches, uniprotCommentLabel,
} from "./workflowView";
import { defaultOptions, initialState, withAlignJob, withModelJob, withPirDoc, withSearchJob, withTarget, withTemplates } from "./wizard";

const hit = (template_id: string, extra: Partial<SearchHit> = {}): SearchHit => ({
  pdb_id: template_id.split("_")[0], chain: template_id.split("_")[1] ?? "A", template_id, title: "", description: "",
  organism: "", method: "X-RAY DIFFRACTION", resolution: 1.8, identity: 96.1, positives: null, coverage: 100,
  evalue: 1e-30, bitscore: 150, align_length: 76, query_start: 1, query_end: 76, subject_start: 1, subject_end: 76,
  query_aligned: "", subject_aligned: "", subject_length: 76, source: "rcsb", ...extra,
});

describe("sequenza e composizione", () => {
  it("righe di 60 in blocchi di 10 con la posizione allineata a destra", () => {
    const seq = "A".repeat(125);
    const lines = numberedSequence(seq).split("\n");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe(`  1  ${Array(6).fill("A".repeat(10)).join(" ")}`);
    expect(lines[2]).toBe("121  AAAAA");
    expect(numberedSequence("")).toBe("");
  });

  it("classi in percentuale sulla lunghezza, righe per conteggio con gli assenti a zero", () => {
    const comp = { A: 2, K: 1, D: 1, S: 1, G: 3 };
    const parts = compositionParts(comp, 10);
    expect(parts.map((p) => [p.label, p.value])).toEqual([["Idrofobici", 20], ["Carichi +", 10], ["Carichi −", 10], ["Polari", 10], ["G, P, C", 30]]);
    const rows = compositionRows(comp, 10);
    expect(rows).toHaveLength(20);
    expect(rows[0]).toEqual({ letter: "G", name: "Glicina", group: "G, P, C", count: 3, pct: 30 });
    expect(rows[1].letter).toBe("A");
    expect(rows[rows.length - 1].count).toBe(0);
  });

  it("titoli dei commenti di UniProtKB", () => {
    expect(uniprotCommentLabel("SUBCELLULAR LOCATION")).toBe("Localizzazione");
    expect(uniprotCommentLabel("WEB RESOURCE")).toBe("Web resource");
  });

  it("struttura secondaria a tre stati in percentuale, con i nomi comuni (stato E: filamento β)", () => {
    expect(ssParts("HHHHEECCCC").map((p) => p.label)).toEqual(["Elica", "Filamento β", "Coil"]);
    expect(ssLabel("E")).toBe("Filamento β");
    expect(ssParts("HHHHEECCCC").map((p) => p.value)).toEqual([40, 20, 40]);
    expect(ssParts("").map((p) => p.value)).toEqual([0, 0, 0]);
    expect(ssParts("HT-").map((p) => Math.round(p.value))).toEqual([33, 0, 67]); // tutto ciò che non è H o E è coil
  });
});

describe("template", () => {
  it("residui mancanti a intervalli per catena, codici di inserzione a parte", () => {
    const list = ["1", "2", "3", "7", "52A", "53", "54"].map((resnum) => ({ chain: "A", resnum }));
    expect(residueRanges([...list, { chain: "B", resnum: "-1" }, { chain: "B", resnum: "0" }])).toBe("A 1–3, 7, 52A, 53–54; B -1–0");
    expect(residueRanges([])).toBe("");
  });

  it("fasce di risoluzione come nel punteggio dei template", () => {
    expect(resolutionTone(1.8)).toBe("ok");
    expect(resolutionTone(2.5)).toBe("ok");
    expect(resolutionTone(3.1)).toBe("warn");
    expect(resolutionTone(4.2)).toBe("err");
    expect(resolutionTone(null)).toBeUndefined();
    expect(resolutionNote(3.1)).toContain("catene laterali incerte");
    expect(resolutionNote(undefined)).toContain("non disponibile");
  });

  it("filtro della libreria su codice, titolo, AlphaFold ed etichette", () => {
    const t = { id: "AF-P69905-F1", format: "pdb", size: 1, source: "alphafold", added_at: null, title: "Hemoglobin subunit alpha", method: "PREDICTED",
      resolution: null, chains: ["A"], alphafold: { gene: "HBA1", organism: "Homo sapiens" }, tags: ["globine"] } as unknown as TemplateItem;
    expect(templateMatches(t, "")).toBe(true);
    expect(templateMatches(t, "hemoglobin")).toBe(true);
    expect(templateMatches(t, "hba1")).toBe(true);
    expect(templateMatches(t, "GLOBINE")).toBe(true);
    expect(templateMatches(t, "1ubq")).toBe(false);
    expect(templateLabel({ id: "1UBQ", chains: ["A", "B"] })).toBe("1UBQ:AB");
    expect(templateLabel({ id: "1UBQ", chains: [] })).toBe("1UBQ");
  });
});

describe("componenti del punteggio", () => {
  it("punti come media pesata delle componenti conteggiate: sommano al punteggio", () => {
    const rows = rankContributions([
      { key: "ligands", label: "Ligandi e cofattori", score: null, weight: 0, text: "olo" },
      { key: "coverage", label: "Copertura del bersaglio", score: 50, weight: 30, text: "metà" },
      { key: "identity", label: "Identità", score: 100, weight: 30, text: "identica" },
      { key: "rfree", label: "R-free", score: null, weight: 5, text: "non applicabile" },
    ]);
    expect(rows.map((r) => [r.key, r.points])).toEqual([["coverage", 25], ["identity", 50], ["rfree", null], ["ligands", null]]);
    expect(rankContributions(undefined)).toEqual([]);
  });
});

describe("mappa di copertura degli hit", () => {
  it("una traccia per hit, colore dalla fascia del punteggio, al massimo `limit`", () => {
    const tracks = hitTracks([
      hit("1UBQ_A", { rank_score: 82, query_start: 3, query_end: 70 }),
      hit("2XYZ_B", { rank_score: 30 }),
      hit("3OLD_A"),
    ], 2);
    expect(tracks).toHaveLength(2);
    expect(tracks[0]).toMatchObject({ label: "1UBQ_A", color: "var(--ok)", items: [{ start: 3, end: 70 }] });
    expect(tracks[0].items[0].title).toContain("identità 96,1%");
    expect(tracks[0].items[0].title).toContain("punteggio 82,0");
    expect(tracks[1].color).toBe("var(--err)");
    expect(hitTracks([hit("3OLD_A")])[0].color).toBe("var(--accent)"); // senza punteggio (versioni precedenti)
  });
});

describe("riepilogo della procedura guidata", () => {
  it("un testo per passo solo dopo la scelta", () => {
    let s = initialState();
    expect(stepSummary(s, 1)).toBeNull();
    s = withTarget(s, { name: "yeast_ub", sequence: "MQIF VKTL" });
    expect(stepSummary(s, 1)).toBe("yeast_ub · 8 aa");
    expect(stepSummary(s, 2)).toBeNull();
    s = withTemplates(withSearchJob(s, "s1"), [{ id: "1UBQ", chains: ["A"] }, { id: "4HHB", chains: ["B"] }]);
    expect(stepSummary(s, 2)).toBe("1UBQ:A, 4HHB:B");
    expect(stepSummary(s, 3)).toBeNull();
    expect(stepSummary(s, 4)).toBeNull();
    s = withAlignJob(s, "a1");
    expect(stepSummary(s, 3)).toBe("MODELLER align2d");
    expect(stepSummary(s, 4)).toBe("5 modelli · Rapido");
    expect(stepSummary(withPirDoc(s, "d1"), 3)).toBe("documento PIR del progetto");
    expect(stepSummary(withModelJob(s, "m1"), 5)).toBe("modellazione avviata");
    expect(stepSummary(s, 6)).toBeNull();
  });

  it("opzioni in una riga", () => {
    const o = defaultOptions();
    expect(optionsSummary(o)).toBe("5 modelli · Rapido · qualità sul migliore con PROCHECK e ProSA");
    expect(optionsSummary({ ...o, models: 1, quality: "none", ligands: true }, true)).toBe("1 modello · Rapido · senza analisi di qualità · ligandi trasferiti · ritaglio pLDDT < 70");
  });
});
