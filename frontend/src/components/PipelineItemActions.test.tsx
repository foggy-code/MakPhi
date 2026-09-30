import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ToastProvider } from "../lib/context";
import { PipelineItemActions, PipelineModelView } from "./PipelineItemActions";

// Markup statico (react-dom/server): i pulsanti che l'elemento offre, senza browser.
const render = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter><ToastProvider>{node}</ToastProvider></MemoryRouter>);
const JOB = "0123456789abcdef";
const MODEL = "items/0000_a/a.B99990001.pdb";
const REPORT = /<button[^>]*title="Report HTML autonomo, da aprire nel browser e stampare in PDF"/;

describe("azioni degli elementi di pipeline", () => {
  it("un elemento completato ha il report del modello, anche senza analisi di qualità", () => {
    const html = render(<PipelineItemActions jobId={JOB} item={{ best_model: MODEL, status: "completed" }} onView={() => undefined} />);
    expect(html).toMatch(REPORT);
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*title="Report HTML/);
    expect(html).toContain('title="Qualità"');
    expect(html).toContain('title="Scarica PDB"');
  });
  it("il report di un elemento non completato è disattivato; senza modello nessuna azione", () => {
    expect(render(<PipelineItemActions jobId={JOB} item={{ best_model: MODEL, status: "failed" }} onView={() => undefined} />))
      .toMatch(/<button[^>]*disabled=""[^>]*title="Report HTML/);
    expect(render(<PipelineItemActions jobId={JOB} item={{ best_model: null, status: "failed" }} onView={() => undefined} />)).not.toMatch(REPORT);
  });
  it("la finestra «Visualizza» genera il report con la vista 3D corrente", () => {
    const html = render(<PipelineModelView jobId={JOB} name="a" model={MODEL} onClose={() => undefined} />);
    expect(html).toContain("Modello di a");
    expect(html).toContain('title="Report HTML autonomo, da aprire nel browser e stampare in PDF (con la vista 3D se è aperta)"');
  });
});
