import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChartFrame, DataTable } from "./ChartFrame";
import { Card, Disclosure, Stat } from "./ui";

// Cornice dei grafici e sezioni espandibili: markup statico senza provider (come gli altri test dei componenti).
describe("cornice dei grafici", () => {
  it("titolo, pulsanti PNG/CSV/Espandi fuori dall'immagine, didascalia per l'esportazione", () => {
    const html = renderToStaticMarkup(
      <ChartFrame title="Profilo DOPE" subtitle="finestra 15" filename="dope" data={{ columns: [{ key: "a", label: "A" }], rows: [{ a: 1 }] }}
        legend={<div className="legend">legenda</div>}>
        {({ expanded }) => <svg className="chart" data-expanded={String(expanded)} />}
      </ChartFrame>,
    );
    expect(html).toContain('class="chart-frame-title">Profilo DOPE<');
    expect(html).toContain('data-export-ignore=""');
    expect(html).toContain("PNG");
    expect(html).toContain("CSV");
    expect(html).toContain("Espandi");
    expect(html).toContain('class="chart-caption"');
    expect(html).toContain('data-expanded="false"');
    expect(html).toContain("legenda");
  });
  it("senza dati niente CSV; senza espansione niente Espandi", () => {
    const html = renderToStaticMarkup(<ChartFrame title="T" filename="t" expandable={false}><svg /></ChartFrame>);
    expect(html).not.toContain("CSV");
    expect(html).not.toContain("Espandi");
  });
  it("tabella dei dati: intestazioni e numeri all'italiana", () => {
    const html = renderToStaticMarkup(
      <DataTable data={{ columns: [{ key: "r", label: "Residuo" }, { key: "v", label: "Valore", num: true }], rows: [{ r: "ALA A1", v: -1.2345 }, { r: "GLY A2", v: null }] }} />,
    );
    expect(html).toContain("Residuo");
    expect(html).toContain("-1,235");
    expect(html).toContain("—");
    expect(html).toContain("2 di 2 righe");
  });
});

describe("sezioni espandibili", () => {
  it("Card richiudibile: aperta per impostazione, con freccia e aria-expanded", () => {
    const html = renderToStaticMarkup(<Card title="Dettagli" collapsible icon="info"><p>contenuto</p></Card>);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("contenuto");
    const closed = renderToStaticMarkup(<Card title="Dettagli" collapsible defaultOpen={false}><p>contenuto</p></Card>);
    expect(closed).toContain('aria-expanded="false"');
    expect(closed).not.toContain("contenuto");
  });
  it("Disclosure: contenuto nella pagina anche se chiusa", () => {
    const html = renderToStaticMarkup(<Disclosure title="Scale e fonti" extra="3 metodi">testo</Disclosure>);
    expect(html).toContain("<details");
    expect(html).toContain("testo");
    expect(html).toContain("3 metodi");
  });
  it("Stat con dettagli e tono", () => {
    const html = renderToStaticMarkup(<Stat label="Clashscore" value="2,1" detail="spiegazione" tone="ok" />);
    expect(html).toContain("stat stat-ok");
    expect(html).toContain("Dettagli");
    expect(html).not.toContain("spiegazione"); // chiuso all'inizio
  });
});
