import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { QualityReport } from "../../api/types";
import { contrastRatio, parseColor } from "../../lib/contrast";
import { RamachandranPlot } from "./Ramachandran";

// Grafico di Ramachandran: colori standard (PROCHECK), forme dei punti, etichette degli outlier. Markup statico.
const report: QualityReport["ramachandran"] = {
  summary: { total: 4, favored: 2, allowed: 1, outlier: 1, favored_pct: 50, allowed_pct: 25, outlier_pct: 25, cis_non_proline: 0 },
  points: [
    { chain: "A", resnum: "12", resname: "ALA", aa: "A", phi: -63, psi: -41, class: "general", region: "favored" },
    { chain: "A", resnum: "30", resname: "SER", aa: "S", phi: -150, psi: -60, class: "prepro", region: "allowed" },
    { chain: "A", resnum: "45", resname: "ASP", aa: "D", phi: 60, psi: -150, class: "general", region: "outlier" },
    { chain: "A", resnum: "50", resname: "GLY", aa: "G", phi: 80, psi: 10, class: "glycine", region: "favored" },
  ],
  regions: {
    general: { favored: [[-115, -40, -75, -5]], allowed: [[-180, -35, -100, 180]] },
    glycine: { favored: [[40, 120, -10, 80]], allowed: [[30, 180, -180, 180]] },
    proline: { favored: [[-95, -45, -65, -5]], allowed: [[-110, -40, -90, 60]] },
  },
};

const plane = (html: string) => html.slice(html.indexOf('<svg class="chart zoomable"'), html.indexOf("</svg>", html.indexOf('<svg class="chart zoomable"')));

describe("grafico di Ramachandran", () => {
  it("colori standard: piano bianco, regioni consentite gialle sotto le favorite rosse", () => {
    const svg = plane(renderToStaticMarkup(<RamachandranPlot report={report} />));
    expect(svg).toContain('fill="#ffffff"');
    const yellow = svg.indexOf('fill="#ffff00"');
    const red = svg.indexOf('fill="#ff0000"');
    expect(yellow).toBeGreaterThan(0);
    expect(red).toBeGreaterThan(yellow);
    // niente colori del tema dentro il piano: uguale nei due temi (e nel PNG)
    expect(svg).not.toMatch(/fill="var\(--/);
  });

  it("vista Tutti (predefinita): tutti i residui, glicine a triangolo, gli altri quadratini neri", () => {
    const html = renderToStaticMarkup(<RamachandranPlot report={report} />);
    expect(html).toContain("Grafico di Ramachandran");
    expect(html).toContain("Tutti i residui · 4 residui · 50,0% in regioni favorite · 1 outlier");
    const svg = plane(html);
    const glycine = svg.match(/data-mark="glycine" d="([^"]+)"/);
    expect(glycine?.[1]).toMatch(/^M[\d. ]+L[\d. ]+L[\d. ]+z$/); // un triangolo
    const residues = svg.match(/data-mark="residue" d="([^"]+)" fill="#000000"/);
    expect(residues?.[1]).toMatch(/^(M[\d. -]+h[\d.]+v[\d.]+h-[\d.]+z){3}$/); // tre quadratini
  });

  it("outlier etichettato in rosso, leggibile sul bianco", () => {
    const svg = plane(renderToStaticMarkup(<RamachandranPlot report={report} />));
    const label = svg.match(/<text[^>]*style="fill:(#[0-9a-f]{6})[^"]*"[^>]*>ASP A45<\/text>/);
    expect(label).not.toBeNull();
    const red = parseColor(label![1])!;
    expect(red[0]).toBeGreaterThan(150);
    expect(red[1] + red[2]).toBe(0);
    expect(contrastRatio(red, [255, 255, 255, 1])).toBeGreaterThanOrEqual(4.5);
    // nella pagina le altre etichette restano fuori
    expect(svg).not.toContain(">ALA A12<");
  });

  it("etichette vicino ai bordi destro e superiore: a sinistra e sotto il punto, dentro l'area", () => {
    const edge = { ...report, points: [{ chain: "A", resnum: "45", resname: "ASP", aa: "D", phi: 165, psi: 178, class: "general", region: "outlier" as const }] };
    const svg = plane(renderToStaticMarkup(<RamachandranPlot report={edge} />));
    const label = svg.match(/<text x="([\d.]+)" y="([\d.]+)" text-anchor="end"[^>]*>ASP A45<\/text>/);
    expect(label).not.toBeNull();
    // area della pagina: x 50…410, y 12…372
    expect(Number(label![1])).toBeLessThan(410);
    expect(Number(label![2]) - 10).toBeGreaterThan(12);
  });

  it("residuo selezionato: anello blu ed etichetta", () => {
    const svg = plane(renderToStaticMarkup(<RamachandranPlot report={report} selected={{ chain: "A", resnum: "12" }} />));
    expect(svg).toContain('stroke="#1d4ed8"');
    expect(svg).toContain(">ALA A12<");
    expect(plane(renderToStaticMarkup(<RamachandranPlot report={report} />))).not.toContain('stroke="#1d4ed8"');
  });

  it("legenda, assi e comandi", () => {
    const html = renderToStaticMarkup(<RamachandranPlot report={report} />);
    for (const text of ["Regioni favorite", "Regioni consentite", "Non consentite", "Residuo", "Glicina", "Outlier (etichettato)", "φ (gradi)", "ψ (gradi)"]) {
      expect(html).toContain(text);
    }
    for (const view of ["Tutti", "Generale", "Glicina", "Prolina"]) expect(html).toContain(`>${view}</button>`);
    expect(html).toContain('aria-label="Ingrandisci"');
    expect(html).toContain("PNG");
    expect(html).toContain("CSV");
    expect(html).toContain("Espandi");
    // tacche di 45° con il meno tipografico
    expect(plane(html)).toContain(">−135</text>");
  });

  it("zoom del piano: comandi fissi (Tutto disattivato a piano intero), gruppi di scelta con un nome", () => {
    const html = renderToStaticMarkup(<RamachandranPlot report={report} />);
    expect(html).toContain('class="zoom-bar plane"');
    expect(html).toContain('aria-label="Riduci lo zoom" disabled=""');
    expect(html).toMatch(/title="Mostra tutto il piano" disabled=""/);
    expect(html).toContain('role="radiogroup" aria-label="Vista del grafico"');
    // regione non consentita con il nome usato nel resto dell'interfaccia
    expect(html).toContain("Non consentite (outlier)");
  });
});
