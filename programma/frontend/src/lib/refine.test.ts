import { describe, expect, it } from "vitest";
import type { RefineComparisonRow, SystemInfo } from "../api/types";
import { decodeSource, linkedSources } from "./compare";
import {
  comparisonSummary, energyText, fmtDelta, fmtRefineValue, MD_MAX_PS, refineCompareLink, refinedQualityLink, refinedSource, refineRequest, verdictTone,
} from "./refine";
import { missingTools, missingToolsText, rememberedJob } from "./toolJobs";

const row = (key: RefineComparisonRow["key"], before: number | null, after: number | null, delta: number | null, verdict: RefineComparisonRow["verdict"]): RefineComparisonRow => ({
  key, label: key, before, after, delta, verdict, higher_is_better: key === "grade_score" || key === "rama_favored",
});

describe("rifinitura energetica", () => {
  it("richiesta: dinamica facoltativa fino a 50 ps", () => {
    expect(refineRequest(false, 10, true)).toEqual({ body: { md_ps: 0, hydrogens: true }, error: null });
    expect(refineRequest(true, 10, false)).toEqual({ body: { md_ps: 10, hydrogens: false }, error: null });
    expect(refineRequest(true, 0, false).error).toBe("Indica la durata della dinamica (ps).");
    expect(refineRequest(true, Number.NaN, false).body).toBeNull();
    expect(refineRequest(true, MD_MAX_PS + 1, false).error).toBe("La dinamica dura al massimo 50 ps.");
  });

  it("link a Qualità e Confronto: originale come riferimento (A), rifinito come B", () => {
    const result = { refined_file: "UBQ_refined.pdb", source: { type: "template" as const, id: "1UBQ" } };
    expect(refinedSource("j1", result)).toEqual({ type: "job", job_id: "j1", file: "UBQ_refined.pdb" });
    expect(refinedQualityLink("j1", result)).toBe("/quality?job=j1&file=UBQ_refined.pdb");
    const link = refineCompareLink("j1", result);
    const params = new URLSearchParams(link.split("?")[1]);
    const { a, b } = linkedSources(params);
    expect(a).toEqual({ type: "template", id: "1UBQ" });
    expect(b).toEqual(decodeSource("job:j1:UBQ_refined.pdb"));
  });

  it("valori, differenze ed esiti del confronto", () => {
    const rows = [row("clashscore", 3.32, 0, -3.32, "meglio"), row("rama_outliers", 0, 1.3, 1.3, "peggio"), row("rama_favored", 95.9, 95.9, 0, "invariato"),
      row("zdope", null, -1.2, null, null), row("dope", -7878.3, -7957.3, -79.0, "meglio")];
    expect(fmtRefineValue(rows[0], 3.32)).toBe("3,32");
    expect(fmtRefineValue(rows[4], -7957.3)).toBe("-7957"); // it-IT: separatore delle migliaia dalle 5 cifre
    expect(fmtDelta(rows[0])).toBe("−3,32");
    expect(fmtDelta(rows[1])).toBe("+1,3");
    expect(fmtDelta(rows[3])).toBe("—");
    expect(verdictTone("meglio")).toBe("ok");
    expect(verdictTone("peggio")).toBe("err");
    expect(verdictTone(null)).toBeUndefined();
    expect(comparisonSummary(rows)).toEqual({ better: 2, worse: 1, same: 1 });
  });

  it("energia prima e dopo", () => {
    expect(energyText({ before: 710.04, after: -12447.54, delta: -13157.6, unit: "kJ/mol" })).toBe("710 → −12.448 kJ/mol (−13.158)");
    expect(energyText({ before: null, after: 1, delta: null, unit: "kJ/mol" })).toBe("—");
  });
});

describe("job degli strumenti nella pagina Qualità", () => {
  it("job del link, poi quello conservato per la stessa struttura", () => {
    expect(rememberedJob("template:1UBQ", { sourceKey: "template:1UBQ", jobId: "a" })).toBe("a");
    expect(rememberedJob("template:2ABC", { sourceKey: "template:1UBQ", jobId: "a" })).toBeNull();
    expect(rememberedJob("template:1UBQ", { sourceKey: "template:1UBQ", jobId: "a" }, "link")).toBe("link");
    expect(rememberedJob("template:1UBQ", null)).toBeNull();
  });

  it("strumenti mancanti dalle informazioni di sistema", () => {
    const info = { tools: { fpocket: { label: "fpocket", available: false }, apbs: { label: "APBS", available: true }, pdb2pqr: { label: "PDB2PQR (con PROPKA)", available: false } } } as unknown as SystemInfo;
    expect(missingTools(info, ["fpocket"])).toEqual(["fpocket"]);
    expect(missingTools(info, ["pdb2pqr", "apbs"])).toEqual(["PDB2PQR (con PROPKA)"]);
    expect(missingTools(info, ["openmm"])).toEqual(["openmm"]); // versione del server senza la voce: manca
    expect(missingTools(null, ["fpocket"])).toEqual([]);
    expect(missingToolsText([], "x")).toBeNull();
    expect(missingToolsText(["fpocket"], "Ricerca delle tasche")).toMatch(/^Ricerca delle tasche non disponibile: manca fpocket/);
    expect(missingToolsText(["OpenMM", "PDBFixer"], "Rifinitura")).toContain("mancano OpenMM, PDBFixer");
  });
});
