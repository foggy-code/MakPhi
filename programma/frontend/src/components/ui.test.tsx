import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StackBar } from "./charts";
import { BarCell } from "./ui";

// Percentuali delle tabelle (identità, copertura) e delle barre di composizione: virgola decimale come il resto
// dell'interfaccia (fmtNum), non il punto di toFixed.
describe("numeri all'italiana nei componenti", () => {
  it("BarCell", () => {
    expect(renderToStaticMarkup(<BarCell value={96.06} />)).toContain(">96,1%<");
    expect(renderToStaticMarkup(<BarCell value={100} />)).toContain(">100,0%<");
    expect(renderToStaticMarkup(<BarCell value={null} />)).toContain(">—<");
  });
  it("StackBar: legenda e titolo", () => {
    const html = renderToStaticMarkup(<StackBar parts={[{ label: "Elica", value: 23.68, color: "red" }, { label: "Coil", value: 76.32, color: "gray" }]} />);
    expect(html).toContain('title="Elica: 23,7%"');
    expect(html).toContain("Elica 23,7%</span>");
    expect(html).toContain("Coil 76,3%</span>");
    expect(html).toContain("width:23.68%"); // le misure CSS restano col punto
  });
});
