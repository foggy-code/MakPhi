import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Viewer3D } from "./Viewer3D";

// Comandi del visualizzatore nel markup iniziale (3Dmol si carica solo negli effetti, che qui non girano).
describe("visualizzatore 3D", () => {
  it("PNG ed Espandi in basso a destra, disabilitati finché non c'è una struttura", () => {
    const html = renderToStaticMarkup(<Viewer3D url="/api/jobs/j/files/modello.pdb" height={380} />);
    expect(html).toContain('class="viewer3d"');
    expect(html).toContain("height:380px");
    expect(html).toContain("Nessuna struttura selezionata");
    // pulsanti cercati per testo e stato, non per l'ordine degli attributi o l'elenco esatto delle classi
    const tr = html.slice(html.indexOf('class="overlay-tr"'));
    expect(tr).toMatch(/<button[^>]*disabled=""[^>]*>(?:(?!<\/button>).)*PNG<\/button>/);
    expect(tr).toMatch(/<button[^>]*disabled=""[^>]*>(?:(?!<\/button>).)*Espandi<\/button>/);
    expect(tr.indexOf("PNG")).toBeLessThan(tr.indexOf("Espandi"));
  });
  it("con il testo della struttura: Espandi attivo, PNG solo dopo il disegno", () => {
    const html = renderToStaticMarkup(<Viewer3D pdb={"ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n"} />);
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("Centra");
    expect(html).toMatch(/<button(?=[^>]*disabled="")[^>]*title="Scarica la vista corrente come immagine PNG"/);
    expect(html).toMatch(/<button(?![^>]*disabled)[^>]*title="Mostra la struttura a schermo intero"/);
  });
});
