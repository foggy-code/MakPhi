import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CoverageCheck, PreflightResult } from "../api/types";
import { PreflightPanel, type PreflightState } from "./Preflight";

// Markup statico (react-dom/server) del riquadro dei controlli: errori e avvisi in evidenza, avvisi di copertura con
// CoverageAlerts (una volta sola), informazioni discrete con la base della stima.
const coverage: CoverageCheck = {
  length: 211, note: null, domains: [], templates: [],
  domains_info: { source: null, accession: null, available: false, note: "Nessuna accessione UniProt: i domini non sono noti." },
  disorder: {
    method: "FoldIndex (Prilusky et al. 2005)", length: 211, window: 51,
    scale: { name: "FoldIndex", threshold: 0, disordered: "below", formula: "", min: -0.5, max: 0.3 }, scores: [],
    regions: [{ start: 77, end: 211, length: 135, disordered: 130, mean_score: -0.4 }], fraction: 0.64, below_fraction: 0.64,
    params: { window: 51, threshold: 0, min_region: 30, max_gap: 3 },
    low_complexity: { method: "SEG", params: { window: 12, k1: 2.2, k2: 2.5 }, regions: [], fraction: 0 }, limits: [],
  },
  combined: {
    label: "1UBQ:A", coverage: 36, covered: [[1, 76]], domains: [], suggestion: "Valuta di escludere 77–211",
    warnings: [{ kind: "disorder_uncovered", tone: "warn", start: 77, end: 211, text: "La regione 77–211 è predetta disordinata" }],
  },
};

const result: PreflightResult = {
  kind: "model", ok: false, counts: { error: 1, warning: 2, info: 1 },
  checks: [
    { level: "error", code: "modeller_license_missing", message: "Manca la chiave di licenza di MODELLER.", suggestion: "Impostazioni → Licenze" },
    { level: "warning", code: "low_coverage", message: "I template coprono il 36% del bersaglio", suggestion: null },
    { level: "warning", code: "disorder_uncovered", message: "La regione 77–211 è predetta disordinata", suggestion: "Valuta di escludere 77–211", group: "coverage" },
    { level: "info", code: "estimated_time", message: "La modellazione richiede circa 30 s (da 15 s a 1 min).", suggestion: null },
  ],
  estimate: { seconds: 30, low: 15, high: 60, detail: "", basis: "Stima dai tempi misurati su un portatile a 4 CPU logiche" },
  coverage,
};

const state = (extra: Partial<PreflightState> = {}): PreflightState => ({
  request: { kind: "model", params: {} }, result, loading: false, error: null, check: async () => result, ...extra,
});

describe("riquadro dei controlli preventivi", () => {
  it("errori e avvisi come allarmi, copertura una volta sola, informazioni con la base della stima", () => {
    const html = renderToStaticMarkup(<PreflightPanel state={state()} />);
    expect(html).toContain("1 errore · 2 avvisi · 1 informazione");
    expect(html).toContain('class="alert alert-err"');
    expect(html).toContain("Manca la chiave di licenza di MODELLER.");
    expect(html).toContain("<b>Suggerimento.</b> Impostazioni → Licenze");
    expect(html).toContain('class="alert alert-warn"');
    expect(html.split("La regione 77–211 è predetta disordinata").length - 1).toBe(1); // CoverageAlerts, non ripetuto
    expect(html).toContain("Disordine e domini del bersaglio");
    expect(html).toContain('title="Stima dai tempi misurati su un portatile a 4 CPU logiche"');
    expect(html).toContain("circa 30 s");
  });

  it("in corso, non riuscito, senza richiesta", () => {
    expect(renderToStaticMarkup(<PreflightPanel state={state({ result: null, loading: true })} />)).toContain("verifica in corso");
    expect(renderToStaticMarkup(<PreflightPanel state={state({ result: null, error: "Errore 500" })} />)).toContain("l&#x27;avvio resta possibile");
    expect(renderToStaticMarkup(<PreflightPanel state={state({ request: null })} />)).toBe("");
  });

  it("stima della ricerca (nessun esito estimated_time) su una riga a parte", () => {
    const search: PreflightResult = { kind: "search", ok: true, counts: { error: 0, warning: 0, info: 0 }, checks: [], coverage: null,
      estimate: { seconds: 8.5, low: 2, high: 15, detail: "RCSB", basis: "Tempi tipici del motore" } };
    const html = renderToStaticMarkup(<PreflightPanel state={state({ request: { kind: "search", params: {} }, result: search })} />);
    expect(html).toContain("Tempo stimato: circa 9 s (da 2 s a 15 s).");
    expect(html).toContain("nessun problema");
  });
});
