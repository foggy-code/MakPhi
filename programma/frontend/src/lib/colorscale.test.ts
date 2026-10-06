import { describe, expect, it } from "vitest";
import { colorFor, hexToInt, interpolateStops, legendFor, mixColors, NO_VALUE_COLOR, uniformStops, type BinnedScale, type CategoricalScale, type ContinuousScale } from "./colorscale";

describe("colori", () => {
  it("mescola in RGB e converte per 3Dmol", () => {
    expect(mixColors("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(mixColors("#ff0000", "#0000ff", 0)).toBe("#ff0000");
    expect(mixColors("#ff0000", "#0000ff", 2)).toBe("#0000ff");
    expect(mixColors("#f00", "#00f", 1)).toBe("#0000ff");
    expect(hexToInt("#0053D6")).toBe(0x0053d6);
  });
});

describe("scala continua", () => {
  const stops = uniformStops(0, 10, ["#000000", "#ffffff"]);
  it("interpola e satura agli estremi", () => {
    expect(stops).toEqual([{ value: 0, color: "#000000" }, { value: 10, color: "#ffffff" }]);
    expect(interpolateStops(stops, 5)).toBe("#808080");
    expect(interpolateStops(stops, -3)).toBe("#000000");
    expect(interpolateStops(stops, 99)).toBe("#ffffff");
  });
  it("due punti con lo stesso valore fanno un salto netto: la soglia appartiene al tratto superiore", () => {
    const step = [
      { value: 0, color: "#000000" }, { value: 25, color: "#ff0000" },
      { value: 25, color: "#00ff00" }, { value: 100, color: "#0000ff" },
    ];
    expect(interpolateStops(step, 24.999)).toBe("#ff0000");
    expect(interpolateStops(step, 25)).toBe("#00ff00");
    expect(interpolateStops(step, 100)).toBe("#0000ff");
  });
  it("tavolozza con un solo valore o min = max", () => {
    const flat = uniformStops(3, 3, ["#123456", "#abcdef"]);
    expect(interpolateStops(flat, 3)).toBe("#123456");
    expect(interpolateStops(flat, 9)).toBe("#123456");
  });
  it("legenda a gradiente con tacche in posizione", () => {
    const scale: ContinuousScale = { kind: "continuous", stops, ticks: [{ value: 0 }, { value: 2.5, label: "soglia" }, { value: 10 }], digits: 1, unit: "Å²" };
    const legend = legendFor(scale, "B-factor", true);
    expect(legend.kind).toBe("gradient");
    if (legend.kind !== "gradient") return;
    expect(legend.css).toBe("linear-gradient(to right, #000000 0.0%, #ffffff 100.0%)");
    expect(legend.ticks).toEqual([
      { position: 0, label: "0,0 Å²" }, { position: 0.25, label: "soglia" }, { position: 1, label: "10,0 Å²" },
    ]);
    expect(legend.noValue).toBe(true);
  });
});

describe("scale a fasce e categoriali", () => {
  const bins: BinnedScale = { kind: "binned", bins: [{ gt: 90, color: "#0053D6", label: "a" }, { gt: 70, color: "#65CBF3", label: "b" }, { color: "#FF7D45", label: "c" }] };
  it("prima fascia con valore oltre la soglia", () => {
    expect(colorFor(bins, 95)).toBe("#0053D6");
    expect(colorFor(bins, 90)).toBe("#65CBF3");
    expect(colorFor(bins, 12)).toBe("#FF7D45");
  });
  it("limite compreso con gte", () => {
    const inclusive: BinnedScale = { kind: "binned", bins: [{ gte: 90, color: "#0053D6", label: "a" }, { gte: 70, color: "#65CBF3", label: "b" }, { color: "#FF7D45", label: "c" }] };
    expect(colorFor(inclusive, 90)).toBe("#0053D6");
    expect(colorFor(inclusive, 89.99)).toBe("#65CBF3");
    expect(colorFor(inclusive, 70)).toBe("#65CBF3");
    expect(colorFor(inclusive, 69.99)).toBe("#FF7D45");
  });
  it("valori mancanti o sconosciuti in grigio", () => {
    const cat: CategoricalScale = { kind: "categorical", categories: [{ key: "H", color: "#f472b6", label: "Elica" }] };
    expect(colorFor(cat, "H")).toBe("#f472b6");
    expect(colorFor(cat, "X")).toBe(NO_VALUE_COLOR);
    expect(colorFor(cat, null)).toBe(NO_VALUE_COLOR);
    expect(colorFor(bins, undefined)).toBe(NO_VALUE_COLOR);
    expect(colorFor(bins, Number.NaN)).toBe(NO_VALUE_COLOR);
    expect(colorFor(bins, "testo")).toBe(NO_VALUE_COLOR);
  });
  it("legenda a campioni", () => {
    const legend = legendFor(bins, "pLDDT");
    expect(legend).toEqual({ kind: "swatches", title: "pLDDT", noValue: false, items: [{ color: "#0053D6", label: "a" }, { color: "#65CBF3", label: "b" }, { color: "#FF7D45", label: "c" }] });
  });
});
