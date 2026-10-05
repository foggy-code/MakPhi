import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { BarSeries } from "../lib/toolsView";
import { ValueBars } from "./ValueBars";

// Grafico a barre: bordo delle barre larghe, profilo delle cime per quelle fitte, soglie leggibili. Markup statico.
const series = (n: number, values: (i: number) => number | null = (i) => (i % 7) / 7): BarSeries => ({
  items: Array.from({ length: n }, (_, i) => ({ key: String(i), label: `R${i + 1}`, tick: String(i + 1), value: values(i), color: "#FFDB13" })),
  label: "pLDDT",
  digits: 0,
  references: [{ value: 0.7, label: "predizione sicura", color: "var(--ok)" }],
});
const render = (s: BarSeries) => renderToStaticMarkup(<ValueBars title="Confidenza" filename="c" series={s} data={{ columns: [], rows: [] }} />);

describe("barre per residuo", () => {
  it("poche barre: ognuna con il bordo, niente profilo delle cime", () => {
    const html = render(series(20));
    expect(html).toMatch(/fill="#FFDB13" stroke="var\(--chart-axis\)" stroke-width="0.6"/);
    expect(html).not.toContain('fill="none" stroke="var(--chart-axis)" stroke-width="0.75"');
  });

  it("barre fitte (< 3 px): senza bordo, una linea segue le cime e si interrompe dove manca il valore", () => {
    const html = render(series(400, (i) => (i === 200 ? null : 0.5)));
    expect(html).not.toMatch(/fill="#FFDB13" stroke=/);
    const tops = /<path d="([^"]+)" fill="none" stroke="var\(--chart-axis\)" stroke-width="0.75"/.exec(html);
    expect(tops).not.toBeNull();
    expect(tops![1].match(/M/g)).toHaveLength(2); // prima e dopo il residuo senza valore
  });

  it("soglia dopo le barre, con l'etichetta ad alone", () => {
    const html = render(series(20));
    const label = html.indexOf('class="ref-label"');
    expect(label).toBeGreaterThan(html.lastIndexOf('fill="#FFDB13"'));
    expect(html).toContain('style="fill:var(--ok)">predizione sicura</text>');
  });

  it("titolo solo per i lettori di schermo e cornice piatta, se chiesti", () => {
    const html = renderToStaticMarkup(
      <ValueBars title="Catene A–B" filename="c" series={series(5)} data={{ columns: [], rows: [] }} titleHidden className="flat" />,
    );
    expect(html).toContain('class="chart-frame flat"');
    expect(html).toContain('class="sr-only">Catene A–B<');
    expect(html).not.toContain('class="chart-frame-title"');
  });
});
