import { describe, expect, it } from "vitest";
import css from "../styles.css?raw";
import { contrastRatio, cssTokens, parseColor, type RGBA } from "./contrast";

// Elementi grafici che portano dati (segnalazioni, tracce) e anello del focus: ≥ 3:1 sui pannelli in entrambi i temi
// (WCAG 1.4.11, contrasto non testuale).
const dark = cssTokens(css, ":root");
const light = { ...dark, ...cssTokens(css, ':root[data-theme="light"]') };
const PANELS = ["bg", "panel", "panel-2", "panel-3"];
const DATA = ["sig-rama", "sig-clash", "sig-dope", "sig-qmean", "sig-prosa", "sig-disorder", "sig-predictor", "trk-disorder", "trk-lowc", "trk-domain"];

function color(tokens: Record<string, string>, name: string): RGBA {
  const c = parseColor(tokens[name] ?? "");
  if (!c) throw new Error(`token --${name} assente o non è un colore: ${tokens[name]}`);
  return c;
}

for (const [name, tokens] of [["scuro", dark], ["chiaro", light]] as const) {
  describe(`tema ${name}`, () => {
    it("colori delle segnalazioni e delle tracce ≥ 3:1 su ogni pannello", () => {
      for (const fg of DATA) {
        for (const bg of PANELS) {
          expect(contrastRatio(color(tokens, fg), color(tokens, bg)), `--${fg} su --${bg}`).toBeGreaterThanOrEqual(3);
        }
      }
    });
    it("anello del focus (--accent) ≥ 3:1 su ogni superficie", () => {
      for (const bg of [...PANELS, "bg-2"]) {
        expect(contrastRatio(color(tokens, "accent"), color(tokens, bg)), `--accent su --${bg}`).toBeGreaterThanOrEqual(3);
      }
    });
  });
}

describe("anello del focus nel visualizzatore 3D (fondo scuro in entrambi i temi)", () => {
  it("#3ddcf3 su #0b1120 ≥ 3:1, e la regola c'è", () => {
    expect(contrastRatio(parseColor("#3ddcf3")!, parseColor("#0b1120")!)).toBeGreaterThanOrEqual(3);
    expect(css).toMatch(/\.viewer3d \.overlay \.btn:focus-visible[^{]*\{[^}]*outline-color: #3ddcf3/);
  });
  it("i pulsanti hanno un anello pieno, non solo l'alone", () => {
    expect(css).toMatch(/\.btn:focus-visible \{ outline: 2px solid var\(--accent\)/);
  });
});
