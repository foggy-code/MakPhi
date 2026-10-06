import { describe, expect, it } from "vitest";
import type { PreflightCheck, PreflightResult } from "../api/types";
import { confirmTitle, countsText, estimateText, fmtSeconds, launchDecision, modelingPreflight, pipelinePreflight, requestKey, splitChecks } from "./preflight";

// Esiti come li produce il backend (domain/preflight.py, tests/test_preflight.py)
const check = (level: PreflightCheck["level"], code: string, extra: Partial<PreflightCheck> = {}): PreflightCheck => ({ level, code, message: `messaggio ${code}`, suggestion: null, ...extra });

function result(checks: PreflightCheck[], extra: Partial<PreflightResult> = {}): PreflightResult {
  const counts = { error: 0, warning: 0, info: 0 };
  for (const c of checks) counts[c.level] += 1;
  return { kind: "model", ok: counts.error === 0, counts, checks, estimate: null, coverage: null, ...extra };
}

describe("esiti dei controlli", () => {
  it("divide per livello e tiene a parte gli avvisi di copertura", () => {
    const r = result([
      check("error", "modeller_license_missing"),
      check("warning", "twilight_zone"),
      check("warning", "disorder_uncovered", { group: "coverage" }),
      check("info", "disorder_covered", { group: "coverage" }),
      check("info", "estimated_time"),
    ]);
    const parts = splitChecks(r);
    expect(parts.errors.map((c) => c.code)).toEqual(["modeller_license_missing"]);
    expect(parts.warnings.map((c) => c.code)).toEqual(["twilight_zone"]);
    expect(parts.coverage.map((c) => c.code)).toEqual(["disorder_uncovered", "disorder_covered"]);
    expect(parts.infos.map((c) => c.code)).toEqual(["estimated_time"]);
    expect(splitChecks(null)).toEqual({ errors: [], warnings: [], infos: [], coverage: [] });
  });

  it("riga di sintesi", () => {
    expect(countsText(result([check("error", "a"), check("warning", "b"), check("warning", "c"), check("info", "d")]))).toBe("1 errore · 2 avvisi · 1 informazione");
    expect(countsText(result([check("info", "d"), check("info", "e")]))).toBe("nessun problema · 2 informazioni");
    expect(countsText(result([]))).toBe("nessun problema");
    expect(countsText(null)).toBe("");
  });
});

describe("pulsante di avvio", () => {
  it("un errore blocca con il primo come motivo; gli avvisi (copertura compresa) chiedono conferma; le informazioni no", () => {
    const blocked = launchDecision(result([check("error", "disk_full_data", { message: "Spazio esaurito" }), check("error", "x"), check("warning", "w")]));
    expect(blocked).toEqual({ state: "blocked", reason: "Spazio esaurito", warnings: [] });
    const confirm = launchDecision(result([check("warning", "twilight_zone"), check("warning", "disorder_uncovered", { group: "coverage" }), check("info", "estimated_time")]));
    expect(confirm.state).toBe("confirm");
    expect(confirm.warnings.map((c) => c.code)).toEqual(["twilight_zone", "disorder_uncovered"]);
    expect(launchDecision(result([check("info", "estimated_time")]))).toEqual({ state: "ready", reason: null, warnings: [] });
  });

  it("controlli in corso, superati o non riusciti", () => {
    const ok = result([]);
    expect(launchDecision(ok, { pending: true }).state).toBe("checking");
    expect(launchDecision(null).state).toBe("checking");
    // server non raggiungibile per i controlli: l'avvio resta possibile (la rotta di avvio valida comunque)
    expect(launchDecision(null, { failed: true }).state).toBe("unchecked");
  });

  it("titolo della conferma", () => {
    expect(confirmTitle([check("warning", "a")])).toBe("C'è un avviso: vuoi avviare comunque?");
    expect(confirmTitle([check("warning", "a"), check("warning", "b")])).toBe("Ci sono 2 avvisi: vuoi avviare comunque?");
  });
});

describe("stime dei tempi", () => {
  it("come fmt_seconds del backend", () => {
    expect(fmtSeconds(0.2)).toBe("1 s");
    expect(fmtSeconds(45)).toBe("45 s");
    expect(fmtSeconds(180)).toBe("3 min");
    expect(fmtSeconds(80 * 60)).toBe("1 h 20 min");
    expect(fmtSeconds(2 * 3600)).toBe("2 h");
    expect(fmtSeconds(5 * 86400)).toBe("5 giorni");
    expect(estimateText({ seconds: 13.9, low: 6.9, high: 27.8, detail: "", basis: "" })).toBe("circa 14 s (da 7 s a 28 s)");
    expect(estimateText(null)).toBeNull();
  });
});

describe("richieste", () => {
  const body = { target: { name: "y", sequence: "MQIFVK" }, templates: [{ id: "1UBQ", chains: ["A"] }], options: { models: 5 }, quality: "best" };

  it("Modellazione: null finché bersaglio e template non sono pronti, come la verifica di copertura", () => {
    expect(modelingPreflight({ mode: "auto", target: { name: "", sequence: "" }, templates: [{ id: "1UBQ", chains: ["A"] }], pirText: null, accession: "", body })).toBeNull();
    expect(modelingPreflight({ mode: "auto", target: { name: "y", sequence: "MQIFVK" }, templates: [{ id: "1UBQ", chains: [] }], pirText: null, accession: "", body })).toBeNull();
    const r = modelingPreflight({ mode: "auto", target: { name: "y", sequence: "MQIFVK" }, templates: [{ id: "1UBQ", chains: ["A"] }], pirText: null, accession: "P0CG48", body, searchJob: "s1" });
    expect(r).toEqual({ kind: "model", params: { ...body, alignment: null }, context: { accession: "P0CG48", search_job: "s1" } });
  });

  it("Modellazione da PIR: l'allineamento del documento nei parametri", () => {
    const pir = ">P1;1UBQ_A\nstructureX:1UBQ_A:1:A:76:A::::\nMQIFVK*\n\n>P1;t\nsequence:t:::::::0.00: 0.00\nMQIFVK*\n";
    expect(modelingPreflight({ mode: "pir", target: { name: "", sequence: "" }, templates: [], pirText: null, accession: "", body })).toBeNull();
    const r = modelingPreflight({ mode: "pir", target: { name: "", sequence: "" }, templates: [], pirText: pir, accession: "", body });
    expect(r?.params.alignment).toBe(pir);
    expect(r?.context).toEqual({ accession: null, search_job: null });
  });

  it("Pipeline e chiave della richiesta", () => {
    expect(pipelinePreflight({ fasta: "" }, 0)).toBeNull();
    const p = pipelinePreflight({ fasta: ">a\nMK\n", engine: "ncbi" }, 1);
    expect(p).toEqual({ kind: "pipeline", params: { fasta: ">a\nMK\n", engine: "ncbi" } });
    expect(requestKey(p)).toBe(JSON.stringify(p));
    expect(requestKey(null)).toBe("");
  });
});
