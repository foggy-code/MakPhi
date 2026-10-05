import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ConsensusResult, ResidueRow } from "../../api/types";
import type { ResidueColoring } from "../../lib/residueColoring";
import { ConsensusTracks, FeatureMap, Heatmap, ResidueProfile, ResidueStrip, StackBar } from ".";

// Grafici lineari nella cornice comune (PNG, CSV, Espandi): markup statico senza provider, come gli altri test.
const residue = (i: number, extra: Partial<ResidueRow> = {}): ResidueRow => ({
  chain: "A", resnum: String(i + 1), resname: "ALA", aa: "A", ss: "H", ss3: "H", rsa: 0.3, bfactor: 20,
  phi: -60, psi: -45, rama: "favored", clashes: 0, dope: -0.02 * i, ...extra,
});
const residues = Array.from({ length: 40 }, (_, i) => residue(i, i >= 30 ? { ss: "", ss3: "C" } : {}));
const parts = [{ label: "Elica", value: 23.68, color: "red" }, { label: "Coil", value: 76.32, color: "gray" }];

describe("barre di composizione", () => {
  it("senza titolo: la barra semplice di sempre, senza cornice", () => {
    expect(renderToStaticMarkup(<StackBar parts={parts} />)).toBe(
      '<div class="stack-sm"><div style="display:flex;height:12px;border-radius:6px;overflow:hidden;background:var(--panel-3)">'
      + '<div title="Elica: 23,7%" style="width:23.68%;background:red"></div><div title="Coil: 76,3%" style="width:76.32%;background:gray"></div></div>'
      + '<div class="legend"><span><i style="background:red"></i>Elica 23,7%</span><span><i style="background:gray"></i>Coil 76,3%</span></div></div>',
    );
  });
  it("con titolo: cornice con PNG, CSV, Espandi e la legenda nell'immagine", () => {
    const html = renderToStaticMarkup(<StackBar parts={parts} title="Struttura secondaria" subtitle="DSSP" />);
    expect(html).toContain('class="chart-frame-title">Struttura secondaria<');
    expect(html).toContain("PNG");
    expect(html).toContain("CSV");
    expect(html).toContain("Espandi");
    expect(html).toContain('title="Elica: 23,7%"');
    expect(html).toContain("Coil 76,3%</span>");
  });
});

describe("profilo per residuo", () => {
  it("cornice con CSV, zoom sui residui e struttura secondaria nell'SVG", () => {
    const html = renderToStaticMarkup(<ResidueProfile residues={residues} metric="dope" selected={{ chain: "A", resnum: "5" }}
      threshold={{ value: -0.2, label: "soglia" }} />);
    expect(html).toContain('class="chart-frame-title">Energia DOPE per residuo<');
    expect(html).toContain("Media mobile calcolata da MODELLER");
    expect(html).toContain("CSV");
    expect(html).toContain("Espandi");
    expect(html).toContain("residui 1–40 (40 in tutto)");
    expect(html).toContain('class="chart zoomable"');
    expect(html).toContain("<clipPath");
    expect(html).toContain('fill="var(--helix)"'); // un tratto di elica (1–30), non un rettangolo per residuo
    expect((html.match(/fill="var\(--helix\)"/g) ?? []).length).toBe(1);
    expect(html).toContain(">soglia<");
    expect(html).toContain('stroke="var(--warn)"'); // residuo selezionato e soglia
    expect(html).toContain("<circle"); // 40 residui visibili: anche i punti
    // soglia dopo i dati, con l'alone del pannello
    expect(html).toContain('class="ref-label"');
    expect(html.indexOf('class="ref-label"')).toBeGreaterThan(html.indexOf("clip-path="));
  });
  it("primo e ultimo residuo mezzo passo dentro il ritaglio (dominio −0,5…n−0,5)", () => {
    const html = renderToStaticMarkup(<ResidueProfile residues={residues} metric="dope" />);
    const clipX = Number(/<clipPath[^>]*><rect x="([\d.]+)"/.exec(html)?.[1]);
    const dots = [...html.matchAll(/<circle cx="([\d.]+)"/g)].map((m) => Number(m[1]));
    expect(dots).toHaveLength(40);
    expect(Math.min(...dots)).toBeGreaterThan(clipX + 5);
  });
  it("serie personalizzata con la sua etichetta; senza valori un avviso", () => {
    const html = renderToStaticMarkup(<ResidueProfile residues={residues} values={residues.map((_, i) => i / 40)} label="QMEAN locale per residuo" hint="Da 0 a 1" />);
    expect(html).toContain('class="chart-frame-title">QMEAN locale per residuo<');
    expect(html).toContain("Da 0 a 1");
    const empty = renderToStaticMarkup(<ResidueProfile residues={residues} values={residues.map(() => null)} label="X" />);
    expect(empty).toContain('<b class="text-2">X</b>: dato non disponibile per questa struttura.');
    expect(empty).not.toContain("chart-frame");
  });
  it("profilo DOPE non calcolato: il nome del grafico e come ottenerlo", () => {
    const html = renderToStaticMarkup(<ResidueProfile residues={residues.map((r) => ({ ...r, dope: null }))} metric="dope" />);
    expect(html).toContain("Energia DOPE per residuo</b>: non calcolato");
    expect(html).toContain("«Calcola il profilo DOPE (MODELLER)»");
  });
});

describe("segnalazioni per residuo", () => {
  const n = residues.length;
  const flags = residues.map((_, i) => (i >= 10 && i < 14 ? ["rama", "clash"] : i === 20 ? ["clash"] : [])) as ConsensusResult["per_residue"]["flags"];
  const consensus = {
    per_residue: {
      signals: [
        { key: "rama", label: "Ramachandran", rule: "outlier di Ramachandran", flagged: 4 },
        { key: "clash", label: "clash", rule: "almeno un contatto sterico grave", flagged: 5 },
      ],
      flags,
      counts: flags.map((f) => f.length),
      values: {},
      dope_threshold: null,
    },
    regions: [{ chain: "A", start: "11", end: "14", start_index: 10, end_index: 13, length: 4, flagged: 4, methods: ["rama", "clash"], text: "A11–A14: 2 metodi" }],
  } as unknown as ConsensusResult;
  it("cornice con legenda, righe leggibili e tratti uniti", () => {
    const html = renderToStaticMarkup(<ConsensusTracks residues={residues} consensus={consensus} />);
    expect(html).toContain('class="chart-frame-title">Segnalazioni per residuo<');
    expect(html).toContain(">Ramachandran (4)<");
    expect(html).toContain("outlier di Ramachandran");
    expect(html).toContain("Regione problematica");
    expect(html).toContain("CSV");
    expect(html).toContain(`residui 1–${n} (${n} in tutto)`);
    // colori per tema (--sig-*, ≥ 3:1 anche nel tema chiaro)
    expect((html.match(/fill="var\(--sig-rama\)"/g) ?? []).length).toBe(1); // 4 residui segnalati di fila: un rettangolo
    expect((html.match(/fill="var\(--sig-clash\)"/g) ?? []).length).toBe(2); // clash: 11–14 e 21
    expect(html).not.toMatch(/fill="#(ef4444|f59e0b)"/);
    // barre del numero di metodi: colori della scala del 3D, con il bordo
    expect(html).toMatch(/fill="#fde047" stroke="var\(--chart-axis\)"/);
    expect(html).toContain('fill="var(--err)"'); // la regione problematica, piena
    expect(html).not.toContain('stroke-dasharray="4 2"');
  });
  it("regione attesa (disordine predetto): vuota e tratteggiata, non solo di un altro colore", () => {
    const expected = { ...consensus, regions: [{ ...consensus.regions[0], expected: true }] } as ConsensusResult;
    const html = renderToStaticMarkup(<ConsensusTracks residues={residues} consensus={expected} />);
    expect(html).toMatch(/fill="var\(--warn-soft\)" stroke="var\(--warn\)" stroke-width="1.5" stroke-dasharray="4 2"/);
    expect(html).not.toContain('fill="var(--err)"');
    expect(html).toContain("Regione in disordine predetto (attesa, tratteggiata)");
    expect(html).toContain("border:1.5px dashed var(--warn)");
  });
  it("conteggi non allineati ai residui: avviso", () => {
    const html = renderToStaticMarkup(<ConsensusTracks residues={residues.slice(0, 5)} consensus={consensus} />);
    expect(html).toContain("Consenso per residuo non disponibile");
  });
});

describe("matrice di identità e mappa delle regioni", () => {
  it("Heatmap: titolo predefinito, legenda 0–100% e valori leggibili", () => {
    const html = renderToStaticMarkup(<Heatmap ids={["seq1", "seq2"]} values={[[100, 42.4], [42.4, 100]]} />);
    expect(html).toContain('class="chart-frame-title">Identità di sequenza a coppie<');
    expect(html).toContain("CSV");
    expect(html).toContain(">0%<");
    expect(html).toContain(">100%<");
    expect(html).toContain("fill:#000000"); // testo scuro sulle celle chiare (100%)
    expect(html).toContain("fill:#ffffff"); // testo chiaro sulle celle scure (42%)
    expect(html).toContain(">42<");
    expect(renderToStaticMarkup(<Heatmap ids={["a"]} values={[[100]]} title="Identità dei template" />)).toContain(">Identità dei template<");
  });
  it("Heatmap: la matrice sta nella larghezza del grafico, etichette inclinate comprese (PNG completo)", () => {
    const n = 60;
    const ids = Array.from({ length: n }, (_, i) => `UniRef_${i + 1}_omologo`);
    const values = ids.map((_, i) => ids.map((__, j) => (i === j ? 100 : 30 + ((i * j) % 50))));
    const html = renderToStaticMarkup(<Heatmap ids={ids} values={values} />);
    const W = Number(/viewBox="0 0 ([\d.]+) [\d.]+" class="chart"/.exec(html)?.[1]);
    expect(W).toBeLessThanOrEqual(900); // larghezza di riserva prima della misura
    expect(html).not.toContain("overflow-x");
    // l'etichetta inclinata dell'ultima colonna (16 caratteri a −50°) resta dentro l'SVG
    const lastX = Math.max(...[...html.matchAll(/rotate\(-50 ([\d.]+) /g)].map((m) => Number(m[1])));
    expect(lastX + 16 * 6.5 * Math.cos((50 * Math.PI) / 180)).toBeLessThanOrEqual(W);
    expect(html).not.toContain(">31<"); // celle piccole: valori solo nel suggerimento e nella tabella
  });
  it("FeatureMap: titolo predefinito o dato, zoom sulle posizioni", () => {
    const tracks = [{ label: "Domini", color: "#60a5fa", items: [{ start: 5, end: 60, title: "Pfam PF00001" }] }];
    const html = renderToStaticMarkup(<FeatureMap length={121} tracks={tracks} />);
    expect(html).toContain('class="chart-frame-title">Mappa delle regioni<');
    expect(html).toContain("posizioni 1–121 (121 in tutto)");
    expect(html).toContain(">Domini<");
    // tratti pieni con il bordo: visibili anche i chiari e quelli di 1–2 residui sul bianco
    expect(html).toMatch(/fill="#60a5fa" stroke="var\(--chart-axis\)"/);
    expect(html).not.toContain('opacity="0.85"');
    expect(renderToStaticMarkup(<FeatureMap length={121} tracks={tracks} title="Annotazioni UniProt" subtitle="P12345" />)).toContain(">Annotazioni UniProt<");
  });
});

describe("striscia per residuo", () => {
  const coloring: ResidueColoring = {
    key: "rsa",
    title: "Accessibilità (RSA)",
    values: residues.map((r, i) => ({ chain: r.chain, resnum: r.resnum, value: i < 39 ? i / 40 : null })),
    scale: { kind: "continuous", stops: [{ value: 0, color: "#000000" }, { value: 1, color: "#ffffff" }], ticks: [{ value: 0, label: "0" }, { value: 1, label: "1" }] },
    describe: (v) => String(v),
  };
  it("una cella per residuo con l'indice, senza gestori per cella; la selezione è un riquadro sopra", () => {
    const html = renderToStaticMarkup(<ResidueStrip residues={residues} coloring={coloring} selected={{ chain: "A", resnum: "11" }} />);
    expect((html.match(/data-i="/g) ?? []).length).toBe(40);
    expect(html).toContain('data-i="0" style="background:#000000"');
    expect(html).toContain('class="res-strip-sel" style="left:25%;width:2.5%"');
    expect(html).not.toContain('class="sel"');
    expect(html).not.toContain("heat-legend"); // senza `legend`: la legenda è quella del 3D accanto
  });
  it("con `legend`: la scala sotto la striscia, con «senza valore» se ci sono residui senza dato", () => {
    const html = renderToStaticMarkup(<ResidueStrip residues={residues} coloring={coloring} legend />);
    expect(html).toContain("Accessibilità (RSA)");
    expect(html).toContain("heat-bar");
    expect(html).toContain(">senza valore<");
  });
});
