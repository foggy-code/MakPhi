import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Plausibility, PredictResult } from "../api/types";
import { ProjectProvider, ToastProvider } from "../lib/context";
import { ESMFOLD_PUBLIC_URL } from "../lib/predict";
import { PredictDialog, PredictionResults } from "./Predictor";

// Markup statico (react-dom/server) dei risultati di un job `predict`, senza il confronto 3D (comparison null).
const render = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter><ToastProvider>{node}</ToastProvider></MemoryRouter>);

const plausibility = (over: Partial<Plausibility> = {}): Plausibility => ({
  version: 1, predictor: "esmfold", label: "ESMFold", status: "divergenze locali", tone: "warn",
  summary: "Stesso ripiegamento (TM-score 0,93, lDDT 0,88), ma le due strutture divergono in 31-37 (catena A), dove la predizione è sicura (pLDDT medio 90): controlla l'allineamento e il template in questa regione.",
  tm_score: 0.9271, lddt: 0.8778, gdt_ts: 95.07, rmsd: 1.735, pairs: 76, plddt_mean: 90, confident_residues: 76, lddt_confident: 0.8721,
  divergent_residues: 7,
  regions: [{
    chain: "A", start: "31", end: "37", start_index: 30, end_index: 36, length: 7, divergent: 7, lddt_mean: 0.238, plddt_mean: 90, confidence: "sicura",
    consensus: [], where: "31-37 (catena A), dove la predizione è sicura (pLDDT medio 90)",
    text: "divergono in 31-37 (catena A), dove la predizione è sicura (pLDDT medio 90): controlla l'allineamento e il template in questa regione", tone: "warn",
  }],
  residues: [], consensus_available: true, notes: ["nota <b>"], limits: "Una predizione non è una misura sperimentale.", thresholds: {},
  ...over,
});

const result = (p: Plausibility | null): PredictResult => ({
  predictor: "esmfold", label: "ESMFold", origin: { service: ESMFOLD_PUBLIC_URL, cached: false, scale: "0-1" },
  prediction_file: "prediction_esmfold.pdb", recognized_as: "esmfold", sequence: { name: "1UBQ", length: 76 },
  plddt: { mean: 90, residues: 76, fractions: {} }, accession: null, source: { type: "template", id: "1UBQ", chain: "A" },
  model_label: "1UBQ", chain: "A", source_sha256: null, comparison: null, plausibility: p, warnings: [], limits: "",
});

describe("risultati del confronto con una predizione", () => {
  it("esito motivato, metriche, regioni e link", () => {
    const html = render(<PredictionResults jobId="j1" result={result(plausibility())} />);
    expect(html).toContain("alert-warn");
    expect(html).toContain("Il modello diverge dalla predizione dove questa è sicura.");
    expect(html).toContain("divergono in 31-37 (catena A)");
    expect(html).toContain("0,927");
    expect(html).toContain("76 residui su 76 con pLDDT ≥ 70");
    expect(html).toContain('href="/quality?job=j1&amp;file=prediction_esmfold.pdb"');
    expect(html).toContain("/api/jobs/j1/files/prediction_esmfold.pdb?download=true");
    expect(html).toContain("pLDDT del servizio su 0–1, riportato a 0–100");
    expect(html).toContain("nota &lt;b&gt;");
  });

  it("concordano e predizione senza confronto", () => {
    const ok = render(<PredictionResults jobId="j1" result={result(plausibility({ status: "concordano", tone: "ok", regions: [], summary: "Le due strutture concordano: TM-score 1,00 (stesso ripiegamento), lDDT 1,00." }))} />);
    expect(ok).toContain("alert-ok");
    expect(ok).toContain("Il modello concorda con la predizione.");
    expect(ok).not.toContain("Regioni che divergono");
    const only = render(<PredictionResults jobId="j2" result={{ ...result(null), source: null }} />);
    expect(only).toContain("Predizione senza confronto");
    expect(only).not.toContain("Apri in Confronto");
  });
});

describe("finestra di avvio del confronto", () => {
  const dialog = (residues: number | null) => render(
    <ProjectProvider><PredictDialog source={{ type: "template", id: "1UBQ", chain: "A" }} label="1UBQ" residues={residues} onClose={() => undefined} onLaunched={() => undefined} /></ProjectProvider>,
  );

  it("catena oltre il limite di ESMFold: nessun consenso da dare, avvio nascosto, AlphaFold DB proposto", () => {
    const html = dialog(450);
    expect(html).toContain("ESMFold accetta al più 400 residui (questa catena ne ha 450)");
    expect(html).toContain("AlphaFold DB");
    expect(html).not.toContain("Consenso all");
    expect(html).not.toContain("Avvia il confronto");
  });

  it("entro il limite: campo per la sequenza completa, impostazioni in lettura", () => {
    const html = dialog(76);
    expect(html).not.toContain("ESMFold accetta al più");
    expect(html).toContain("Sequenza (facoltativa)");
    expect(html).toContain("spinner");
  });
});
