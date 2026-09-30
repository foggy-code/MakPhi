import { describe, expect, it } from "vitest";
import type { PipelineItem, RankReason, SearchHit, SearchResult, SuggestedTemplate } from "../api/types";
import {
  clampMaxTemplates,
  itemTemplates,
  itemTemplatesTooltip,
  MAX_TEMPLATES_DEFAULT,
  modelingLink,
  rankBand,
  rankTone,
  rankTooltip,
  reasonLine,
  sortedReasons,
  suggestedRoles,
  suggestionTemplates,
} from "./templateRank";

const reason = (key: RankReason["key"], score: number | null, weight: number, text = "motivo"): RankReason => ({
  key, label: key === "identity" ? "Identità" : key === "ligands" ? "Ligandi e cofattori" : key, score, weight, text,
});

const hit = (template_id: string, extra: Partial<SearchHit> = {}): SearchHit => ({
  pdb_id: template_id.split("_")[0], chain: template_id.split("_")[1] ?? "A", template_id, title: "", description: "",
  organism: "", method: "X-RAY DIFFRACTION", resolution: 1.8, identity: 96.1, positives: null, coverage: 100,
  evalue: 1e-30, bitscore: 150, align_length: 76, query_start: 1, query_end: 76, subject_start: 1, subject_end: 76,
  query_aligned: "", subject_aligned: "", subject_length: 76, source: "rcsb", ...extra,
});

const suggested = (template_id: string, role: SuggestedTemplate["role"], extra: Partial<SuggestedTemplate> = {}): SuggestedTemplate => ({
  template_id, pdb_id: template_id.split("_")[0], chain: template_id.split("_")[1], rank_score: 80, identity: 90,
  coverage: 50, predicted: false, role, reason: `${role} perché sì`, new_residues: 38, ranges: "1–38", ...extra,
});

const result = (extra: Partial<SearchResult> = {}): SearchResult => ({
  engine: "rcsb", engine_label: "RCSB", query_length: 76, hits: [hit("1UBQ_A"), hit("2XYZ_B")],
  query: { name: "lievito", sequence: "MQIFVKT", length: 7 }, best: "1UBQ_A", ...extra,
});

describe("fasce del punteggio", () => {
  it("confini come nel backend: buona ≥ 70, intermedia 40–70, scarsa < 40", () => {
    expect(rankBand(70)).toBe("buona");
    expect(rankBand(69.9)).toBe("intermedia");
    expect(rankBand(40)).toBe("intermedia");
    expect(rankBand(39.9)).toBe("scarsa");
    expect(rankTone(95.3)).toBe("ok");
    expect(rankTone(55)).toBe("warn");
    expect(rankTone(10)).toBe("err");
    expect(rankTone(undefined)).toBeUndefined();
    expect(rankTone(null)).toBeUndefined();
  });
});

describe("motivazioni", () => {
  it("riga per componente: conteggiata, informativa o non disponibile", () => {
    expect(reasonLine(reason("identity", 97.6, 30, "96,1% su 76 residui"))).toBe("Identità · 97,6/100 (peso 30): 96,1% su 76 residui");
    expect(reasonLine(reason("ligands", null, 0, "apo"))).toBe("Ligandi e cofattori · informativa: apo");
    expect(reasonLine(reason("rfree", null, 5, "non depositato"))).toBe("rfree · non conteggiata: non depositato");
  });
  it("prima le componenti conteggiate, per peso", () => {
    const list = [reason("ligands", null, 0), reason("gaps", 100, 7), reason("identity", 97.6, 30), reason("rfree", null, 5)];
    expect(sortedReasons(list).map((r) => r.key)).toEqual(["identity", "gaps", "rfree", "ligands"]);
    expect(sortedReasons(undefined)).toEqual([]);
  });
  it("tooltip con punteggio, fascia, zona crepuscolare e una riga per componente", () => {
    const text = rankTooltip({ rank_score: 43.6, rank_twilight: true, rank_reasons: [reason("identity", 34.5, 30, "sotto la curva")] });
    expect(text.split("\n")).toEqual([
      "Punteggio 43,6/100, fascia intermedia · zona crepuscolare (sotto la curva di Rost)",
      "• Identità · 34,5/100 (peso 30): sotto la curva",
    ]);
    expect(rankTooltip({})).toMatch(/versione precedente/);
  });
});

describe("template consigliati", () => {
  it("ruoli dalla selezione del backend, oppure il solo best per i risultati precedenti", () => {
    const sel = { templates: [suggested("1UBQ_A", "principale"), suggested("2XYZ_B", "complementare")], excluded: [], not_examined: 0, coverage: 100, covered_residues: 76, query_length: 76, max_templates: 3, min_new_residues: 10, version: 1 };
    const roles = suggestedRoles(result({ suggested: sel }));
    expect([...roles.keys()]).toEqual(["1UBQ_A", "2XYZ_B"]);
    expect(roles.get("2XYZ_B")?.role).toBe("complementare");
    const old = suggestedRoles(result());
    expect([...old.entries()]).toEqual([["1UBQ_A", null]]);
    expect(suggestedRoles(result({ best: null })).size).toBe(0);
  });
  it("template per la Modellazione e collegamento con un t=ID:CATENA per template", () => {
    const sel = { templates: [suggested("1UBQ_A", "principale"), suggested("AF-P0CG48-F1_A", "complementare", { pdb_id: "AF-P0CG48-F1", chain: "A" })], excluded: [], not_examined: 0, coverage: 100, covered_residues: 76, query_length: 76, max_templates: 3, min_new_residues: 10, version: 1 };
    const refs = suggestionTemplates(result({ suggested: sel }));
    expect(refs).toEqual([{ id: "1UBQ", chains: ["A"] }, { id: "AF-P0CG48-F1", chains: ["A"] }]);
    const link = modelingLink(result(), refs);
    const params = new URLSearchParams(link.split("?")[1]);
    expect(link.startsWith("/modeling?")).toBe(true);
    expect(params.get("name")).toBe("lievito");
    expect(params.get("seqtext")).toBe("MQIFVKT");
    expect(params.getAll("t")).toEqual(["1UBQ:A", "AF-P0CG48-F1:A"]);
    // risultato senza selezione: il best; nessun template: nessun collegamento
    expect(suggestionTemplates(result())).toEqual([{ id: "1UBQ", chains: ["A"] }]);
    expect(modelingLink(result(), [])).toBe("");
    expect(new URLSearchParams(modelingLink(result(), [{ id: "1ABC", chains: [] }]).split("?")[1]).getAll("t")).toEqual(["1ABC"]);
  });
});

describe("pipeline in modalità multi", () => {
  it("numero di template entro 2–5", () => {
    expect(clampMaxTemplates(1)).toBe(2);
    expect(clampMaxTemplates(9)).toBe(5);
    expect(clampMaxTemplates(3.6)).toBe(4);
    expect(clampMaxTemplates(Number.NaN)).toBe(MAX_TEMPLATES_DEFAULT);
  });
  it("template di un elemento: scelti con ruolo dalle metriche, altrimenti dalla colonna template", () => {
    const multi = {
      template: "1UBQ_A,2XYZ_B",
      metrics: {
        templates: [
          { template_id: "1UBQ_A", role: "principale" as const, reason: "miglior punteggio", identity: 96, coverage: 50, rank_score: 80, predicted: false },
          { template_id: "2XYZ_B", role: "complementare" as const, reason: "copre 38 residui", identity: 90, coverage: 50, rank_score: 70, predicted: false },
        ],
        combined_coverage: 100, excluded_templates: 2,
      },
    } satisfies Pick<PipelineItem, "template" | "metrics">;
    expect(itemTemplates(multi).map((t) => [t.id, t.role])).toEqual([["1UBQ_A", "principale"], ["2XYZ_B", "complementare"]]);
    expect(itemTemplatesTooltip(multi)?.split("\n")).toEqual([
      "1UBQ_A (principale): miglior punteggio",
      "2XYZ_B (complementare): copre 38 residui",
      "Copertura complessiva 100,0%",
      "2 candidati esclusi (motivi nel log)",
    ]);
    const fixed = { template: "1UBQ, 2XYZ", metrics: {} };
    expect(itemTemplates(fixed)).toEqual([{ id: "1UBQ" }, { id: "2XYZ" }]);
    expect(itemTemplatesTooltip(fixed)).toBeUndefined();
    expect(itemTemplates({ template: null, metrics: {} })).toEqual([]);
  });
});
