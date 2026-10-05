import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AlignResult, StructuralPair } from "../api/types";
import { StructuralComparison } from "./StructuralComparison";

// Markup statico (react-dom/server) del confronto senza il viewer 3D (nessun PDB sovrapposto): metriche con i
// dettagli, profilo nella cornice dei grafici, tratti che deviano, tabella delle coppie.
const pair = (i: number, extra: Partial<StructuralPair> = {}): StructuralPair => ({
  chain_a: "A", resnum_a: String(i + 1), aa_a: "K", chain_b: "B", resnum_b: String(i + 1), aa_b: "K", distance: 0.8, lddt: 0.92, ...extra,
});
const pairs = Array.from({ length: 40 }, (_, i) => pair(i, i >= 10 && i < 14 ? { distance: 3.2, lddt: 0.41, aa_b: "R" } : {}));

const result = (over: Partial<AlignResult> = {}): AlignResult => ({
  mode: "structural", method: "US-align", ids: ["1UBQ", "model_1"], aligned: ["K".repeat(40), "K".repeat(40)],
  tm_score: 0.8123, tm_score_b: 0.79, rmsd: 1.234, rmsd_close: 0.9, aligned_length: 40, close_pairs: 40, lengths: [42, 40],
  gdt_ts: 91.2, gdt_ha: 75.3, lddt: 0.861, lddt_distances: 412, comparison_version: 1, pairs,
  gdt: [{ cutoff: 0.5, count: 20, percent: 47.6 }, { cutoff: 1, count: 30, percent: 71.4 }, { cutoff: 2, count: 36, percent: 85.7 }, { cutoff: 4, count: 40, percent: 95.2 }, { cutoff: 8, count: 40, percent: 95.2 }],
  ...over,
});

describe("confronto strutturale", () => {
  it("metriche con tono, dettagli e soglie per GDT", () => {
    const html = renderToStaticMarkup(<StructuralComparison jobId="j1" result={result()} />);
    expect(html).toContain("Metriche del confronto");
    expect(html).toContain("A = 1UBQ (riferimento) · B = model_1");
    expect(html).toContain("stat stat-ok"); // TM-score 0,81
    expect(html).toContain("Dettagli");
    expect(html).toContain("0,812");
    expect(html).toContain("Cα di A entro ogni soglia (GDT)");
    expect(html).toContain("GDT-TS, GDT-HA"); // le soglie 1, 2, 4 Å entrano in entrambi
  });

  it("profilo nella cornice: titolo, zoom sulle coppie, PNG, CSV, Espandi", () => {
    const html = renderToStaticMarkup(<StructuralComparison jobId="j1" result={result()} />);
    expect(html).toContain('class="chart-frame-title">Deviazione per posizione<');
    expect(html).toContain("tratti che deviano: almeno 3 coppie oltre 2 Å");
    expect(html).toContain("A: 1–40 · 40 coppie");
    expect(html).toContain("PNG");
    expect(html).toContain("CSV");
    expect(html).toContain("Espandi");
    expect(html).toContain('class="chart zoomable"');
    expect(html).toContain("<clipPath");
    expect(html).toContain('fill="var(--warn-soft)"'); // il tratto che devia, evidenziato nel grafico
  });

  it("tratti che deviano come chip, tabella con le sostituzioni e invito a selezionare", () => {
    const html = renderToStaticMarkup(<StructuralComparison jobId="j1" result={result()} />);
    // il chip non premuto, senza legarsi all'ordine degli attributi né all'elenco esatto delle classi
    expect(html).toMatch(/<button(?=[^>]*\bclass="[^"]*\bchip\b)(?=[^>]*\baria-pressed="false")[^>]*>/);
    expect(html).toContain("11–14 (catena A): fino a 3,2 Å");
    expect(html).toContain("Coppie allineate");
    expect(html).toContain(">simile<"); // K → R, stessa classe
    expect(html).toContain("Seleziona una coppia");
  });

  it("risultato di una versione precedente: niente profilo né tabella", () => {
    const html = renderToStaticMarkup(<StructuralComparison jobId="j1" result={result({ pairs: [], comparison_version: undefined, gdt_ts: undefined, gdt: [] })} />);
    expect(html).toContain("Confronto per residuo non disponibile");
    expect(html).not.toContain("Deviazione per posizione");
    expect(html).not.toContain("Coppie allineate");
  });
});

describe("confronto strutturale: esiti, tastiera e soglia", () => {
  it("TM-score con l'esito a parole della metrica; ordinamento e righe da tastiera; soglia con l'alone", () => {
    const html = renderToStaticMarkup(<StructuralComparison jobId="j1" result={result()} />);
    expect(html).toMatch(/class="stat-verdict">.*stesso ripiegamento/);
    // intestazioni: un pulsante per ordinare, aria-sort sulla colonna ordinata
    expect(html).toMatch(/<th(?=[^>]*\baria-sort="ascending")[^>]*><button(?=[^>]*\bclass="th-sort")[^>]*>Posizione/);
    expect(html.match(/class="th-sort"/g)).toHaveLength(3);
    expect(html).toMatch(/<tr(?=[^>]*\btabindex="0")(?=[^>]*\bclass="clickable)[^>]*>/);
    expect(html).toContain('class="ref-label"');
    expect(html).not.toContain("cmp-detail");
  });
});
