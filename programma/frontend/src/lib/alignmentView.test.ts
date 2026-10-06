import { describe, expect, it } from "vitest";
import css from "../styles.css?raw";
import { textOn } from "../components/charts/linearHelpers";
import { IDENTITY_SCALE } from "./alignConfidence";
import { CLASS_TINT, CONFIDENCE_TINT, identicalColumns, isGap, residueNumberAt, rulerText } from "./alignmentView";
import { RESIDUE_CLASSES } from "./compareMetrics";
import { composite, contrastRatio, cssTokens, parseColor, type RGBA } from "./contrast";

describe("righello dell'allineamento", () => {
  it("numeri ogni 10 colonne, allineati a destra sulla colonna, punto ogni 5", () => {
    const r = rulerText(0, 30);
    expect(r).toHaveLength(30);
    expect(r).toBe("    ·   10    ·   20    ·   30");
    expect(r.indexOf("10") + 1).toBe(9); // "0" sulla colonna 10 (indice 9)
  });
  it("blocchi successivi e numeri lunghi", () => {
    expect(rulerText(60, 80)).toBe("    ·   70    ·   80");
    expect(rulerText(995, 1005)).toBe(" 1000    ·");
    expect(rulerText(0, 4)).toBe("    ");
    expect(rulerText(10, 10)).toBe("");
  });
  it("un numero che non entra nel blocco diventa una tacca", () => {
    // colonne 99–101: il "100" dovrebbe iniziare prima del blocco
    expect(rulerText(98, 101)).toBe(" | ");
  });
});

describe("posizioni e colonne", () => {
  it("numero del residuo nella sua sequenza, gap esclusi", () => {
    expect(residueNumberAt("MK-.T", 0)).toBe(1);
    expect(residueNumberAt("MK-.T", 2)).toBeNull();
    expect(residueNumberAt("MK-.T", 4)).toBe(3);
    expect(residueNumberAt("MK", 5)).toBeNull();
    expect(isGap("-") && isGap(".") && !isGap("A")).toBe(true);
  });
  it("colonne identiche in tutte le sequenze (non i gap)", () => {
    expect(identicalColumns(["MK-A", "MR-A", "MK-A"])).toEqual([true, false, false, true]);
    expect(identicalColumns([])).toEqual([]);
  });
});

// Leggibilità delle lettere sui fondi colorati, nei due temi (WCAG AA, 4,5:1)
const dark = cssTokens(css, ":root");
const light = { ...dark, ...cssTokens(css, ':root[data-theme="light"]') };
const token = (tokens: Record<string, string>, name: string): RGBA => parseColor(tokens[name] ?? "") ?? [0, 0, 0, 0];
const tinted = (hex: string, tint: string): RGBA => {
  const [r, g, b] = parseColor(hex)!;
  return [r, g, b, parseInt(tint, 16) / 255];
};

describe("contrasto delle celle colorate", () => {
  it("colore pieno (colonne identiche): testo nero o bianco ≥ 4,5:1", () => {
    for (const c of RESIDUE_CLASSES) {
      expect(contrastRatio(parseColor(textOn(c.color))!, parseColor(c.color)!), c.label).toBeGreaterThanOrEqual(4.5);
    }
  });
  for (const [name, tokens] of [["scuro", dark], ["chiaro", light]] as const) {
    it(`fondi tenui con il testo del tema ${name} ≥ 4,5:1`, () => {
      const bg = token(tokens, "bg-2");
      const text = token(tokens, "text");
      // il fondo tenue si compone sullo sfondo dell'allineamento, poi il testo sopra
      const over = (fill: RGBA) => composite(fill, bg);
      for (const c of RESIDUE_CLASSES) {
        expect(contrastRatio(text, over(tinted(c.color, CLASS_TINT))), c.label).toBeGreaterThanOrEqual(4.5);
      }
      for (const s of IDENTITY_SCALE.stops) {
        expect(contrastRatio(text, over(tinted(s.color, CONFIDENCE_TINT))), s.color).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});
