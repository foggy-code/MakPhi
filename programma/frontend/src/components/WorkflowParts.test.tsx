import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CoverageCheck, PreflightResult, SearchHit } from "../api/types";
import { CoverageAlerts } from "./CoverageAlerts";
import { PreflightPanel } from "./Preflight";
import { HitPanel, InfoChips, RankBreakdown } from "./WorkflowParts";

// Markup statico (react-dom/server) delle parti comuni del flusso di lavoro: dettaglio di un hit, chip dei metadati,
// dettagli a scomparsa della verifica di copertura e dei controlli preventivi.
const hit = (extra: Partial<SearchHit> = {}): SearchHit => ({
  pdb_id: "1UBQ", chain: "A", template_id: "1UBQ_A", title: "UBIQUITIN", description: "", organism: "Homo sapiens",
  method: "X-RAY DIFFRACTION", resolution: 1.8, identity: 96.1, positives: 98.7, coverage: 100, evalue: 1e-40, bitscore: 150.2,
  align_length: 76, query_start: 1, query_end: 76, subject_start: 1, subject_end: 76, query_aligned: "MQIF", subject_aligned: "MQVF",
  subject_length: 76, source: "rcsb", rfree: 0.21, released: "1987-01-02", ligands: [], oligomeric_state: "Monomer",
  rank_score: 75, rank_band: "buona", rank_twilight: false,
  rank_reasons: [
    { key: "identity", label: "Identità", score: 100, weight: 30, text: "quasi identico" },
    { key: "coverage", label: "Copertura del bersaglio", score: 50, weight: 30, text: "metà" },
    { key: "ligands", label: "Ligandi e cofattori", score: null, weight: 0, text: "apo" },
  ],
  ...extra,
});

describe("dettaglio di un hit", () => {
  it("campi, punteggio con le componenti e i punti, allineamento con la legenda", () => {
    const html = renderToStaticMarkup(<HitPanel hit={hit()} queryLength={76} />);
    expect(html).toContain("1UBQ · catena A");
    expect(html).toContain("Copertura della query");
    expect(html).toContain("100,0% · residui 1–76 di 76");
    expect(html).toContain("R-free");
    expect(html).toContain("Monomer");
    expect(html).toContain(">nessuno<"); // ligandi noti: nessuno
    expect(html).toContain('href="https://www.rcsb.org/structure/1UBQ"');
    expect(html).toContain("fascia buona");
    expect(html).toContain(">50,0<"); // punti dell'identità: 100 × 30 / 60
    expect(html).toContain(">25,0<"); // punti della copertura: 50 × 30 / 60
    expect(html).toContain("informativa");
    expect(html).toContain("Media pesata delle componenti conteggiate");
    expect(html).toContain(">         ||·|<"); // riga centrale: identici e sostituzioni
    expect(html).toContain("«|» residui identici");
  });

  it("modello predetto: pLDDT e collegamento ad AlphaFold DB; senza punteggio la spiegazione", () => {
    const html = renderToStaticMarkup(<HitPanel hit={hit({ predicted: true, plddt: 91.2, uniprot_accession: "P0CG48", resolution: null, rank_score: undefined, rank_reasons: undefined })} queryLength={76} />);
    expect(html).toContain("pLDDT medio");
    expect(html).toContain('href="https://alphafold.ebi.ac.uk/entry/P0CG48"');
    expect(html).toContain("Punteggio non disponibile");
    expect(renderToStaticMarkup(<RankBreakdown hit={hit({ rank_twilight: true })} />)).toContain("zona crepuscolare");
  });

  it("chip dei metadati senza le voci vuote", () => {
    const html = renderToStaticMarkup(<InfoChips items={[{ label: "Lunghezza", value: "76 aa" }, { label: "Organismo", value: null }, { label: "Voci", value: "" }]} />);
    expect(html).toContain("Lunghezza <b>76 aa</b>");
    expect(html).not.toContain("Organismo");
    expect(renderToStaticMarkup(<InfoChips items={[{ label: "Voci", value: null }]} />)).toBe("");
  });
});

const coverage: CoverageCheck = {
  length: 211, note: null, templates: [],
  domains: [{ accession: "PF00240", accessions: ["PF00240"], name: "Ubiquitin", type: "domain", start: 1, end: 72 }],
  domains_info: { source: "InterPro", accession: "P0CG48", available: true, note: null },
  disorder: {
    method: "FoldIndex (Prilusky et al. 2005)", length: 211, window: 51,
    scale: { name: "FoldIndex", threshold: 0, disordered: "below", formula: "", min: -0.5, max: 0.3 }, scores: [],
    regions: [{ start: 77, end: 211, length: 135, disordered: 130, mean_score: -0.4 }], fraction: 0.64, below_fraction: 0.64,
    params: { window: 51, threshold: 0, min_region: 30, max_gap: 3 },
    low_complexity: { method: "SEG", params: { window: 12, k1: 2.2, k2: 2.5 }, regions: [], fraction: 0 }, limits: [],
  },
  combined: {
    label: "1UBQ:A", coverage: 36, covered: [[1, 76]], suggestion: null,
    domains: [{ accession: "PF00240", accessions: ["PF00240"], name: "Ubiquitin", type: "domain", start: 1, end: 72, covered_pct: 100, status: "covered" }],
    warnings: [{ kind: "disorder_uncovered", tone: "warn", start: 77, end: 211, text: "La regione 77–211 è predetta disordinata" }],
  },
};

describe("dettagli a scomparsa di copertura e controlli", () => {
  it("copertura: tratti coperti e domini con lo stato nei dettagli", () => {
    const html = renderToStaticMarkup(<CoverageAlerts check={coverage} assessment={coverage.combined} />);
    expect(html).toMatch(/<details[^>]*class="[^"]*\bdisclosure\b[^"]*"/);
    expect(html).toContain("copertura 36,0%");
    expect(html).toContain("Residui del bersaglio con un residuo del template di fronte: 1–76.");
    expect(html).toContain("Ubiquitin");
    expect(html).toContain('class="badge badge-ok">coperto<');
  });

  it("controlli: informazioni in chiaro, copertura aperta se c'è un avviso, esiti e base della stima nei dettagli tecnici", () => {
    const result: PreflightResult = {
      kind: "model", ok: true, counts: { error: 0, warning: 1, info: 1 }, coverage,
      checks: [
        { level: "warning", code: "disorder_uncovered", message: "La regione 77–211 è predetta disordinata", suggestion: null, group: "coverage" },
        { level: "info", code: "template_identity", message: "1UBQ: identità 96%", suggestion: null },
      ],
      estimate: { seconds: 8, low: 4, high: 16, detail: "5 modelli", basis: "Tempi misurati" },
    };
    const html = renderToStaticMarkup(<PreflightPanel state={{ request: { kind: "model", params: {} }, result, loading: false, error: null, check: async () => result }} />);
    expect(html).toContain('class="badge badge-warn">1 avviso · 1 informazione<');
    expect(html).toContain('title="Tempi misurati"');
    expect(html).toContain("Tempo stimato: circa 8 s (da 4 s a 16 s).");
    expect(html).toMatch(/<details[^>]*class="[^"]*\bdisclosure\b[^"]*"[^>]*open=""[^>]*>\s*<summary>(?:(?!<\/summary>).)*Disordine e domini del bersaglio/);
    // le informazioni (dati che lasciano il computer, consigli) restano visibili, prima delle sezioni a scomparsa
    expect(html).toContain("1UBQ: identità 96%");
    expect(html.indexOf("1UBQ: identità 96%")).toBeLessThan(html.indexOf("<details"));
    expect(html).toContain("Dettagli tecnici");
    expect(html).toContain("disorder_uncovered (avviso) · template_identity (informazione)");
    expect(html).toContain("5 modelli");
  });
});

describe("domini non noti e informazioni in vista", () => {
  const noDomains: CoverageCheck = {
    ...coverage, domains: [],
    domains_info: { source: null, accession: null, available: false, note: "La sequenza non coincide con quella di UniProt: i domini non si possono riportare." },
    combined: { ...coverage.combined!, domains: [], warnings: [] },
  };

  it("copertura: il perché dei domini mancanti fuori dai dettagli, una volta sola", () => {
    const html = renderToStaticMarkup(<CoverageAlerts check={noDomains} assessment={noDomains.combined} />);
    const note = "La sequenza non coincide con quella di UniProt";
    expect(html.split(note).length - 1).toBe(1);
    expect(html.indexOf(note)).toBeLessThan(html.indexOf("<details"));
    expect(html).toContain("Domini non verificati.");
    expect(html).toContain("Regione disordinata predetta: 77–211"); // la sintesi resta nei dettagli
  });

  it("controlli: senza avvisi la copertura resta chiusa, ma il riepilogo dice che i domini non sono verificati", () => {
    const result: PreflightResult = {
      kind: "model", ok: true, counts: { error: 0, warning: 0, info: 1 }, coverage: noDomains, estimate: null,
      checks: [{ level: "info", code: "remote_ncbi", message: "La sequenza viene inviata a NCBI BLAST.", suggestion: null }],
    };
    const html = renderToStaticMarkup(<PreflightPanel state={{ request: { kind: "model", params: {} }, result, loading: false, error: null, check: async () => result }} />);
    expect(html).toMatch(/<details[^>]*class="[^"]*\bdisclosure\b[^"]*"[^>]*>\s*<summary>(?:(?!<\/summary>).)*Disordine e domini del bersaglio(?:(?!<\/summary>).)*domini non verificati/);
    expect(html).not.toMatch(/open=""[^>]*>\s*<summary>(?:(?!<\/summary>).)*Disordine e domini/);
    expect(html.indexOf("La sequenza viene inviata a NCBI BLAST.")).toBeLessThan(html.indexOf("<details"));
  });
});
