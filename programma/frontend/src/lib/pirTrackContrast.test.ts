import { describe, expect, it } from "vitest";
import css from "../styles.css?raw";
import pirCss from "../styles/pir.css?raw";
import { contrastRatio, cssTokens, parseColor } from "./contrast";

// Tracce della struttura secondaria nell'Editor PIR (styles/pir.css): grafica non testuale, almeno 3:1 sullo sfondo
// della griglia (--panel-2) nei due temi (WCAG 1.4.11). Le tracce usano i colori --trk-* definiti in pir.css.
const LIGHT = ':root[data-theme="light"]';
const themes = [
  { name: "scuro", tokens: { ...cssTokens(css, ":root"), ...cssTokens(pirCss, ".pirg") } },
  {
    name: "chiaro",
    tokens: { ...cssTokens(css, ":root"), ...cssTokens(css, LIGHT), ...cssTokens(pirCss, ".pirg"), ...cssTokens(pirCss, `${LIGHT} .pirg`) },
  },
];

/** Valore di una variabile, seguendo i `var(--altra)`. */
const resolve = (name: string, tokens: Record<string, string>): string => {
  const value = tokens[name] ?? "";
  const m = /^var\(--([\w-]+)\)$/.exec(value.trim());
  return m ? resolve(m[1], tokens) : value;
};

describe("contrasto delle tracce della struttura secondaria", () => {
  for (const t of themes) {
    it(`tema ${t.name}: elica, filamento e coil almeno 3:1 sullo sfondo della griglia`, () => {
      const bg = parseColor(resolve("panel-2", t.tokens))!;
      expect(bg).not.toBeNull();
      for (const name of ["trk-helix", "trk-strand", "trk-coil"]) {
        const fg = parseColor(resolve(name, t.tokens));
        expect(fg, name).not.toBeNull();
        expect(contrastRatio(fg!, bg), `${name} (${t.name})`).toBeGreaterThanOrEqual(3);
      }
    });
  }
});
