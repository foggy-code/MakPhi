import { describe, expect, it } from "vitest";
import base from "../styles.css?raw";
import { clustalColor } from "./aminoAcids";
import { contrastRatio, cssTokens, parseColor, type RGBA } from "./contrast";
import { AA_CLASSES, compositionParts } from "./workflowView";

// Colori che portano dati nelle pagine delle sequenze (styles/sequences.css): classi di residui della composizione e
// tracce delle annotazioni UniProt, ≥ 3:1 sui pannelli in entrambi i temi (WCAG 1.4.11, contrasto non testuale).
// vitest elabora solo styles.css (css.include in vite.config.ts: gli altri fogli arrivano vuoti, anche con `?raw`):
// questo si legge dal disco con il modulo fs di Node (i test girano in Node, senza i tipi di Node nel progetto)
type Fs = { readFileSync: (path: URL, encoding: "utf8") => string };
const fs = (globalThis as unknown as { process: { getBuiltinModule: (id: string) => Fs } }).process.getBuiltinModule("node:fs");
const css = fs.readFileSync(new URL("../styles/sequences.css", import.meta.url), "utf8");
const panelsDark = cssTokens(base, ":root");
const panelsLight = { ...panelsDark, ...cssTokens(base, ':root[data-theme="light"]') };
const dark = cssTokens(css, ":root");
const light = { ...dark, ...cssTokens(css, ':root[data-theme="light"]') };
const PANELS = ["bg", "bg-2", "panel", "panel-2", "panel-3"];
const DATA = ["aa-hydrophobic", "aa-positive", "aa-negative", "aa-polar", "aa-special", "trk-chain", "trk-region", "trk-site", "trk-disulfide", "trk-mod", "trk-pdb"];

function color(tokens: Record<string, string>, name: string): RGBA {
  const c = parseColor(tokens[name] ?? "");
  if (!c) throw new Error(`token --${name} assente o non è un colore: ${tokens[name]}`);
  return c;
}

for (const [name, tokens, panels] of [["scuro", dark, panelsDark], ["chiaro", light, panelsLight]] as const) {
  describe(`tema ${name}`, () => {
    it("classi di residui e tracce UniProt ≥ 3:1 su ogni pannello", () => {
      for (const fg of DATA) {
        for (const bg of PANELS) {
          expect(contrastRatio(color(tokens, fg), color(panels, bg)), `--${fg} su --${bg}`).toBeGreaterThanOrEqual(3);
        }
      }
    });
  });
}

describe("composizione amminoacidica", () => {
  it("nel tema scuro i colori Clustal X dell'allineamento (residuo rappresentativo della classe)", () => {
    for (const c of AA_CLASSES) {
      const token = /^var\(--([\w-]+)\)$/.exec(c.color)?.[1];
      expect(token, c.label).toBeTruthy();
      expect(dark[token!]?.toLowerCase(), c.label).toBe(clustalColor(c.clustal));
    }
  });
  it("la barra usa i token per classe", () => {
    expect(compositionParts({ A: 1 }, 1).map((p) => p.color)).toEqual(AA_CLASSES.map((c) => c.color));
  });
});
