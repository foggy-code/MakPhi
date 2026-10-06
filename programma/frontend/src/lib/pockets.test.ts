import { describe, expect, it } from "vitest";
import type { Pocket, PocketsResult } from "../api/types";
import { colorFor, legendFor } from "./colorscale";
import {
  CENTER_DRAW_RADIUS, druggabilityLabel, isDruggable, MAX_COLORED, OUTSIDE_COLOR, POCKET_COLORS, pocketColor, pocketColoring, pocketHighlight, pocketOfResidue,
  pocketSpheres, residueList, SPHERE_DRAW_RADIUS,
} from "./pockets";
import { coloringMap, describeResidue } from "./residueColoring";

const res = (resnum: string, resname: string, chain = "A") => ({ chain, resnum, resname });

function pocket(id: number, rank: number, druggability: number, residues: { chain: string; resnum: string; resname: string }[], spheres = 2): Pocket {
  return {
    id, rank, druggability, druggable: druggability >= 0.5, score: 0.3, volume: 300, alpha_spheres: spheres,
    center: [1, 2, 3], spheres: Array.from({ length: spheres }, (_v, k) => ({ center: [k, 0, 0] as [number, number, number], radius: 4 })),
    residues, atoms: residues.length * 3, other: {},
  };
}

// valori dell'uscita reale di fpocket su 1UBQ (test del backend): tasca 2 (0,535) e tasca 4 (0,498)
const RESULT: PocketsResult = {
  source: { type: "template", id: "1UBQ" }, label: "1UBQ", source_sha256: "x",
  pockets: [
    pocket(2, 1, 0.535, [res("1", "MET"), res("18", "GLU"), res("57", "SER")]),
    pocket(4, 2, 0.498, [res("7", "THR"), res("18", "GLU"), res("69", "LEU")]),
  ],
  summary: { count: 2, druggable: 1, threshold: 0.5, best: { id: 2, druggability: 0.535, volume: 323.7, residues: 3 } },
  structure: { residues: 76, chains: 1, heavy_atoms: 602, chains_detail: [{ chain: "A", first: "1", last: "76", residues: 76, sequence: "M", breaks: [], ligands: 0 }] },
  warnings: [], files: [], params: { program: "fpocket", options: "predefinite", druggable_threshold: 0.5 }, pdb: "structure_out/structure_out.pdb",
};

describe("tasche di fpocket", () => {
  it("soglia di druggability 0,5 (Schmidtke & Barril 2010)", () => {
    expect(isDruggable({ druggability: 0.5 })).toBe(true);
    expect(isDruggable({ druggability: 0.498 })).toBe(false);
    expect(druggabilityLabel({ druggability: 0.535 })).toBe("probabilmente druggabile");
    expect(druggabilityLabel({ druggability: undefined })).toBe("—");
  });

  it("colori per posto nella tabella, che si ripetono dopo l'ottavo", () => {
    expect(pocketColor(1)).toBe(POCKET_COLORS[0]);
    expect(pocketColor(9)).toBe(POCKET_COLORS[0]);
    expect(new Set(POCKET_COLORS).size).toBe(MAX_COLORED);
  });

  it("colora i residui delle tasche; un residuo condiviso prende la tasca più druggabile", () => {
    const c = pocketColoring(RESULT)!;
    expect(c.key).toBe("pocket");
    const map = coloringMap(c);
    expect(map.get("A:1")).toBe(pocketColor(1));
    expect(map.get("A:69")).toBe(pocketColor(2));
    expect(map.get("A:18")).toBe(pocketColor(1)); // in entrambe: vale la tasca 2 (prima per druggability)
    expect(map.has("A:30")).toBe(false);
    expect(c.chainColors).toEqual({ A: OUTSIDE_COLOR });
    expect(describeResidue(c, { chain: "A", resnum: "57" })).toBe("tasca 2 (druggability 0,54)");
    const legend = legendFor(c.scale, c.title);
    expect(legend.kind).toBe("swatches");
    expect(colorFor(c.scale, "4")).toBe(pocketColor(2));
  });

  it("con una tasca scelta colora solo quella", () => {
    const c = pocketColoring(RESULT, 4)!;
    expect(c.values.map((v) => v.resnum)).toEqual(["7", "18", "69"]);
    expect(c.title).toContain("Tasca 4");
    expect(pocketColoring(RESULT, 99)).toBeNull();
    expect(pocketColoring({ ...RESULT, pockets: [] })).toBeNull();
  });

  it("sfere: i centri delle sfere alfa piccoli e il centro della tasca", () => {
    const all = pocketSpheres(RESULT);
    expect(all).toHaveLength(2 * 2 + 2);
    expect(all.filter((s) => s.radius === CENTER_DRAW_RADIUS).map((s) => s.color)).toEqual(["#ffffff", "#ffffff"]);
    expect(all.filter((s) => s.radius === SPHERE_DRAW_RADIUS).every((s) => s.opacity < 1)).toBe(true);
    expect(pocketSpheres(RESULT, 2)).toHaveLength(3);
    expect(pocketSpheres(null)).toEqual([]);
  });

  it("tasca di un residuo, evidenza ed elenco dei residui", () => {
    expect(pocketOfResidue(RESULT, { chain: "A", resnum: "18" })?.id).toBe(2);
    expect(pocketOfResidue(RESULT, { chain: "A", resnum: "69" })?.id).toBe(4);
    expect(pocketOfResidue(RESULT, { chain: "B", resnum: "18" })).toBeNull();
    expect(pocketHighlight(RESULT.pockets[0])).toEqual([{ chain: "A", resi: 1 }, { chain: "A", resi: 18 }, { chain: "A", resi: 57 }]);
    expect(residueList(RESULT.pockets[0])).toBe("MET1, GLU18, SER57");
    expect(residueList(RESULT.pockets[0], 2)).toBe("MET1, GLU18 … (+1)");
    expect(residueList(pocket(5, 3, 0.1, [res("57", "SER", "A"), res("57", "SER", "B")]))).toBe("SER57:A, SER57:B");
  });
});
