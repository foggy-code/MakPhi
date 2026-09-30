import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CoverageCheck } from "../api/types";
import { featureTracks } from "../lib/disorder";
import { FeatureMap } from "./charts";
import { CoverageAlerts } from "./CoverageAlerts";

// Markup statico (react-dom/server): avvisi prima di modellare e tracce della mappa, senza browser.
const check: CoverageCheck = {
  length: 121, note: null, domains: [], combined: null,
  domains_info: { source: null, accession: null, available: false, note: "Nessuna accessione UniProt: i domini non sono noti." },
  disorder: {
    method: "FoldIndex (Prilusky et al. 2005)", length: 121, window: 51,
    scale: { name: "FoldIndex", threshold: 0, disordered: "below", formula: "", min: -0.5, max: 0.3 }, scores: [],
    regions: [{ start: 80, end: 121, length: 42, disordered: 42, mean_score: -0.4 }], fraction: 0.35, below_fraction: 0.4,
    params: { window: 51, threshold: 0, min_region: 30, max_gap: 3 },
    low_complexity: { method: "SEG", params: { window: 12, k1: 2.2, k2: 2.5 }, regions: [], fraction: 0 }, limits: [],
  },
  templates: [{ label: "9ZZZ", error: "Template 9ZZZ non presente nella libreria." }],
};

describe("avvisi di disordine e domini", () => {
  it("avvisi, suggerimento, template non valutati e sintesi con il perché dei domini mancanti", () => {
    const html = renderToStaticMarkup(<CoverageAlerts check={check} assessment={{
      warnings: [{ kind: "disorder_uncovered", tone: "warn", start: 80, end: 121, text: "La regione 80–121 è predetta disordinata" }],
      suggestion: "Valuta di escludere 80–121",
    }} />);
    expect(html).toContain('class="alert alert-warn"');
    expect(html).toContain("La regione 80–121 è predetta disordinata");
    expect(html).toContain("<b>Suggerimento.</b> Valuta di escludere 80–121");
    expect(html).toContain("9ZZZ: non valutato (Template 9ZZZ non presente nella libreria.)");
    expect(html).toContain("Regione disordinata predetta: 80–121 (35% dei residui).");
    expect(html).toContain("Nessuna accessione UniProt");
  });
  it("in attesa, errore e nota del server al posto degli avvisi", () => {
    expect(renderToStaticMarkup(<CoverageAlerts check={null} assessment={null} loading />)).toContain("Verifica di disordine e domini del bersaglio…");
    expect(renderToStaticMarkup(<CoverageAlerts check={null} assessment={null} />)).toBe("");
    expect(renderToStaticMarkup(<CoverageAlerts check={null} assessment={null} error="Errore 500" />)).toContain("non riuscita: Errore 500");
    expect(renderToStaticMarkup(<CoverageAlerts check={{ ...check, note: "Bersaglio con più catene" }} assessment={null} />)).toContain("Bersaglio con più catene");
  });
});

describe("mappa delle caratteristiche", () => {
  it("un rettangolo per tratto con il colore del tratto, testo sulle tracce vuote", () => {
    const tracks = featureTracks({ disorder: check.disorder, gor: [{ type: "helix", start: 2, end: 20 }, { type: "strand", start: 30, end: 35 }] });
    const html = renderToStaticMarkup(<FeatureMap length={121} tracks={tracks} />);
    expect(html).toContain(">Disordine (FoldIndex)<");
    expect(html).toContain(">nessuna<"); // bassa complessità vuota
    expect((html.match(/<rect /g) ?? []).length).toBe(3); // 1 regione disordinata + 2 elementi GOR
    expect(html).toContain('fill="var(--helix)"');
    expect(html).toContain('fill="var(--strand)"');
    expect(html).toContain('fill="#f87171"');
  });
});
