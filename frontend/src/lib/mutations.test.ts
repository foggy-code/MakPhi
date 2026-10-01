import { describe, expect, it } from "vitest";
import type { AlaScanResult, AlaScanRow, MappedVariant, MutationClass, MutationHistoryItem, MutationSite, VariantMapping } from "../api/types";
import { decodeSource, linkedSources } from "./compare";
import { coloringMap } from "./residueColoring";
import {
  addNotation, CLASS_COLOR, dopeText, energyText, formatMutation, historyOption, historyText, modelQualityLink, mutateRequest, mutationCompareLinks,
  mutationPreflight, OUTSIDE_COLOR, parseMutations, pocketsView, scanColoring, scanRequest, scanRowOf, scanRows, siteSummary, sitesFocus, variantRows,
  variantText,
} from "./mutations";

const UBQ = { type: "template" as const, id: "1UBQ" };

describe("notazione delle mutazioni", () => {
  it("forme ammesse, come il backend", () => {
    const { items, error } = parseMutations("A:K48R, A:LYS63ARG;  i30p\n B:G52AA, _:M-1A");
    expect(error).toBeNull();
    expect(items.map(formatMutation)).toEqual(["A:K48R", "A:K63R", "I30P", "B:G52AA", "_:M-1A"]);
    expect(items[3]).toMatchObject({ chain: "B", resnum: "52A", wt: "G", mut: "A" });
    expect(parseMutations("A:G52AARG").items[0]).toMatchObject({ resnum: "52A", mut: "R" });
  });

  it("errori con il primo motivo", () => {
    expect(parseMutations("").error).toBe("Indica almeno una mutazione (es. A:K48R).");
    expect(parseMutations("A:K48").error).toMatch(/^Mutazione non valida: «A:K48»/);
    expect(parseMutations("A:K48K").error).toBe("«A:K48K» non cambia il residuo.");
    expect(parseMutations("A:K48B").error).toMatch(/Amminoacido non valido/);
    expect(parseMutations("A:K48R, A:K48A").error).toBe("Il residuo A:48 compare in più mutazioni.");
    expect(parseMutations(Array.from({ length: 11 }, (_, i) => `A:K${i + 1}R`).join(",")).error).toMatch(/Troppe mutazioni/);
  });

  it("aggiunta di una variante: niente doppioni, un solo cambio per residuo", () => {
    expect(addNotation("", "A:K48R")).toBe("A:K48R");
    expect(addNotation("A:K48R", "A:K48R")).toBe("A:K48R");
    expect(addNotation("A:K48R, A:I30P", "A:K48A")).toBe("A:I30P, A:K48A");
    expect(addNotation("A:K48R", "A:K63R")).toBe("A:K48R, A:K63R");
  });
});

describe("richieste e controlli preventivi", () => {
  it("mutagenesi: notazione canonica nel corpo, errore senza struttura o con la notazione sbagliata", () => {
    const req = mutateRequest({ source: UBQ, text: "A:lys48arg", energy: true, projectId: "p1" });
    expect(req).toEqual({ body: { project_id: "p1", source: UBQ, mutations: "A:K48R", energy: true }, error: null });
    expect(mutateRequest({ source: null, text: "A:K48R", energy: false }).error).toBe("Scegli la struttura.");
    expect(mutateRequest({ source: UBQ, text: "K48", energy: false }).body).toBeNull();
    expect(mutationPreflight("mutate", req.body)).toEqual({ kind: "mutate", params: req.body });
    expect(mutationPreflight("mutate", null)).toBeNull();
  });

  it("scansione: residui, interfaccia (catena facoltativa), tasca obbligatoria", () => {
    const base = { source: UBQ, residues: "", chain: "", pocketJob: null, pocket: null, energy: false };
    expect(scanRequest({ ...base, mode: "residues" }).error).toBe("Indica i residui (es. A:30, A:43, A:45-48).");
    expect(scanRequest({ ...base, mode: "residues", residues: " A:30, A:43 " }).body?.selection).toEqual({ mode: "residues", residues: "A:30, A:43" });
    expect(scanRequest({ ...base, mode: "interface" }).body?.selection).toEqual({ mode: "interface", chain: null });
    expect(scanRequest({ ...base, mode: "interface", chain: " B " }).body?.selection).toEqual({ mode: "interface", chain: "B" });
    expect(scanRequest({ ...base, mode: "pocket" }).error).toMatch(/Scegli una tasca/);
    expect(scanRequest({ ...base, mode: "pocket", pocketJob: "pj", pocket: 2 }).body?.selection).toEqual({ mode: "pocket", pocket_job: "pj", pocket: 2 });
  });
});

describe("risultati", () => {
  it("testi di DOPE, energia e sito", () => {
    expect(dopeText({ delta: 348.6, delta_eq: 3.35 })).toBe("+349 (+3,35 residui equivalenti)");
    expect(dopeText({ delta: -39, delta_eq: -0.37 })).toBe("−39 (−0,37 residui equivalenti)");
    expect(dopeText(null)).toBe("—");
    expect(energyText({ available: true, ddE: 72.6, ddE_kcal: 17.4 })).toBe("+73 kJ/mol (+17,4 kcal/mol)");
    expect(energyText({ available: false, message: "x" })).toBe("OpenMM non disponibile");
    expect(energyText({ available: true, error: "boom" })).toBe("non riuscita (boom)");
    expect(energyText(null)).toBe("non calcolata");
    const site = { ss: "H", ss_position: 7, ss_length: 12, rsa_wt: 0.042, core: true, blosum62: -3 } as MutationSite;
    expect(siteSummary(site)).toBe("α-elica (8 di 12) · sepolto (RSA 4%) · BLOSUM62 −3");
    expect(siteSummary({ ...site, ss: "-", core: false, rsa_wt: 0.47, blosum62: 2 })).toBe("coil · esposto (RSA 47%) · BLOSUM62 +2");
  });

  it("link: Qualità del modello, Confronto con il selvatico riottimizzato e con l'originale", () => {
    const result = { source: UBQ, files: { wild_type: "1UBQ_wt.pdb", mutant: "1UBQ_K48R.pdb" } };
    expect(modelQualityLink("j1", result.files.mutant)).toBe("/quality?job=j1&file=1UBQ_K48R.pdb");
    const links = mutationCompareLinks("j1", result);
    const wt = linkedSources(new URLSearchParams(links.wildType.split("?")[1]));
    expect(wt.a).toEqual(decodeSource("job:j1:1UBQ_wt.pdb"));
    expect(wt.b).toEqual(decodeSource("job:j1:1UBQ_K48R.pdb"));
    expect(linkedSources(new URLSearchParams(links.original.split("?")[1])).a).toEqual(UBQ);
    expect(sitesFocus([{ chain: "A", resnum: "52A" }])).toEqual([{ chain: "A", resi: 52 }]);
    // catena «AA» (mmCIF): nei modelli costruiti resta la lettera di MODELLER
    expect(sitesFocus([{ chain: "AA", resnum: "48", model_chain: "A" }])).toEqual([{ chain: "A", resi: 48 }]);
  });

  const scanRow = (label: string, chain: string, resnum: string, code: MutationClass, hotspot: "probabile" | null = null): AlaScanRow => ({
    label, files: { wild_type: "w.pdb", mutant: "m.pdb" },
    mutation: { chain, resnum, wt: "K", mut: "A", label, wt_name: "LYS", mut_name: "ALA" },
    dope: { delta: 10, delta_eq: 0.1 } as AlaScanRow["dope"], energy: null, site: {} as MutationSite,
    assessment: { code, label: code, summary: "", reasons: [], signals: [], score: 0, binding: hotspot ? { interface: true, hotspot, text: "", interchain_delta_eq: 0.8, lost_inter_chain: 1 } : null },
  });

  it("filtri, colorazione per classe ed esclusi", () => {
    const result = {
      rows: [scanRow("A:L43A", "A", "43", "destabilizing", "probabile"), scanRow("A:S20A", "A", "20", "uncertain"), scanRow("A:K48A", "A", "48", "neutral")],
      skipped: [{ chain: "A", resnum: "10", resname: "GLY", reason: "glicina: …" }],
    } as unknown as AlaScanResult;
    expect(scanRows(result).length).toBe(3);
    expect(scanRows(result, "neutral").map((r) => r.label)).toEqual(["A:K48A"]);
    expect(scanRows(result, "hotspot").map((r) => r.label)).toEqual(["A:L43A"]);
    const coloring = scanColoring(result, ["A", "B"])!;
    const colors = coloringMap(coloring);
    expect(colors.get("A:43")).toBe(CLASS_COLOR.destabilizing);
    expect(colors.get("A:48")).toBe(CLASS_COLOR.neutral);
    expect(colors.get("A:10")).toBe(CLASS_COLOR.excluded);
    expect(coloring.chainColors).toEqual({ A: OUTSIDE_COLOR, B: OUTSIDE_COLOR });
    expect(coloring.describe("uncertain")).toBe("incerta");
    expect(scanColoring({ rows: [], skipped: [] } as unknown as AlaScanResult)).toBeNull();
    expect(scanRowOf(result, "A", "20")?.label).toBe("A:S20A");
    expect(scanRowOf(result, "B", "20")).toBeNull();
  });
});

describe("varianti di UniProt", () => {
  const v = (position: number, variation: string, applicable: boolean, category: MappedVariant["category"], notation: string | null = null): MappedVariant => ({
    type: category === "mutagenesi" ? "mutagenesi" : "naturale", category, position, original: "K", variation, description: category === "malattia" ? "in TESTD" : "",
    diseases: category === "malattia" ? ["Malattia di prova"] : [], ids: [], evidences: 0, feature_id: "", chain: "A", resnum: applicable ? String(position - 5) : null,
    structure_aa: applicable ? "K" : null, applicable, notation, reason: applicable ? null : "posizione non presente nella struttura",
    segment: null,
  });
  const mapping = { variants: [v(2, "V", false, "naturale"), v(68, "A", true, "mutagenesi", "A:K63A"), v(53, "R", true, "malattia", "A:K48R")] } as VariantMapping;

  it("applicabili prima, poi per posizione; filtri e ricerca", () => {
    expect(variantRows(mapping).map((x) => x.position)).toEqual([53, 68, 2]);
    expect(variantRows(mapping, "applicable").map((x) => x.notation)).toEqual(["A:K48R", "A:K63A"]);
    expect(variantRows(mapping, "malattia").map((x) => x.position)).toEqual([53]);
    expect(variantRows(mapping, "all", "k63").map((x) => x.position)).toEqual([68]);
    expect(variantRows(mapping, "all", "prova").map((x) => x.position)).toEqual([53]);
    expect(variantRows(null)).toEqual([]);
    expect(variantText(mapping.variants[2])).toBe("K53R → A:K48R");
    expect(variantText(mapping.variants[0])).toBe("K2V: posizione non presente nella struttura");
  });

  it("cronologia", () => {
    const item = { id: "j", kind: "mutate", title: "Mutagenesi · 1UBQ A:K48R", status: "completed", created_at: "", finished_at: "", mutations: "A:K48R", energy: true } as MutationHistoryItem;
    expect(historyText(item)).toBe("Mutagenesi A:K48R (con energia)");
    expect(historyText({ ...item, kind: "alascan", title: "Scansione di alanina · 1UBQ (3 residui)", mutations: null, energy: false })).toBe("Scansione di alanina (3 residui)");
    // stato in italiano, come StatusBadge; completato: quando
    expect(historyOption({ ...item, status: "queued" }, () => "ieri")).toBe("Mutagenesi A:K48R (con energia) · In coda");
    expect(historyOption({ ...item, status: "failed" }, () => "ieri")).toBe("Mutagenesi A:K48R (con energia) · Fallito");
    expect(historyOption({ ...item, status: "cancelled" }, () => "ieri")).toContain("· Annullato");
    expect(historyOption(item, () => "2 min fa")).toBe("Mutagenesi A:K48R (con energia) · 2 min fa");
  });

  it("tasche per la scansione: calcolo in corso seguito, errore, nessun calcolo", () => {
    const base = { loading: false, error: null, jobId: null, status: null, ready: false };
    expect(pocketsView({ ...base, loading: true })).toBe("loading");
    expect(pocketsView(base)).toBe("none");
    expect(pocketsView({ ...base, error: "rete" })).toBe("error"); // non «nessun calcolo»
    expect(pocketsView({ ...base, jobId: "p1", status: "running" })).toBe("running");
    expect(pocketsView({ ...base, jobId: "p1", status: "queued" })).toBe("running");
    expect(pocketsView({ ...base, jobId: "p1", status: "completed" })).toBe("loading"); // risultato in lettura
    expect(pocketsView({ ...base, jobId: "p1", status: "completed", ready: true })).toBe("ready");
    expect(pocketsView({ ...base, jobId: "p1", status: "failed" })).toBe("failed");
  });
});
