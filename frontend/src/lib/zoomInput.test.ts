import { describe, expect, it } from "vitest";
import { clampDomain, spanAround, wheelFactor } from "./zoom";

// Rotella e selezioni strette: zoom proporzionale (touchpad, pinch) e intervalli centrati.
describe("rotella", () => {
  it("nessuna componente verticale: l'evento resta alla pagina", () => {
    expect(wheelFactor({ deltaY: 0, deltaMode: 0 })).toBeNull();
    expect(wheelFactor({ deltaY: -0, deltaMode: 1 })).toBeNull();
  });
  it("proporzionale a deltaY: un touchpad fa passi piccoli, una tacca del mouse circa ×0,82", () => {
    const small = wheelFactor({ deltaY: -2, deltaMode: 0 })!;
    expect(small).toBeLessThan(1);
    expect(small).toBeGreaterThan(0.99);
    expect(wheelFactor({ deltaY: -100, deltaMode: 0 })!).toBeCloseTo(0.82, 2);
    expect(wheelFactor({ deltaY: 100, deltaMode: 0 })!).toBeCloseTo(1.22, 2);
    // venti piccoli eventi non portano subito all'ampiezza minima (prima: 0,8^20 ≈ 0,01)
    expect(wheelFactor({ deltaY: -5, deltaMode: 0 })! ** 20).toBeGreaterThan(0.8);
  });
  it("righe e pagine in pixel, fattore limitato", () => {
    expect(wheelFactor({ deltaY: 3, deltaMode: 1 })).toBeCloseTo(wheelFactor({ deltaY: 48, deltaMode: 0 })!, 10);
    expect(wheelFactor({ deltaY: 1, deltaMode: 2 })).toBeCloseTo(Math.exp(0.4), 10);
    expect(wheelFactor({ deltaY: 5000, deltaMode: 0 })).toBeCloseTo(Math.exp(0.4), 10);
    expect(wheelFactor({ deltaY: -5000, deltaMode: 0 })).toBeCloseTo(Math.exp(-0.4), 10);
  });
});

describe("intervalli centrati", () => {
  it("spanAround allarga attorno al centro", () => {
    expect(spanAround(50, 50, 10)).toEqual({ lo: 45, hi: 55 });
    expect(spanAround(40, 42, 10)).toEqual({ lo: 36, hi: 46 });
    expect(spanAround(10, 30, 10)).toEqual({ lo: 10, hi: 30 });
    expect(spanAround(30, 10, 10)).toEqual({ lo: 10, hi: 30 });
  });
  it("vicino al bordo lo spostamento lo riporta dentro (come fa useDomain.set)", () => {
    expect(clampDomain(spanAround(1, 1, 10), 0, 100, 10)).toEqual({ lo: 0, hi: 10 });
    expect(clampDomain(spanAround(47.5, 52.5, 10), 0, 100, 10)).toEqual({ lo: 45, hi: 55 });
  });
});
