import { describe, expect, it } from "vitest";
import css from "../styles.css?raw";
import type { PocketsResult } from "../api/types";
import { colorFor, CONTEXT_COLOR, drawColor, LIGHT_THEME_COLORS, NO_VALUE_COLOR, ONE_METHOD_COLOR, PALETTES, themedColor, uniformStops } from "./colorscale";
import { comparisonColoring, DISTANCE_SCALE, REFERENCE_COLOR } from "./compare";
import { composite, contrastRatio, cssTokens, luminance, parseColor, type RGBA } from "./contrast";
import { CONSERVATION_SCALE, INTERFACE_SCALE } from "./interactions";
import { OUTSIDE_COLOR as NOT_SCANNED } from "./mutations";
import { CENTER_COLOR, OUTSIDE_COLOR as OUTSIDE_POCKETS, pocketSpheres } from "./pockets";
import { CLASH_SCALE, CONSENSUS_SCALE } from "./residueColoring";
import { VIEWER_BACKGROUND, VIEWER_VIEW_STYLE, type Theme } from "./theme";

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

describe("visualizzatore 3D: fondo e comandi del tema (--viewer-*)", () => {
  for (const [name, tokens] of [["scuro", dark], ["chiaro", light]] as const) {
    it(`tema ${name}: anello del focus ≥ 3:1 sul fondo, testi ≥ 4,5:1 sul fondo e sui comandi`, () => {
      const bg = color(tokens, "viewer-bg");
      const chip = composite(color(tokens, "viewer-chip"), bg);
      expect(contrastRatio(color(tokens, "viewer-accent"), bg), "--viewer-accent su --viewer-bg").toBeGreaterThanOrEqual(3);
      for (const fg of ["viewer-muted", "viewer-err"]) {
        expect(contrastRatio(color(tokens, fg), bg), `--${fg} su --viewer-bg`).toBeGreaterThanOrEqual(4.5);
      }
      for (const fg of ["viewer-text", "viewer-muted", "viewer-accent"]) {
        expect(contrastRatio(color(tokens, fg), chip), `--${fg} sui comandi (--viewer-chip)`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
  it("le regole usano i token (niente colori fissi del tema scuro), il codice ha lo stesso fondo", () => {
    expect(css).toMatch(/\.viewer3d \.overlay \.btn:focus-visible[^{]*\{[^}]*outline-color: var\(--viewer-accent\)/);
    expect(css).toMatch(/\.viewer3d \{[^}]*background: var\(--viewer-bg\)/);
    expect(dark["viewer-bg"]).toBe(VIEWER_BACKGROUND.dark);
    expect(light["viewer-bg"]).toBe(VIEWER_BACKGROUND.light);
  });
  it("i pulsanti hanno un anello pieno, non solo l'alone", () => {
    expect(css).toMatch(/\.btn:focus-visible \{ outline: 2px solid var\(--accent\)/);
  });
});

// Grigio di contesto dei residui (la maggior parte della catena nelle viste consenso, clash, interfaccia, tasche e
// scansione di alanina): visibile sul fondo del 3D, sotto la legenda del 3D e sui pannelli delle strisce in entrambi i
// temi; nel chiaro il grigio chiaro del tema scuro sparirebbe (1,1:1 su #f2f4f8).
describe("visualizzatore 3D: grigio di contesto dei residui", () => {
  for (const [name, tokens, theme] of [["scuro", dark, "dark"], ["chiaro", light, "light"]] as [string, Record<string, string>, Theme][]) {
    it(`tema ${name}: ≥ 3:1 sul fondo del 3D, sotto la legenda (--viewer-chip) e sui pannelli`, () => {
      const fg = parseColor(themedColor(CONTEXT_COLOR, theme))!;
      const bg = color(tokens, "viewer-bg");
      expect(contrastRatio(fg, bg), "su --viewer-bg").toBeGreaterThanOrEqual(3);
      expect(contrastRatio(fg, parseColor(VIEWER_BACKGROUND[theme])!), "su VIEWER_BACKGROUND").toBeGreaterThanOrEqual(3);
      expect(contrastRatio(fg, composite(color(tokens, "viewer-chip"), bg)), "su --viewer-chip").toBeGreaterThanOrEqual(3);
      for (const panel of PANELS) expect(contrastRatio(fg, color(tokens, panel)), `su --${panel}`).toBeGreaterThanOrEqual(3);
    });
  }
  it("le colorazioni usano il grigio di contesto, che solo nel tema chiaro cambia", () => {
    expect([colorFor(CONSENSUS_SCALE, 0), colorFor(CLASH_SCALE, 0), colorFor(INTERFACE_SCALE, 0), OUTSIDE_POCKETS, NOT_SCANNED])
      .toEqual(Array(5).fill(CONTEXT_COLOR));
    expect(themedColor(CONTEXT_COLOR, "dark")).toBe(CONTEXT_COLOR);
    expect(themedColor(CONTEXT_COLOR, "light")).not.toBe(CONTEXT_COLOR);
    expect(themedColor("#dc2626", "light")).toBe("#dc2626");
  });
});

// Simulazione della visione dicromatica (Machado, Oliveira & Fernandes 2009, gravità 1, in RGB lineare) e differenza di
// colore CIE76 in CIELAB: per controllare che due categorie restino distinte anche per chi non distingue rosso e verde.
const CVD: Record<string, number[][] | null> = {
  normale: null,
  deuteranopia: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
  protanopia: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
};
const toLinear = (c: number) => (c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4);
function lab(hex: string, matrix: number[][] | null): [number, number, number] {
  const rgb = parseColor(hex)!.slice(0, 3).map(toLinear);
  const [r, g, b] = matrix ? matrix.map((row) => Math.min(1, Math.max(0, row[0] * rgb[0] + row[1] * rgb[1] + row[2] * rgb[2]))) : rgb;
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : ((24389 / 27) * t + 16) / 116);
  const [x, y, z] = [(0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b, (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883].map(f);
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
}
function deltaE(a: string, b: string, matrix: number[][] | null): number {
  const [p, q] = [lab(a, matrix), lab(b, matrix)];
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
}

// Altri colori fissi pensati per il fondo scuro del 3D che sul fondo chiaro sparirebbero (struttura di riferimento del
// confronto, «1 metodo» del consenso, centri delle tasche): una variante per il tema chiaro, ≥ 3:1 come il grigio di
// contesto, senza cambiare nulla nel tema scuro.
describe("visualizzatore 3D: colori fissi per tema (LIGHT_THEME_COLORS)", () => {
  const base = [...LIGHT_THEME_COLORS.keys()];
  it("la tabella copre il grigio di contesto, il riferimento A, «1 metodo» e il centro delle tasche", () => {
    expect(base).toEqual(expect.arrayContaining([CONTEXT_COLOR, REFERENCE_COLOR, ONE_METHOD_COLOR, CENTER_COLOR]));
    expect(colorFor(CONSENSUS_SCALE, 1)).toBe(ONE_METHOD_COLOR);
    expect(pocketSpheres({ pockets: [{ id: 1, rank: 1, center: [0, 0, 0], spheres: [], residues: [] }] } as unknown as PocketsResult)[0].color).toBe(CENTER_COLOR);
  });
  for (const [name, tokens, theme] of [["scuro", dark, "dark"], ["chiaro", light, "light"]] as [string, Record<string, string>, Theme][]) {
    it(`tema ${name}: ogni colore disegnato è ≥ 3:1 sul fondo del 3D, sotto la legenda e sui pannelli`, () => {
      const bg = color(tokens, "viewer-bg");
      for (const c of base) {
        const fg = parseColor(themedColor(c, theme))!;
        expect(contrastRatio(fg, bg), `${c} → ${themedColor(c, theme)} su --viewer-bg`).toBeGreaterThanOrEqual(3);
        expect(contrastRatio(fg, parseColor(VIEWER_BACKGROUND[theme])!), `${c} su VIEWER_BACKGROUND`).toBeGreaterThanOrEqual(3);
        expect(contrastRatio(fg, composite(color(tokens, "viewer-chip"), bg)), `${c} su --viewer-chip`).toBeGreaterThanOrEqual(3);
        for (const panel of PANELS) expect(contrastRatio(fg, color(tokens, panel)), `${c} su --${panel}`).toBeGreaterThanOrEqual(3);
      }
    });
  }
  it("il tema scuro disegna i colori di sempre", () => {
    for (const c of base) expect(themedColor(c, "dark")).toBe(c);
    expect(comparisonColoring([]).chainColors).toEqual({ A: "#cbd5e1", B: NO_VALUE_COLOR });
    expect(colorFor(CONSENSUS_SCALE, 1)).toBe("#fde047");
    expect(CENTER_COLOR).toBe("#ffffff");
  });

  it("riferimento A e «B senza coppia»: due grigi lontani per luminosità (≥ 2,5:1), distinti anche con deuteranopia e protanopia", () => {
    for (const theme of ["dark", "light"] as Theme[]) {
      const a = themedColor(REFERENCE_COLOR, theme);
      const b = themedColor(NO_VALUE_COLOR, theme);
      expect(b).toBe(NO_VALUE_COLOR);
      expect(contrastRatio(parseColor(a)!, parseColor(b)!), `${theme}: ${a} / ${b}`).toBeGreaterThanOrEqual(2.5);
      for (const [vision, m] of Object.entries(CVD)) expect(deltaE(a, b, m), `${theme}, ${vision}`).toBeGreaterThanOrEqual(25);
    }
  });
  it("riferimento A nel tema chiaro: distinto anche da B lungo tutta la scala delle distanze (un viola, con la deuteranopia, sembrerebbe il blu delle coppie coincidenti)", () => {
    const a = themedColor(REFERENCE_COLOR, "light");
    for (let d = 0; d <= 5; d += 0.25) {
      for (const [vision, m] of Object.entries(CVD)) expect(deltaE(a, colorFor(DISTANCE_SCALE, d), m), `${d} Å, ${vision}`).toBeGreaterThanOrEqual(25);
    }
  });
  it("«1 metodo» nel tema chiaro resta distinto da «nessuna segnalazione», «2 metodi» e «3 o più metodi», anche con deuteranopia e protanopia", () => {
    const one = themedColor(ONE_METHOD_COLOR, "light");
    // il limite: con la deuteranopia un giallo scuro diventa quasi il rosso di «3 o più» (ocra #a16207: 3,8; oro #a68a00: 10,4)
    for (const v of [0, 2, 3]) {
      const other = themedColor(colorFor(CONSENSUS_SCALE, v), "light");
      for (const [vision, m] of Object.entries(CVD)) expect(deltaE(one, other, m), `${v} metodi, ${vision}`).toBeGreaterThanOrEqual(12);
    }
  });

  it("i gradienti delle scale continue restano quelli della scala anche dove passano per un colore della tabella", () => {
    // conservazione 0,5 = bianco, come il centro delle tasche: non va scurito
    expect(colorFor(CONSERVATION_SCALE, 0.5)).toBe(CENTER_COLOR);
    expect(drawColor(CONSERVATION_SCALE, 0.5, "light")).toBe(CENTER_COLOR);
    // fasce, categorie e colori fissi prendono invece la variante
    expect(drawColor(CONSENSUS_SCALE, 1, "light")).toBe(themedColor(ONE_METHOD_COLOR, "light"));
    expect(drawColor(DISTANCE_SCALE, 1, "light", REFERENCE_COLOR)).toBe(themedColor(REFERENCE_COLOR, "light"));
    expect(drawColor(CONSENSUS_SCALE, 1, "dark")).toBe(ONE_METHOD_COLOR);
  });

  // Centro bianco di blu-bianco-rosso (B-factor, DOPE, potenziale), giallo di viridis (RSA), giallo della scala delle
  // distanze: colori chiari voluti (il centro neutro, il massimo), che restano; nel tema chiaro li stacca dal fondo il
  // contorno scuro della vista 3D.
  it("scale continue: i colori chiari restano (sotto 3:1 sul fondo chiaro), con il contorno scuro solo nel tema chiaro", () => {
    const bg = color(light, "viewer-bg");
    const lightest = (stops: { color: string }[]) => stops.map((s) => s.color).sort((p, q) => luminance(parseColor(q)!) - luminance(parseColor(p)!))[0];
    for (const stops of [uniformStops(0, 1, PALETTES.blueWhiteRed), uniformStops(0, 1, PALETTES.viridis), DISTANCE_SCALE.stops]) {
      expect(contrastRatio(parseColor(lightest(stops))!, bg)).toBeLessThan(3);
    }
    expect(VIEWER_VIEW_STYLE.dark.style).not.toContain("outline");
    expect(VIEWER_VIEW_STYLE.light.style).toBe("outline");
    expect(contrastRatio(parseColor(VIEWER_VIEW_STYLE.light.color!)!, bg)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(parseColor(VIEWER_VIEW_STYLE.light.color!)!, parseColor(VIEWER_BACKGROUND.light)!)).toBeGreaterThanOrEqual(3);
    // e nella legenda del 3D (fondo bianco) campioni e barra hanno un bordo ≥ 3:1, solo nel tema chiaro
    expect(css).toMatch(/:root\[data-theme="light"\] \.viewer3d \.legend3d \.sw i, :root\[data-theme="light"\] \.viewer3d \.legend3d \.bar \{ box-shadow: inset 0 0 0 1px var\(--chart-axis\); \}/);
    expect(contrastRatio(color(light, "chart-axis"), composite(color(light, "viewer-chip"), bg))).toBeGreaterThanOrEqual(3);
  });
});
