import { describe, expect, it } from "vitest";
import { ANGLE_STEPS, clampDomain, isFullDomain, niceTicks, panDomain, visibleIndices, zoomDomain } from "./zoom";

describe("intervalli ingrandibili", () => {
  it("clampDomain: limiti, ampiezza minima, estremi invertiti", () => {
    expect(clampDomain({ lo: -5, hi: 20 }, 0, 100)).toEqual({ lo: 0, hi: 25 });
    expect(clampDomain({ lo: 90, hi: 120 }, 0, 100)).toEqual({ lo: 70, hi: 100 });
    expect(clampDomain({ lo: 50, hi: 51 }, 0, 100, 10)).toEqual({ lo: 50, hi: 60 });
    expect(clampDomain({ lo: 30, hi: 10 }, 0, 100)).toEqual({ lo: 10, hi: 30 });
    expect(clampDomain({ lo: 0, hi: 500 }, 0, 100)).toEqual({ lo: 0, hi: 100 });
    expect(clampDomain({ lo: 0, hi: 1 }, 0, 4, 10)).toEqual({ lo: 0, hi: 4 }); // dati più corti dell'ampiezza minima
  });
  it("zoomDomain: attorno al centro o a un punto, entro i limiti", () => {
    expect(zoomDomain({ lo: 0, hi: 100 }, 0.5, 0, 100)).toEqual({ lo: 25, hi: 75 });
    expect(zoomDomain({ lo: 0, hi: 100 }, 0.5, 0, 100, 0, 0)).toEqual({ lo: 0, hi: 50 });
    expect(zoomDomain({ lo: 25, hi: 75 }, 2, 0, 100)).toEqual({ lo: 0, hi: 100 });
    expect(zoomDomain({ lo: 40, hi: 60 }, 0.1, 0, 100, 5)).toEqual({ lo: 47.5, hi: 52.5 });
  });
  it("panDomain e isFullDomain", () => {
    expect(panDomain({ lo: 10, hi: 30 }, 50, 0, 100)).toEqual({ lo: 60, hi: 80 });
    expect(panDomain({ lo: 10, hi: 30 }, -50, 0, 100)).toEqual({ lo: 0, hi: 20 });
    expect(panDomain({ lo: 10, hi: 30 }, 500, 0, 100)).toEqual({ lo: 80, hi: 100 });
    expect(isFullDomain({ lo: 0, hi: 100 }, 0, 100)).toBe(true);
    expect(isFullDomain({ lo: 0, hi: 99 }, 0, 100)).toBe(false);
  });
  it("niceTicks: valori tondi dentro l'intervallo", () => {
    expect(niceTicks(-180, 180, 6, ANGLE_STEPS)).toEqual([-180, -120, -60, 0, 60, 120, 180]);
    expect(niceTicks(-80, -20, 4, ANGLE_STEPS)).toEqual([-75, -60, -45, -30]);
    expect(niceTicks(0, 300, 6)).toEqual([0, 50, 100, 150, 200, 250, 300]);
    expect(niceTicks(0, 1, 5)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(12.3, 47.9, 4)).toEqual([20, 30, 40]);
    expect(niceTicks(5, 5)).toEqual([5]);
  });
  it("visibleIndices", () => {
    expect(visibleIndices({ lo: 2.4, hi: 7.6 }, 10)).toEqual([2, 8]);
    expect(visibleIndices({ lo: -1, hi: 30 }, 10)).toEqual([0, 9]);
  });
});
