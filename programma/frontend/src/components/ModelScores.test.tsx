import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ModelEntry } from "../api/types";
import { ModelScores } from "./ModelScores";

const model = (file: string, dope: number | null, extra: Partial<ModelEntry> = {}): ModelEntry => ({
  file, dope, zdope: null, molpdf: 900, ga341: 0.9, failure: null, kind: "model", best: false, ...extra,
});

const models = [model("t.B99990001.pdb", -1200), model("t.B99990002.pdb", -1300, { best: true }), model("t.B99990003.pdb", null, { failure: "errore" })];

describe("punteggi dei modelli", () => {
  it("grafico nella cornice con PNG, CSV ed Espandi; un punto per modello riuscito, il fallito segnato", () => {
    const html = renderToStaticMarkup(<ModelScores models={models} selected="t.B99990002.pdb" onSelect={() => undefined} filename="punteggi" />);
    expect(html).toContain("Punteggi dei modelli");
    expect(html).toContain('aria-label="DOPE di 3 modelli"');
    for (const label of ["PNG", "CSV", "Espandi"]) expect(html).toContain(label);
    const svg = html.slice(html.indexOf('<svg class="chart shell-scores"'), html.indexOf("</svg>", html.indexOf('<svg class="chart shell-scores"')));
    expect(svg.match(/data-mark="/g)).toHaveLength(2);
    expect(html).toContain("×");
    // metriche con almeno un valore: DOPE, GA341, molpdf (zDOPE e voto assenti)
    expect(html).toContain(">GA341<");
    expect(html).not.toContain(">zDOPE<");
    expect(html).toContain("mostrato nel 3D: t.B99990002.pdb");
  });

  it("il migliore si distingue per forma (rombo), non solo per colore; i loop sono triangoli", () => {
    const loops = [...models, model("t.BL00010001.pdb", -1250, { kind: "loop" })];
    const html = renderToStaticMarkup(<ModelScores models={loops} selected="t.B99990001.pdb" onSelect={() => undefined} filename="p" />);
    const svg = html.slice(html.indexOf('<svg class="chart shell-scores"'), html.indexOf("</svg>", html.indexOf('<svg class="chart shell-scores"')));
    expect(svg).toMatch(/<path data-mark="best" d="M[\d. ]+L[\d. ]+L[\d. ]+L[\d. ]+Z"[^>]*fill="var\(--ok\)"/);
    expect(svg).toMatch(/<path data-mark="loop" d="M[\d. ]+L[\d. ]+L[\d. ]+Z"[^>]*fill="var\(--accent-2\)"/);
    expect(svg).toMatch(/<circle data-mark="model"[^>]*fill="var\(--accent\)"/);
    // legenda con le stesse forme
    const legend = html.slice(html.indexOf('<div class="legend">'));
    for (const kind of ["best", "model", "loop"]) expect(legend).toContain(`data-mark="${kind}"`);
  });

  it("gruppi di scelta con un nome (metrica, ordine)", () => {
    const html = renderToStaticMarkup(<ModelScores models={models} selected="" onSelect={() => undefined} filename="p" />);
    expect(html).toContain('role="radiogroup" aria-label="Metrica"');
    expect(html).toContain('role="radiogroup" aria-label="Ordine"');
  });

  it("niente grafico con un solo modello", () => {
    expect(renderToStaticMarkup(<ModelScores models={[models[0]]} selected="" onSelect={() => undefined} filename="x" />)).toBe("");
  });
});
