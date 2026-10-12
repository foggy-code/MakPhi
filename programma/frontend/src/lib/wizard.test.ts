import { describe, expect, it } from "vitest";
import type { Align2dResult, DatabaseStatus, Job, ModelResult, SearchHit, SearchResult } from "../api/types";
import {
  accessionFor, align2dRequest, applyUrl, cleanSequence, decodeWizardParams, defaultOptions, encodeWizardParams, engineChoices, fillFromJobs, hitRole,
  initialState, isChosen, MAX_TEMPLATES, missingModelJob, modelRequest, nextEngineHint, optionNotes, optionsIssues, preflightFor, preselect, rankedHits, reachable,
  reasonedOptions, restoreState, resultSummary, sanitizeDraft, searchRequest, sequenceIssues, templatesIssues, toggleTemplate, withAccession,
  withAlignJob, withModelJob, withOptions, withPirDoc, withSearchJob, withTarget, withTemplates, type WizardState,
} from "./wizard";

const YEAST_UB = "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG";
const HUMAN_UB = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG";

function hit(template_id: string, extra: Partial<SearchHit> = {}): SearchHit {
  const [pdb_id, chain] = template_id.split("_");
  return {
    pdb_id, chain, template_id, title: "", description: "", organism: "", method: "X-RAY DIFFRACTION", resolution: 1.8, identity: 96.1, positives: 98,
    coverage: 100, evalue: 1e-40, bitscore: 150, align_length: 76, query_start: 1, query_end: 76, subject_start: 1, subject_end: 76,
    query_aligned: "", subject_aligned: "", subject_length: 76, source: "rcsb", ...extra,
  };
}

function searchResult(hits: SearchHit[], extra: Partial<SearchResult> = {}): SearchResult {
  return { engine: "rcsb", engine_label: "RCSB", query_length: 76, hits, query: { name: "yeast_ub", sequence: YEAST_UB, length: 76 }, best: hits[0]?.template_id ?? null, ...extra };
}

function job<R>(id: string, kind: Job["kind"], params: Record<string, unknown>, result: R | null = null, status: Job["status"] = "completed"): Job<R> {
  return {
    id, kind, title: "", project_id: null, status, progress: 1, stage: "", error: null, created_at: "", started_at: null, finished_at: null, updated_at: "",
    params, result, files: [], resumable: false, resumable_items: null,
  };
}

/** Stato di una procedura arrivata al passo 4: bersaglio, ricerca, template, allineamento. */
function atOptions(): WizardState {
  let s = withTarget(initialState(), { name: "yeast_ub", sequence: YEAST_UB });
  s = withSearchJob(s, "s1");
  s = withTemplates(s, [{ id: "1UBQ", chains: ["A"] }]);
  return { ...withAlignJob(s, "a1"), step: 4 };
}

describe("passo 1: sequenza", () => {
  it("validazione: vuota, troppo lunga, acido nucleico", () => {
    expect(sequenceIssues({ sequence: "" })).toEqual(["Inserisci la sequenza bersaglio."]);
    expect(sequenceIssues({ sequence: "12 34 *\n" })).toEqual(["Inserisci la sequenza bersaglio."]);
    expect(sequenceIssues({ sequence: YEAST_UB })).toEqual([]);
    expect(sequenceIssues({ sequence: "L".repeat(5000) })).toEqual([]);
    expect(sequenceIssues({ sequence: "L".repeat(5001) })[0]).toContain("5001 residui, massimo 5000");
    // limite delle Impostazioni (max_sequence_length)
    expect(sequenceIssues({ sequence: "L".repeat(5001) }, 8000)).toEqual([]);
    expect(sequenceIssues({ sequence: "L".repeat(101) }, 100)[0]).toContain("101 residui, massimo 100");
    expect(sequenceIssues({ sequence: "ACGUACGUUUGACCAGUAGG" })[0]).toContain("acido nucleico");
    expect(cleanSequence(" mqif vk\n12*")).toBe("MQIFVK");
  });

  it("una sequenza diversa invalida ricerca, template, allineamento e modello; lo stesso bersaglio no", () => {
    const s = { ...atOptions(), modelJob: "m1" };
    const renamed = withTarget(s, { name: "altro nome", sequence: YEAST_UB.toLowerCase() });
    expect(renamed.searchJob).toBe("s1");
    expect(renamed.modelJob).toBe("m1");
    const other = withTarget(s, { name: "human", sequence: HUMAN_UB });
    expect([other.searchJob, other.templates, other.alignJob, other.modelJob, other.templatesFor]).toEqual([null, [], null, null, null]);
  });

  it("accessione: dall'intestazione di UniProt, conservata con la stessa sequenza, tolta con un'altra", () => {
    const s = withAccession(withTarget(initialState(), { name: "y", sequence: YEAST_UB }), " p0cg48 ");
    expect(s.accession).toBe("P0CG48");
    expect(accessionFor(s, { name: "sp|P0CG47|UBB_HUMAN", sequence: HUMAN_UB })).toBe("P0CG47");
    expect(accessionFor(s, { name: "incollata", sequence: YEAST_UB })).toBe("P0CG48");
    expect(accessionFor(s, { name: "incollata", sequence: HUMAN_UB })).toBe("");
    expect(withAccession(s, "  ").accession).toBe("");
  });
});

describe("passo 2: template", () => {
  const result = searchResult([hit("1UBQ_A", { rank_score: 95.3 }), hit("2XYZ_B", { rank_score: 40.1, identity: 30 }), hit("3ABC_A", { rank_score: 70 })], {
    suggested: {
      templates: [
        { template_id: "1UBQ_A", pdb_id: "1UBQ", chain: "A", rank_score: 95.3, identity: 96.1, coverage: 100, predicted: false, role: "principale", reason: "il migliore", new_residues: 76, ranges: "1–76" },
        { template_id: "3ABC_A", pdb_id: "3ABC", chain: "A", rank_score: 70, identity: 60, coverage: 50, predicted: false, role: "complementare", reason: "copre 40–76", new_residues: 10, ranges: "40–76" },
      ],
      excluded: [], not_examined: 0, coverage: 100, covered_residues: 76, query_length: 76, max_templates: 3, min_new_residues: 10, version: 1,
    },
  });

  it("ordine per punteggio, ruoli dei consigliati", () => {
    expect(rankedHits(result).map((h) => h.template_id)).toEqual(["1UBQ_A", "3ABC_A", "2XYZ_B"]);
    // senza punteggio (ricerche di versioni precedenti): per E-value
    expect(rankedHits(searchResult([hit("B_A", { evalue: 1e-5 }), hit("A_A", { evalue: 1e-20 })])).map((h) => h.template_id)).toEqual(["A_A", "B_A"]);
    expect(hitRole(result, result.hits[0])).toBe("principale");
    expect(hitRole(result, result.hits[2])).toBe("complementare");
    expect(hitRole(result, result.hits[1])).toBeNull();
  });

  it("i consigliati si preselezionano una volta per ricerca; poi decide l'utente", () => {
    let s = withSearchJob(withTarget(initialState(), { name: "y", sequence: YEAST_UB }), "s1");
    s = preselect(s, "s1", result);
    expect(s.templates).toEqual([{ id: "1UBQ", chains: ["A"] }, { id: "3ABC", chains: ["A"] }]);
    s = withTemplates(s, []);
    expect(preselect(s, "s1", result).templates).toEqual([]); // stessa ricerca: nessuna nuova preselezione
    const again = preselect(withSearchJob(s, "s2"), "s2", result);
    expect(again.templates.length).toBe(2);
  });

  it("scelta di un hit: una catena per voce PDB, massimo dei template", () => {
    let list = toggleTemplate([], hit("1UBQ_A"));
    expect(list).toEqual([{ id: "1UBQ", chains: ["A"] }]);
    expect(isChosen(list, hit("1UBQ_A"))).toBe(true);
    list = toggleTemplate(list, hit("1UBQ_B"));
    expect(list).toEqual([{ id: "1UBQ", chains: ["B"] }]);
    expect(toggleTemplate(list, hit("1UBQ_B"))).toEqual([]);
    const full = Array.from({ length: MAX_TEMPLATES }, (_, i) => ({ id: `${i}ABC`, chains: ["A"] }));
    expect(toggleTemplate(full, hit("9ZZZ_A"))).toBe(full);
    expect(templatesIssues([])).toEqual(["Scegli almeno un template."]);
    expect(templatesIssues([{ id: "1UBQ", chains: [] }])).toEqual(["Ogni template deve avere una catena."]);
  });

  it("template cambiati: allineamento e modello da rifare", () => {
    const s = { ...atOptions(), modelJob: "m1" };
    expect(withTemplates(s, [{ id: "1UBQ", chains: ["A"] }])).toBe(s);
    const changed = withTemplates(s, [{ id: "1UBQ", chains: ["A"] }, { id: "3ABC", chains: ["A"] }]);
    expect([changed.alignJob, changed.modelJob, changed.searchJob]).toEqual([null, null, "s1"]);
  });

  it("suggerimento dopo la ricerca", () => {
    expect(nextEngineHint(searchResult([]), "")).toContain("Nessun template: prova NCBI BLAST");
    expect(nextEngineHint(searchResult([]), "P0CG48")).toContain("accessione P0CG48");
    expect(nextEngineHint(searchResult([hit("2XYZ_B", { rank_score: 35, rank_twilight: true })]), "")).toContain("in zona crepuscolare");
    expect(nextEngineHint(searchResult([hit("2XYZ_B", { rank_score: 35 })]), "")).toContain("fascia scarsa");
    expect(nextEngineHint(searchResult([hit("1UBQ_A", { rank_score: 95 })]), "")).toBeNull();
    expect(nextEngineHint(searchResult([], { engine: "ncbi" }), "")).toBe("Nessun template: prova AlphaFold DB (modelli predetti).");
  });

  it("motore consigliato: RCSB, oppure il motore locale delle Impostazioni se è pronto", () => {
    const ready: DatabaseStatus = {
      pdbaa: { present: true, usable: true, blast: true },
      mmseqs: { present: true, usable: true, tool: true, indexed_here: true, index_size: 1 },
      qmean: { configured: false, reachable: false, available: false, message: null, databases: [], archives: [], unsupported: [] },
    };
    const none = engineChoices({ databases: null, defaultEngine: "mmseqs", accession: "" });
    expect(none.recommended).toBe("rcsb");
    expect(none.choices.find((c) => c.engine === "mmseqs")?.available).toBe(false);
    const local = engineChoices({ databases: ready, defaultEngine: "mmseqs", accession: "" });
    expect(local.recommended).toBe("mmseqs");
    expect(local.choices.find((c) => c.engine === "mmseqs")?.note).toContain("circa 2 s");
    const af = engineChoices({ databases: ready, defaultEngine: "rcsb", accession: "P0CG48" });
    expect(af.recommended).toBe("rcsb");
    expect(af.choices.find((c) => c.engine === "alphafold")?.note).toContain("accessione P0CG48");
    expect(af.choices.map((c) => c.engine)).toEqual(["rcsb", "ncbi", "mmseqs", "local", "alphafold"]);
  });
});

describe("passo 4: opzioni", () => {
  const ctx = { predicted: false, ligandNames: [], procheck: true, prosa: false };

  it("predefiniti ragionati: 5 modelli, Rapido, analisi estesa se c'è uno strumento, ligandi se i template ne hanno", () => {
    const o = reasonedOptions(ctx);
    expect([o.models, o.refinement, o.quality, o.extended, o.ligands, o.plddtTrim, o.plddtMin]).toEqual([5, "fast", "best", true, false, true, 70]);
    expect(reasonedOptions({ ...ctx, procheck: false }).extended).toBe(false);
    expect(reasonedOptions({ ...ctx, ligandNames: ["ZN"] }).ligands).toBe(true);
    const notes = optionNotes(o, { ...ctx, ligandNames: ["ZN"] });
    expect(notes.models).toContain("tutorial di MODELLER");
    expect(notes.refinement).toContain("impostazioni predefinite di MODELLER");
    // quattro livelli distinti (prima Rapido e Standard coincidevano)
    const levels = (["very_fast", "fast", "normal", "thorough"] as const).map((r) => optionNotes({ ...o, refinement: r }, ctx).refinement);
    expect(new Set(levels).size).toBe(4);
    expect(levels[2]).toContain("refine.fast");
    expect(levels[0]).toContain("i 5 modelli saranno identici");
    expect(optionNotes({ ...o, refinement: "very_fast", models: 1 }, ctx).refinement).not.toContain("saranno identici");
    expect(notes.quality).toContain("PROCHECK");
    expect(notes.ligands).toContain("ZN");
    expect(notes.plddt).toContain("Nessun template AlphaFold");
    expect(optionNotes({ ...o, refinement: "thorough" }, ctx).refinement).toContain("4 volte");
    expect(optionNotes(o, { ...ctx, predicted: true }).plddt).toContain("pLDDT < 70");
  });

  it("validazione e invalidazione del modello", () => {
    expect(optionsIssues(defaultOptions())).toEqual([]);
    expect(optionsIssues({ ...defaultOptions(), models: 0 })[0]).toContain("fra 1 e 100");
    expect(optionsIssues({ ...defaultOptions(), models: 2.5 })[0]).toContain("intero");
    expect(optionsIssues({ ...defaultOptions(), plddtMin: 120 })[0]).toContain("pLDDT");
    const s = { ...atOptions(), modelJob: "m1" };
    const byUser = withOptions(s, { models: 10 });
    expect([byUser.options.models, byUser.optionsTouched, byUser.modelJob]).toEqual([10, true, null]);
    const byDefaults = withOptions(s, { extended: false }, false);
    expect([byDefaults.optionsTouched, byDefaults.modelJob]).toEqual([false, "m1"]);
    expect(withOptions(s, { models: 5 })).toBe(s); // nessun cambiamento
  });

  it("richieste: ricerca, allineamento, modellazione con l'allineamento del passo 3 e i ligandi scelti", () => {
    const s = { ...atOptions(), accession: "P0CG48", engine: "alphafold" as const };
    expect(searchRequest(s, "p1")).toEqual({ project_id: "p1", query: { name: "yeast_ub", sequence: YEAST_UB }, engine: "alphafold", evalue: 1e-3, max_hits: 50, uniprot: "P0CG48" });
    expect(searchRequest({ ...s, engine: "rcsb" }, null).uniprot).toBeUndefined();
    expect(align2dRequest(s, null)).toEqual({ project_id: undefined, target: { name: "yeast_ub", sequence: YEAST_UB }, templates: [{ id: "1UBQ", chains: ["A"] }] });
    const withLigands = withOptions(s, { ligands: true });
    const body = modelRequest(withLigands, { projectId: "p1", alignment: ">P1;…", ligandRefs: [{ template: "1UBQ", chain: "A", resnum: "101", resname: "ZN" }] });
    expect(body.target).toEqual({ name: "yeast_ub", sequence: YEAST_UB }); // dà il nome al job; l'allineamento prevale
    expect(body.alignment).toBe(">P1;…");
    expect(body.options).toEqual({ models: 5, refinement: "fast", plddt_trim: true, plddt_min: 70, hetatm: true, ligands: [{ template: "1UBQ", chain: "A", resnum: "101", resname: "ZN" }] });
    expect(body.extended_quality).toBe(true);
    // template del documento PIR (Editor PIR) al posto di quelli scelti
    expect(modelRequest(s, { projectId: null, alignment: "x", templates: [{ id: "2XYZ", chains: ["B"] }] }).templates).toEqual([{ id: "2XYZ", chains: ["B"] }]);
    const none = modelRequest(withOptions(s, { quality: "none" }), { projectId: null, alignment: null });
    expect([none.target?.sequence, none.extended_quality, "ligands" in none.options]).toEqual([YEAST_UB, false, false]);
    expect(preflightFor("model", s, body)).toEqual({ kind: "model", params: body, context: { accession: "P0CG48", search_job: "s1" } });
    expect(preflightFor("search", s, {}).context?.search_job).toBeNull();
  });
});

describe("passi raggiungibili", () => {
  it("ogni passo richiede i dati dei precedenti", () => {
    const none = { align: false, model: false };
    expect(reachable(initialState(), none)).toBe(1);
    const seq = withTarget(initialState(), { name: "y", sequence: YEAST_UB });
    expect(reachable(seq, none)).toBe(2);
    const tpl = withTemplates(seq, [{ id: "1UBQ", chains: ["A"] }]);
    expect(reachable(tpl, none)).toBe(3);
    expect(reachable(withAlignJob(tpl, "a1"), none)).toBe(3); // allineamento non ancora completato
    expect(reachable(withAlignJob(tpl, "a1"), { align: true, model: false })).toBe(4);
    expect(reachable(withPirDoc(tpl, "d1"), none)).toBe(4); // documento PIR del progetto
    const launched = withModelJob(withAlignJob(tpl, "a1"), "m1");
    expect(reachable(launched, { align: true, model: false })).toBe(5);
    expect(reachable(launched, { align: true, model: true })).toBe(6);
    expect(reachable(withOptions(launched, { models: 0 }, false), { align: true, model: true })).toBe(4);
  });

  it("passo 5 con un job di modellazione che non c'è più: avviso invece della rotella", () => {
    // bozza con modelJob di un job eliminato: il passo 5 resta raggiungibile, l'errore 404 va mostrato
    const launched = withModelJob(withAlignJob(withTemplates(withTarget(initialState(), { name: "y", sequence: YEAST_UB }), [{ id: "1UBQ", chains: ["A"] }]), "a1"), "m1");
    expect(reachable(launched, { align: true, model: false })).toBe(5);
    expect(missingModelJob(false, "Job m1 non trovato.", true)).toBe("Il job di modellazione non è più disponibile (eliminato o ripulito): Job m1 non trovato.");
    expect(missingModelJob(false, "Failed to fetch", false)).toContain("non si legge: Failed to fetch");
    expect(missingModelJob(false, null, false)).toBeNull(); // si sta ancora leggendo: rotella
    expect(missingModelJob(true, "errore passato", false)).toBeNull(); // lo stato c'è: il pannello del job
  });

  it("nuovo allineamento o documento: il modello va rifatto", () => {
    const s = { ...atOptions(), modelJob: "m1" };
    expect(withAlignJob(s, "a2").modelJob).toBeNull();
    const doc = withPirDoc(s, "d1");
    expect([doc.pirDoc, doc.modelJob, doc.alignJob]).toEqual(["d1", null, "a1"]);
    expect(withAlignJob(doc, "a3").pirDoc).toBeNull();
  });
});

describe("ripresa dall'URL e dalla bozza", () => {
  it("URL: passo e job; valori non validi ignorati", () => {
    const s = { ...atOptions(), modelJob: "m1", pirDoc: null };
    const p = encodeWizardParams(s);
    expect(p.toString()).toBe("step=4&search=s1&align=a1&model=m1");
    expect(decodeWizardParams(p)).toEqual({ step: 4, searchJob: "s1", alignJob: "a1", pirDoc: undefined, modelJob: "m1" });
    expect(decodeWizardParams(new URLSearchParams("step=9&search=../x"))).toEqual({ step: 6, searchJob: null, alignJob: undefined, pirDoc: undefined, modelJob: undefined });
    expect(decodeWizardParams(new URLSearchParams("step=abc")).step).toBeUndefined();
  });

  it("indietro nel browser: cambia il passo e restano i job che l'URL non nomina", () => {
    const s = { ...atOptions(), modelJob: "m1", step: 6 as const };
    const back = applyUrl(s, new URLSearchParams("step=5"));
    expect([back.step, back.modelJob, back.alignJob]).toEqual([5, "m1", "a1"]);
    expect(applyUrl(s, encodeWizardParams(s))).toBe(s); // stesso URL: stesso stato
    // un'altra ricerca nell'URL (link): i passi successivi si azzerano
    const other = applyUrl(s, new URLSearchParams("step=2&search=s9"));
    expect([other.step, other.searchJob, other.templates, other.alignJob, other.modelJob]).toEqual([2, "s9", [], null, null]);
  });

  it("bozza del browser: ripresa, valori non validi riportati ai predefiniti, URL che prevale", () => {
    const s = withOptions({ ...atOptions(), accession: "P0CG48" }, { models: 7, ligandSel: { "1UBQ|A|101|ZN": true } });
    const draft = JSON.parse(JSON.stringify(s));
    expect(sanitizeDraft(draft)).toEqual(s);
    expect(restoreState(new URLSearchParams("step=3"), draft).step).toBe(3);
    expect(restoreState(new URLSearchParams(""), null)).toEqual(initialState());
    const broken = sanitizeDraft({ step: "x", target: 5, accession: "non-valida", engine: "boh", templates: [{ id: 1 }, { id: "1UBQ", chains: ["A", 3] }], searchJob: "../x",
      options: { models: 1000, refinement: "velocissimo", quality: "tutti", extended: "sì", plddtMin: -5, ligandSel: { a: true, b: "no" } } });
    expect(broken).toMatchObject({ step: 1, target: { name: "", sequence: "" }, accession: "", engine: "rcsb", templates: [{ id: "1UBQ", chains: ["A"] }], searchJob: null });
    expect(broken?.options).toEqual({ ...defaultOptions(), ligandSel: { a: true } });
    expect(sanitizeDraft("testo")).toBeNull();
    expect(sanitizeDraft(null)).toBeNull();
  });

  it("link senza bozza: bersaglio, template e opzioni dai job", () => {
    const s = restoreState(new URLSearchParams("step=6&search=s1&align=a1&model=m1"), null);
    expect(s.target.sequence).toBe("");
    const search = job<SearchResult>("s1", "search", { query: { name: "yeast_ub", sequence: YEAST_UB }, engine: "mmseqs", uniprot: "" });
    const align = job<Align2dResult>("a1", "align2d", { target: { name: "yeast_ub", sequence: YEAST_UB }, templates: [{ id: "1UBQ", chains: ["A"] }] });
    const model = job<ModelResult>("m1", "model", { options: { models: 3, refinement: "thorough", plddt_trim: true, plddt_min: 80, hetatm: true }, quality: "all", extended_quality: false });
    const filled = fillFromJobs(s, { search, align, model });
    expect(filled.target).toEqual({ name: "yeast_ub", sequence: YEAST_UB });
    expect(filled.engine).toBe("mmseqs");
    expect(filled.templates).toEqual([{ id: "1UBQ", chains: ["A"] }]);
    expect(filled.options).toMatchObject({ models: 3, refinement: "thorough", quality: "all", extended: false, ligands: true, plddtMin: 80 });
    expect(reachable(filled, { align: true, model: true })).toBe(6);
    // con la bozza (bersaglio già noto) motore e opzioni scelti restano
    const kept = fillFromJobs(withOptions({ ...filled, engine: "rcsb" }, { models: 9 }), { search, model });
    expect([kept.engine, kept.options.models]).toEqual(["rcsb", 9]);
    // job di un'altra procedura (id diverso): ignorati
    expect(fillFromJobs(initialState(), { search })).toEqual(initialState());
  });
});

describe("passo 6: risultati", () => {
  it("modello migliore, voto e consenso del suo rapporto", () => {
    const result = {
      models: [
        { file: "t.B99990001.pdb", failure: null, molpdf: 500, dope: -8000, ga341: 1, zdope: -2.5, kind: "model", best: false },
        { file: "t.B99990002.pdb", failure: null, molpdf: 480, dope: -8350, ga341: 1, zdope: -2.73, kind: "model", best: true,
          quality: { grade: "A", grade_score: 98.9, rama_favored: 96, rama_outliers: 0, clashscore: 1.7, helix_pct: 20, strand_pct: 30, dope: -8350, zdope: -2.73, residues: 76 } },
        { file: "t.B99990003.pdb", failure: "optimization failed", molpdf: null, dope: null, zdope: null, kind: "model", best: false },
      ],
      warnings: ["Orientamento relativo non vincolato"],
    } as unknown as ModelResult;
    const s = resultSummary(result, { consensus: { score: 97.0, band: "buona" } as never });
    expect(s).toMatchObject({ file: "t.B99990002.pdb", grade: "A", gradeScore: 98.9, dope: -8350, consensus: 97.0, consensusBand: "buona", models: 2 });
    expect(s?.warnings).toEqual(["Orientamento relativo non vincolato"]);
    expect(resultSummary(result, null)?.consensus).toBeNull();
    expect(resultSummary({ ...result, models: [] } as ModelResult)).toBeNull();
    expect(resultSummary(null)).toBeNull();
  });
});
