import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AnalysisResult, JobSummary, Pocket, PocketsResult } from "../api/types";
import { ToastProvider } from "../lib/context";
import { JobPanel } from "./JobPanel";
import { AnalysisView } from "./StructuralAnalysis";
import { PocketsView } from "./StructureTools";
import { ValueBars } from "./ValueBars";

// Markup statico (react-dom/server) dei pannelli di analisi: grafico a barre, stato dei job, tasche.
const render = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter><ToastProvider>{node}</ToastProvider></MemoryRouter>);

describe("grafico a barre", () => {
  const series = {
    items: [
      { key: "a", label: "Tasca 1", tick: "1", value: 0.8, color: "#E69F00" },
      { key: "b", label: "Tasca 2", tick: "2", value: null, color: "#56B4E9" },
      { key: "c", label: "Tasca 3", tick: "3", value: 0.2, color: "#009E73", faded: true },
    ],
    label: "Druggability (0–1)", digits: 2, domain: [0, 1] as [number, number], references: [{ value: 0.5, label: "probabilmente druggabile", color: "var(--ok)" }],
  };

  it("barre, soglia, etichette, PNG/CSV/Espandi; il residuo senza valore è un segno sullo zero", () => {
    const html = render(
      <ValueBars title="Descrittori delle tasche" filename="tasche" series={series} selected="a" onSelect={() => undefined}
        data={{ columns: [{ key: "t", label: "Tasca" }], rows: [{ t: 1 }, { t: 2 }, { t: 3 }] }} />,
    );
    expect(html).toContain('class="chart-frame-title">Descrittori delle tasche<');
    expect(html).toContain("probabilmente druggabile");
    expect(html).toContain('aria-label="Druggability (0–1): 3 elementi"');
    expect(html).toContain("chart selectable");
    expect(html).not.toContain("zoomable");
    expect(html).toContain('fill="#E69F00"');
    expect(html).toContain('fill-opacity="0.45"');
    expect(html).toContain('fill="var(--coil)"');
    expect(html).toContain('stroke="var(--text)"'); // barra selezionata
    expect(html).toContain("PNG");
    expect(html).toContain("CSV");
    expect(html).toContain("Espandi");
  });

  it("oltre 60 barre l'asse si ingrandisce, con la barra dello zoom", () => {
    const many = { ...series, items: Array.from({ length: 80 }, (_, i) => ({ key: String(i), label: `R${i + 1}`, tick: String(i + 1), value: i / 80, color: "var(--accent)" })) };
    const html = render(<ValueBars title="Profilo" filename="p" series={many} noun="residui" data={{ columns: [], rows: [] }} />);
    expect(html).toContain("chart zoomable");
    expect(html).toContain("1–80 di 80 residui");
  });

  it("senza barre niente grafico", () => {
    expect(renderToStaticMarkup(<ValueBars title="Vuoto" filename="v" series={{ ...series, items: [] }} data={{ columns: [], rows: [] }} />)).toBe("");
  });
});

describe("stato di un job", () => {
  const base: JobSummary = {
    id: "j1", kind: "pockets", title: "Tasche · 1UBQ", project_id: null, status: "running", progress: 0.45, stage: "fpocket", error: null,
    created_at: "2026-01-01T10:00:00Z", started_at: "2026-01-01T10:00:00Z", finished_at: null, updated_at: "2026-01-01T10:00:00Z",
  } as JobSummary;

  it("in corso: fase, percentuale, tempi; log a scomparsa con copia, download ed espansione", () => {
    const html = render(<JobPanel summary={base} log={"riga 1\nriga 2\nriga 3\n"} onCancel={() => undefined} />);
    expect(html).toContain("fpocket");
    expect(html).toContain("45%");
    expect(html).toContain("In corso da");
    expect(html).toContain("Tasche (fpocket)"); // tipo del job accanto al titolo
    expect(html).toContain("Annulla");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("3 righe");
    expect(html).toContain("Scarica");
    expect(html).toContain("Espandi");
    expect(html).toMatch(/<button[^>]*aria-haspopup="dialog"[^>]*>(?:(?!<\/button>).)*Espandi<\/button>/);
    expect(html).not.toContain("riga 2"); // log chiuso
    expect(render(<JobPanel summary={base} log="riga 1" defaultLogOpen />)).toContain("riga 1");
  });

  it("link alla pagina del job: navigazione interna, non un link esterno né «Dettagli»", () => {
    const html = render(<JobPanel summary={base} />);
    expect(html).toMatch(/<a[^>]*href="\/jobs\?id=j1"[^>]*>(?:(?!<\/a>).)*Pagina del job<\/a>/);
    expect(html).not.toContain("> Dettagli<");
  });

  it("fallito: errore in evidenza; senza log niente sezione del log", () => {
    const html = render(<JobPanel summary={{ ...base, status: "failed", error: "fpocket: file vuoto", finished_at: "2026-01-01T10:01:00Z" }} compact />);
    expect(html).toContain("Il calcolo non è riuscito");
    expect(html).toContain("fpocket: file vuoto");
    expect(html).toContain("Durata");
    expect(html).not.toContain("job-log");
    expect(html).not.toContain("Annulla");
  });
});

describe("tasche", () => {
  const pocket = (id: number, rank: number, druggability: number): Pocket => ({
    id, rank, druggability, druggable: druggability >= 0.5, score: 0.3, volume: 400 + id, alpha_spheres: 30, center: [0, 0, 0], spheres: [], atoms: 40,
    residues: [{ chain: "A", resnum: String(10 + id), resname: "LEU" }], other: {},
  });
  const result = {
    source: { type: "template", id: "1UBQ" }, label: "1UBQ", source_sha256: "x", pockets: [pocket(2, 1, 0.81), pocket(1, 2, 0.12)],
    summary: { count: 2, druggable: 1, threshold: 0.5, best: { id: 2, druggability: 0.81, volume: 402, residues: 1 } },
    structure: { residues: 76, chains: 1, heavy_atoms: 600, chains_detail: [] }, warnings: [], files: [], params: { program: "fpocket 4.0", options: "predefiniti", druggable_threshold: 0.5 },
    pdb: "structure_out/structure_out.pdb",
  } as PocketsResult;

  it("grafico dei descrittori e tabella con righe espandibili, chiuse all'inizio", () => {
    const html = render(<PocketsView result={result} jobId="p1" url={null} />);
    expect(html).toContain("Descrittori delle tasche");
    expect(html).toContain("Tasca 2");
    expect(html).toContain('aria-label="Mostra descrittori della tasca 2"');
    expect(html).not.toContain("row-detail");
    expect(html).toContain("Apri tutte");
    expect(html).toContain("<details");
  });

  it("righe raggiungibili da tastiera, con la selezione esposta", () => {
    const html = render(<PocketsView result={result} jobId="p1" url={null} />);
    expect(html).toMatch(/<tr(?=[^>]* data-row-key="2")(?=[^>]* tabindex="0")(?=[^>]* aria-selected="false")[^>]*>/);
  });
});

describe("interfacce fra catene", () => {
  const end = (chain: string, resnum: string, resname: string, atom: string) => ({ chain, resnum, resname, atom });
  const res = (chain: string, resnum: string) => ({ chain, resnum, resname: "ASN", aa: "N", delta: 60, apolar_delta: 20 });
  const analysis = {
    source: { type: "template", id: "UBQD" }, label: "UBQD", source_sha256: "x",
    structure: { file: "UBQD.pdb", chains: 2, residues: 152, heavy_atoms: 1204, chains_detail: ["A", "B"].map((chain) => ({ chain, first: "1", last: "76", residues: 76, sequence: "", breaks: [], ligands: 0 })) },
    interactions: {
      version: 1, hydrogens: "placed", criteria: {},
      summary: { residues: 152, chains: 2, hbonds: 1, hbonds_backbone: 0, hbonds_mixed: 1, hbonds_sidechain: 0, salt_bridges: 0, disulfides: 0, pi_stacking: 0, cation_pi: 0, inter_chain: { hbonds: 1, salt_bridges: 0, disulfides: 0, aromatic: 0 } },
      hbonds: [{ a: end("B", "41", "GLN", "NE2"), b: end("A", "27", "LYS", "O"), distance: 3.04, ha_distance: 2.1, angle: 150.7, donor_angle: null, acceptor_angle: 130, hydrogen: "placed", kind: "mixed", inter_chain: true }],
      salt_bridges: [], disulfides: [], aromatic: [],
    },
    interfaces: {
      version: 1, method: "Shrake-Rupley", chains: ["A", "B"], min_delta: 1, interface_residues: 2, residues: [],
      pairs: [{
        chains: ["A", "B"], area: 122.9, buried: 245.8, apolar_fraction: 0.3, residues: { A: [res("A", "60")], B: [res("B", "60")] }, counts: { A: 1, B: 1 },
        composition: { counts: { apolari: 0, polari: 2, carichi: 0, altri: 0 }, percent: { apolari: 0, polari: 100, carichi: 0, altri: 0 } },
        interactions: { hbonds: 1, salt_bridges: 0, disulfides: 0, pi_stacking: 0, cation_pi: 0 },
        plausibility: { level: "bassa", text: "area sotto 400 Å²", limits: "solo l'area", thresholds: { low: 400, high: 600 } },
      }],
    },
    conservation: null, warnings: [],
  } as unknown as AnalysisResult;

  it("il grafico ΔSASA di ogni coppia si monta solo all'apertura della sezione; righe delle interazioni da tastiera", () => {
    const html = render(<AnalysisView result={analysis} url={null} />);
    expect(html).toContain("Superficie sepolta per residuo (ΔSASA)");
    expect(html).toContain("2 residui");
    expect(html).not.toContain("ΔSASA dei residui d&#x27;interfaccia");
    expect(html).toMatch(/<tr(?=[^>]* tabindex="0")(?=[^>]* aria-selected="false")[^>]*title="Evidenzia i due residui nel 3D/);
  });
});
