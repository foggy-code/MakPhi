import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { ComplexResult } from "../api/types";
import { missingAssessment } from "../lib/complex";
import { ToastProvider } from "../lib/context";
import { ComplexResults } from "./ComplexResults";

// Markup statico (react-dom/server): tabella del confronto delle interfacce di un omodimero, senza browser.
const render = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter><ToastProvider>{node}</ToastProvider></MemoryRouter>);

const COMPLEX: ComplexResult = {
  chains: [
    { index: 0, model_chain: "A", template_chain: "A", length: 76, name: "ub1", same_as: null },
    { index: 1, model_chain: "B", template_chain: "B", length: 76, name: "ub1", same_as: "A" },
  ],
  symmetry: { requested: true, applied: true, groups: [["A", "B"]], weight: 1 },
  template: "UBQD_AB",
  mode: "complex",
  assessment: {
    version: 1, template: "UBQD_AB", chains: [{ template: "A", model: "A" }, { template: "B", model: "B" }],
    interfaces: { version: 1, method: "Shrake-Rupley", chains: ["A", "B"], min_delta: 1, pairs: [] },
    template_interfaces: null,
    comparison: [{
      template_chains: ["A", "B"], model_chains: ["A", "B"], status: "alta", tone: "ok", area_template: 122.9, area_model: 117.2,
      area_ratio: 95.4, template_residues: 8, conserved: 6, conserved_percent: 75, unaligned: 0, conserved_residues: [], lost_residues: [],
      fnat: 1, irms: 0.35, lrms: 1.66, dockq: 0.97, dockq_class: "alta", native_contacts: 5, reproduced_contacts: 5, receptor: "A", ligand: "B",
      text: "Interfaccia del template riprodotta: DockQ 0,97 …",
    }],
    status: "alta", tone: "ok", summary: "…", notes: [], limits: "Il template non è la struttura vera del complesso.",
  },
};

describe("risultati di un complesso", () => {
  it("catene, simmetria e confronto dell'interfaccia con il template", () => {
    const html = render(<ComplexResults jobId="j" complex={COMPLEX} selected="m1.pdb" best="m1.pdb" onView={() => undefined} />);
    expect(html).toContain("Complesso · 2 catene");
    expect(html).toContain("A ← catena A del template (76 aa, ub1); B ← catena B del template (76 aa, come A)");
    expect(html).toContain("Restrizioni di simmetria sui C-alfa fra A = B (peso 1,0)");
    expect(html).toContain("75% (6/8)");
    expect(html).toContain("0,97");
    expect(html).toContain(">alta<");
    expect(html).toContain("Il template non è la struttura vera del complesso.");
  });

  it("modello migliore senza analisi delle interfacce: analisi non riuscita, non «nel risultato del migliore»", () => {
    const html = render(<ComplexResults jobId="j" complex={{ ...COMPLEX, assessment: null, symmetry: { ...COMPLEX.symmetry, applied: false } }} selected="m1.pdb" best="m1.pdb" onView={() => undefined} />);
    expect(html).toContain("Analisi delle interfacce non riuscita per il modello migliore (m1.pdb): vedi il log del job.");
    expect(html).not.toContain("sono nel risultato per il modello migliore");
    expect(html).toContain("Simmetria richiesta ma non applicata");
  });

  it("altro modello senza confronto nel rapporto di qualità: non analizzato, non «non riuscito»", () => {
    expect(missingAssessment("m2.pdb", "m1.pdb")).toEqual({
      tone: "info",
      text: "Interfacce non analizzate per m2.pdb: sono nel risultato per il modello migliore e nel rapporto dei modelli con l'analisi di qualità.",
    });
    expect(missingAssessment("m1.pdb", "m1.pdb")).toEqual({
      tone: "warn", text: "Analisi delle interfacce non riuscita per il modello migliore (m1.pdb): vedi il log del job.",
    });
  });

  it("catena del template allineata solo a gap: «?» al posto della catena del modello", () => {
    const comparison = [{ ...COMPLEX.assessment!.comparison[0], model_chains: ["A", null], dockq: null }];
    const html = render(<ComplexResults jobId="j" complex={{ ...COMPLEX, assessment: { ...COMPLEX.assessment!, comparison } }} selected="m1.pdb" best="m1.pdb" onView={() => undefined} />);
    expect(html).toContain("<b>A–?</b>");
  });
});
