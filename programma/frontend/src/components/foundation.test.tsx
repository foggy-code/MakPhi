import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChartFrame, tableRows, ZoomBar } from "./ChartFrame";
import { Card, DetailPanel, Disclosure, ExpandAll, Modal, Segmented, Spinner, Stat } from "./ui";
import type { DomainState } from "../lib/zoom";

// Parti comuni dell'interfaccia: markup statico (come gli altri test dei componenti), niente window né document.
const noop = () => {};

describe("tabella dei dati", () => {
  const data = {
    columns: [{ key: "r", label: "Residuo" }, { key: "v", label: "Valore", num: true }],
    rows: [{ r: "A1", v: 0.8734 }, { r: "A2", v: null }, { r: "A3", v: 2.5 }, { r: "A4", v: -1 }],
  };
  it("i vuoti in fondo in entrambe le direzioni", () => {
    expect(tableRows(data, "", { key: "v", dir: 1 }).map((x) => x.r.r)).toEqual(["A4", "A1", "A3", "A2"]);
    expect(tableRows(data, "", { key: "v", dir: -1 }).map((x) => x.r.r)).toEqual(["A3", "A1", "A4", "A2"]);
  });
  it("il filtro trova il testo mostrato e il valore grezzo", () => {
    expect(tableRows(data, "0,87", null).map((x) => x.r.r)).toEqual(["A1"]);
    expect(tableRows(data, "0.87", null).map((x) => x.r.r)).toEqual(["A1"]);
    expect(tableRows(data, "—", null).map((x) => x.r.r)).toEqual(["A2"]);
  });
});

describe("cornice dei grafici", () => {
  const html = renderToStaticMarkup(
    <ChartFrame title="Profilo DOPE" filename="dope" data={{ columns: [], rows: [] }} controls={<span>comandi</span>}>
      <svg />
    </ChartFrame>,
  );
  it("gruppo col nome del titolo; pulsanti col nome del grafico", () => {
    const id = /class="chart-frame" role="group" aria-labelledby="([^"]+)"/.exec(html)?.[1];
    expect(id).toBeTruthy();
    expect(html).toContain(`id="${id}" class="chart-frame-title">Profilo DOPE<`);
    expect(html).toContain('aria-label="Scarica PNG: Profilo DOPE"');
    expect(html).toContain('aria-label="Scarica CSV: Profilo DOPE"');
    expect(html).toContain('aria-haspopup="dialog" aria-label="Espandi: Profilo DOPE"');
  });
  it("comandi e azioni in due gruppi separati", () => {
    expect(html).toMatch(/class="chart-frame-controls" data-export-ignore="">.*comandi.*<div class="chart-frame-actions"/);
  });
  it("titleHidden: titolo solo per i lettori di schermo, sottotitolo visibile", () => {
    const hidden = renderToStaticMarkup(<ChartFrame title="Struttura" subtitle="percentuale" filename="ss" titleHidden><svg /></ChartFrame>);
    expect(hidden).not.toContain('class="chart-frame-title"');
    expect(hidden).toMatch(/class="sr-only">Struttura</);
    expect(hidden).toContain('class="chart-frame-sub">percentuale<');
  });
  it("ZoomBar: pulsanti sempre presenti, disattivati con tutto l'intervallo", () => {
    const zoom = { domain: { lo: 0, hi: 10 }, full: true } as DomainState;
    const bar = renderToStaticMarkup(<ZoomBar zoom={zoom} format={(lo, hi) => `${lo}–${hi}`} />);
    for (const name of ["Sposta indietro", "Sposta avanti", "Riduci lo zoom"]) expect(bar).toMatch(new RegExp(`aria-label="${name}" disabled=""`));
    expect(bar).toMatch(/title="Mostra tutto" disabled="">.*Tutto/);
  });
});

describe("Card e sezioni", () => {
  it("Card richiudibile: titolo intestazione con il pulsante dentro", () => {
    const html = renderToStaticMarkup(<Card title="PROCHECK" collapsible><p>contenuto</p></Card>);
    expect(html).toMatch(/<h3><button type="button" class="card-toggle" aria-expanded="true" aria-controls="[^"]+"/);
    expect(html).toContain('<div class="card-body"');
    expect(html).toContain("contenuto");
  });
  it("Card chiusa all'inizio: contenuto montato solo alla prima apertura", () => {
    const html = renderToStaticMarkup(<Card title="PROCHECK" collapsible defaultOpen={false}><p>contenuto</p></Card>);
    expect(html).not.toContain("contenuto");
    expect(html).not.toContain("aria-controls");
  });
  it("Disclosure lazy: niente contenuto finché chiusa; aperta all'inizio sì", () => {
    expect(renderToStaticMarkup(<Disclosure title="ΔSASA" lazy>grafico</Disclosure>)).not.toContain("grafico");
    expect(renderToStaticMarkup(<Disclosure title="ΔSASA" lazy defaultOpen>grafico</Disclosure>)).toContain("grafico");
    expect(renderToStaticMarkup(<Disclosure title="ΔSASA">grafico</Disclosure>)).toContain("grafico");
  });
  it("Stat: esito scritto oltre al colore; Dettagli col nome della statistica", () => {
    const html = renderToStaticMarkup(<Stat label="Clashscore" value="9,2" tone="warn" detail="soglie" />);
    expect(html).toContain('class="stat-verdict"');
    expect(html).toContain("esito: </span>da controllare");
    expect(html).toContain('aria-label="Dettagli: Clashscore"');
    expect(renderToStaticMarkup(<Stat label="TM" value="0,4" tone="err" verdict="ripiegamento diverso" />)).toContain("ripiegamento diverso");
    expect(renderToStaticMarkup(<Stat label="TM" value="0,4" tone="err" verdict={false} />)).not.toContain("stat-verdict");
    expect(renderToStaticMarkup(<Stat label="Info" value="1" tone="info" />)).not.toContain("stat-verdict");
  });
  it("Segmented: nome del gruppo e una sola tappa del Tab", () => {
    const html = renderToStaticMarkup(<Segmented label="Metrica" value="b" onChange={noop} options={[{ value: "a", label: "A" }, { value: "b", label: "B" }]} />);
    expect(html).toContain('role="radiogroup" aria-label="Metrica"');
    expect(html).toContain('aria-checked="false" tabindex="-1"');
    expect(html).toContain('aria-checked="true" tabindex="0"');
  });
  it("ExpandAll: apre le righe, femminile o maschile, testo personalizzato", () => {
    expect(renderToStaticMarkup(<ExpandAll allOpen={false} onExpand={noop} onCollapse={noop} />)).toContain('aria-label="Apri tutte le righe"');
    const m = renderToStaticMarkup(<ExpandAll allOpen onExpand={noop} onCollapse={noop} masculine noun="contatti" />);
    expect(m).toContain('aria-label="Chiudi tutti i contatti"');
    expect(m).toContain("Chiudi tutti");
    expect(renderToStaticMarkup(<ExpandAll allOpen={false} onExpand={noop} onCollapse={noop} masculine noun="template" label="Apri i primi 20" />))
      .toContain('aria-label="Apri i primi 20 template"');
  });
  it("Spinner annunciato, DetailPanel con la sua etichetta di chiusura", () => {
    expect(renderToStaticMarkup(<Spinner label="Caricamento dei modelli" />)).toContain('role="status" aria-label="Caricamento dei modelli"');
    const panel = renderToStaticMarkup(<DetailPanel title="ALA A12" label="Residuo" onClose={noop} closeLabel="Chiudi i dettagli del residuo">x</DetailPanel>);
    expect(panel).toContain('class="detail-panel" aria-label="Residuo"');
    expect(panel).toContain('aria-label="Chiudi i dettagli del residuo"');
  });
  it("Modal: dialogo col nome del titolo", () => {
    const html = renderToStaticMarkup(<Modal title="Progetti" size="wide" onClose={noop}>corpo</Modal>);
    const id = /aria-labelledby="([^"]+)"/.exec(html)?.[1];
    expect(html).toContain('class="modal wide" role="dialog" aria-modal="true"');
    expect(html).toContain(`<h2 id="${id}">Progetti</h2>`);
  });
});
