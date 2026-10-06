import { describe, expect, it } from "vitest";
import css from "../styles.css?raw";
import { composite, contrastRatio, cssTokens, parseColor, type RGBA } from "./contrast";

// Leggibilità dei due temi: ogni testo su ogni superficie supera 4,5:1 (WCAG 2.1 AA per il testo normale).
const dark = cssTokens(css, ":root");
const light = { ...dark, ...cssTokens(css, ':root[data-theme="light"]') };
const SURFACES = ["bg", "bg-2", "panel", "panel-2", "panel-3"];
const TEXTS = ["text", "text-2", "muted", "accent", "ok", "info", "warn", "err", "chart-text"];
const TONES = ["ok", "info", "warn", "err", "accent"];

function color(tokens: Record<string, string>, name: string): RGBA {
  const c = parseColor(tokens[name] ?? "");
  if (!c) throw new Error(`token --${name} assente o non è un colore: ${tokens[name]}`);
  return c;
}

describe("calcolo del contrasto", () => {
  it("valori noti", () => {
    expect(contrastRatio([0, 0, 0, 1], [255, 255, 255, 1])).toBeCloseTo(21, 1);
    expect(contrastRatio([255, 255, 255, 1], [255, 255, 255, 1])).toBeCloseTo(1, 5);
    expect(parseColor("#fff")).toEqual([255, 255, 255, 1]);
    expect(parseColor("rgba(10, 20, 30, 0.5)")).toEqual([10, 20, 30, 0.5]);
    expect(parseColor("var(--x)")).toBeNull();
    expect(composite([255, 255, 255, 0.5], [0, 0, 0, 1])).toEqual([127.5, 127.5, 127.5, 1]);
  });
});

for (const [name, tokens] of [["scuro", dark], ["chiaro", light]] as const) {
  describe(`tema ${name}`, () => {
    it("testi su tutte le superfici ≥ 4,5:1", () => {
      for (const fg of TEXTS) {
        for (const bg of SURFACES) {
          const ratio = contrastRatio(color(tokens, fg), color(tokens, bg));
          expect(ratio, `--${fg} su --${bg}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });
    it("badge e avvisi: colore dello stato sul suo fondo tenue ≥ 4,5:1, testo ≥ 7:1", () => {
      for (const tone of TONES) {
        for (const surface of ["panel", "panel-2", "panel-3", "bg-2"]) {
          const soft = composite(color(tokens, `${tone}-soft`), color(tokens, surface));
          expect(contrastRatio(color(tokens, tone), soft), `--${tone} su --${tone}-soft (${surface})`).toBeGreaterThanOrEqual(4.5);
          expect(contrastRatio(color(tokens, "text"), soft), `--text su --${tone}-soft (${surface})`).toBeGreaterThanOrEqual(7);
        }
      }
    });
    it("voce attiva del menu e chip delle etichette (--accent-soft): testo ≥ 4,5:1", () => {
      for (const surface of ["bg-2", "panel", "panel-2"]) {
        const soft = composite(color(tokens, "accent-soft"), color(tokens, surface));
        for (const fg of ["text", "text-2", "accent"]) {
          expect(contrastRatio(color(tokens, fg), soft), `--${fg} su --accent-soft (${surface})`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });
    it("testo sui pulsanti primari ≥ 4,5:1", () => {
      expect(contrastRatio(color(tokens, "on-accent"), color(tokens, "accent"))).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(color(tokens, "on-accent"), color(tokens, "accent-strong"))).toBeGreaterThanOrEqual(4.5);
    });
    it("testo principale ad alto contrasto (≥ 12:1 sui pannelli)", () => {
      for (const bg of ["panel", "bg"]) expect(contrastRatio(color(tokens, "text"), color(tokens, bg))).toBeGreaterThanOrEqual(12);
      for (const bg of SURFACES) expect(contrastRatio(color(tokens, "text-2"), color(tokens, bg))).toBeGreaterThanOrEqual(7);
    });
  });
}
