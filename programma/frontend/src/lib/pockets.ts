// Tasche di fpocket (F7b): colorazione dei residui che rivestono le tasche, sfere alfa e centri nel viewer, righe della
// tabella. Logica pura; il risultato arriva già ordinato per druggability dal backend (domain/pockets.py).

import type { Pocket, PocketsResult } from "../api/types";
import { CONTEXT_COLOR, MARKER_COLOR, type CategoricalScale } from "./colorscale";
import { fmtNum } from "./format";
import type { ResidueColoring, ResidueRef, ResidueValue } from "./residueColoring";
import { resiNumber, residueKey } from "./residueColoring";

/** Druggability ≥ 0,5: tasca probabilmente in grado di legare una piccola molecola simile a un farmaco
 *  (Schmidtke & Barril, J. Med. Chem. 53:5858, 2010; soglia della documentazione di fpocket). */
export const DRUGGABLE = 0.5;

/** Colori delle tasche nell'ordine della tabella: tavolozza qualitativa di Okabe & Ito (2008), distinguibile anche con
 *  le discromatopsie più comuni; oltre l'ottava tasca i colori si ripetono. */
export const POCKET_COLORS = ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999"];
/** Tasche colorate insieme nella vista "tutte le tasche": le prime per druggability. */
export const MAX_COLORED = 8;
/** Residui fuori dalle tasche: il grigio di contesto (più scuro nel tema chiaro), così le tasche risaltano. */
export const OUTSIDE_COLOR = CONTEXT_COLOR;
/** Centro della tasca: bianco, ardesia quasi nera nel tema chiaro (il visualizzatore 3D disegna le sfere con `themedColor`). */
export const CENTER_COLOR = MARKER_COLOR;
/** Raggio delle sfere disegnate sui centri delle sfere alfa (Å): piccole, come lo script PyMOL di fpocket (scala 0,3),
 *  perché le sfere alfa vere (3,4–6,2 Å) coprirebbero la proteina. */
export const SPHERE_DRAW_RADIUS = 0.45;
export const CENTER_DRAW_RADIUS = 1.1;

export function pocketColor(rank: number): string {
  return POCKET_COLORS[(Math.max(1, rank) - 1) % POCKET_COLORS.length];
}

export function isDruggable(p: Pick<Pocket, "druggability">): boolean {
  return (p.druggability ?? 0) >= DRUGGABLE;
}

export function druggabilityLabel(p: Pick<Pocket, "druggability">): string {
  if (p.druggability == null) return "—";
  return isDruggable(p) ? "probabilmente druggabile" : "poco druggabile";
}

/** Prima tasca (per druggability) che contiene il residuo, oppure null. */
export function pocketOfResidue(result: PocketsResult | null | undefined, ref: ResidueRef): Pocket | null {
  const key = residueKey(ref.chain, ref.resnum);
  return result?.pockets.find((p) => p.residues.some((r) => residueKey(r.chain, r.resnum) === key)) ?? null;
}

/** Colorazione per tasca: con `selected` solo quella tasca, altrimenti le prime `MAX_COLORED`. Un residuo che riveste
 *  più tasche prende il colore di quella con druggability più alta (la prima nell'ordine). */
export function pocketColoring(result: PocketsResult | null | undefined, selected?: number | null): ResidueColoring | null {
  if (!result?.pockets.length) return null;
  const shown = selected != null ? result.pockets.filter((p) => p.id === selected) : result.pockets.slice(0, MAX_COLORED);
  if (!shown.length) return null;
  const values: ResidueValue[] = [];
  const seen = new Set<string>();
  for (const p of shown) {
    for (const r of p.residues) {
      const key = residueKey(r.chain, r.resnum);
      if (seen.has(key)) continue;
      seen.add(key);
      values.push({ chain: r.chain, resnum: r.resnum, value: String(p.id), note: `druggability ${fmtNum(p.druggability ?? null, 2)}` });
    }
  }
  const scale: CategoricalScale = {
    kind: "categorical",
    categories: shown.map((p) => ({ key: String(p.id), color: pocketColor(p.rank), label: `Tasca ${p.id} · ${fmtNum(p.druggability ?? null, 2)}` })),
  };
  const chains = new Set<string>(result.structure.chains_detail.map((c) => c.chain));
  return {
    key: "pocket",
    title: selected != null ? `Tasca ${selected} (residui che la rivestono)` : "Tasche di fpocket (druggability)",
    values, scale,
    describe: (v) => (v == null ? "fuori dalle tasche" : `tasca ${v}`),
    chainColors: Object.fromEntries([...chains].map((c) => [c, OUTSIDE_COLOR])),
    legendExtra: [{ color: OUTSIDE_COLOR, label: "fuori dalle tasche" }],
  };
}

/** Sfere da disegnare: per ogni tasca mostrata i centri delle sfere alfa (piccoli) e il centro della tasca (`CENTER_COLOR`). */
export function pocketSpheres(result: PocketsResult | null | undefined, selected?: number | null): { center: [number, number, number]; radius: number; color: string; opacity: number }[] {
  if (!result) return [];
  const shown = selected != null ? result.pockets.filter((p) => p.id === selected) : result.pockets.slice(0, MAX_COLORED);
  const out: { center: [number, number, number]; radius: number; color: string; opacity: number }[] = [];
  for (const p of shown) {
    for (const s of p.spheres) out.push({ center: s.center, radius: SPHERE_DRAW_RADIUS, color: pocketColor(p.rank), opacity: 0.85 });
    if (p.center) out.push({ center: p.center, radius: CENTER_DRAW_RADIUS, color: CENTER_COLOR, opacity: 0.9 });
  }
  return out;
}

/** Residui di una tasca da evidenziare (bastoncini) e centrare nel viewer. */
export function pocketHighlight(p: Pocket | null | undefined): { chain: string; resi: number }[] {
  return (p?.residues ?? []).map((r) => ({ chain: r.chain, resi: resiNumber(r.resnum) }));
}

/** "MET1, GLU18, …" con al massimo `max` residui; la catena compare se la tasca ne tocca più di una ("SER57:B"). */
export function residueList(p: Pocket, max = 12): string {
  const multi = new Set(p.residues.map((r) => r.chain)).size > 1;
  const names = p.residues.map((r) => `${r.resname}${r.resnum}${multi ? `:${r.chain}` : ""}`);
  return names.length > max ? `${names.slice(0, max).join(", ")} … (+${names.length - max})` : names.join(", ");
}
