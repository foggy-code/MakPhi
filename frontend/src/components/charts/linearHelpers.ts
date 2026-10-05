import type { ResidueRow } from "../../api/types";
import { PALETTES, uniformStops, type ContinuousScale } from "../../lib/colorscale";
import { contrastRatio, parseColor, type RGBA } from "../../lib/contrast";
import { niceTicks, type Domain } from "../../lib/zoom";

/**
 * Funzioni pure dei grafici lineari (profili, consenso, mappe delle regioni, identità): asse dei residui ingrandibile,
 * tratti consecutivi, testo leggibile sulle celle colorate. Convenzione: il residuo (o la posizione) `i` ha il centro
 * in `i` sull'asse; i tratti vanno da `inizio − 0,5` a `fine + 0,5`.
 */
type ResidueId = Pick<ResidueRow, "chain" | "resnum">;

/** "ALA A12": in lib/residues (anche per le lib); qui per gli import esistenti dal barrel dei grafici. */
export { residueName } from "../../lib/residues";

/** Numero del residuo sull'asse: con più catene anche la catena ("B12"). */
export function residueLabel(r: ResidueId, multi: boolean): string {
  return multi ? `${r.chain}${r.resnum}` : r.resnum;
}

export function manyChains(residues: ResidueId[]): boolean {
  return residues.some((r) => r.chain !== residues[0].chain);
}

/** Indici con il centro nell'intervallo visibile: [primo, ultimo] (nessuno se ultimo < primo). */
export function centeredRange(d: Domain, count: number): [number, number] {
  return [Math.max(0, Math.ceil(d.lo - 1e-6)), Math.min(count - 1, Math.floor(d.hi + 1e-6))];
}

/** Tacche intere tra lo e hi (circa `count`), con passi 1, 2, 5, 10… */
export function integerTicks(lo: number, hi: number, count: number): number[] {
  return niceTicks(lo, hi, count, [1, 2, 5, 10]).filter((v) => Number.isInteger(v));
}

/**
 * Numero del primo residuo se la numerazione è intera e contigua in una sola catena (es. 25, 26, … 324): le tacche
 * dell'asse cadono allora su numeri di residuo tondi. Con più catene, lacune o codici d'inserzione: null.
 */
export function firstResnum(residues: ResidueId[]): number | null {
  if (!residues.length) return null;
  const start = Number(residues[0].resnum);
  if (!Number.isInteger(start)) return null;
  const chain = residues[0].chain;
  return residues.every((r, i) => r.chain === chain && r.resnum === String(start + i)) ? start : null;
}

/**
 * Tacche dell'asse dei residui (indici): posizioni tonde nella numerazione che parte da `first`, il numero del primo
 * residuo (predefinito 1: la posizione nella sequenza).
 */
export function residueTicks(d: Domain, count: number, n: number, first = 1): number[] {
  const [lo, hi] = centeredRange(d, n);
  if (hi < lo) return [];
  return integerTicks(lo + first, hi + first, count).map((p) => p - first);
}

/**
 * Intervallo visibile per la barra dello zoom: "residui 12–48 (300 in tutto)". I numeri sono quelli dei residui (PDB,
 * con lacune o non da 1): "12–48 di 300" sembrerebbe una posizione su 300.
 */
export function residueSpan(residues: ResidueId[], d: Domain, multi: boolean): string {
  const [first, last] = centeredRange(d, residues.length);
  if (last < first) return `${residues.length} residui`;
  return `residui ${residueLabel(residues[first], multi)}–${residueLabel(residues[last], multi)} (${residues.length} in tutto)`;
}

/** Decimali delle etichette di un asse con tacche a passo costante (passi 1, 2, 5 × 10ⁿ); una tacca sola: come i valori. */
export function tickDigits(ticks: number[]): number {
  if (ticks.length < 2) return Math.abs(ticks[0] ?? 0) >= 100 ? 0 : 2;
  const step = Math.abs(ticks[1] - ticks[0]);
  return step > 0 ? Math.max(0, Math.min(4, -Math.floor(Math.log10(step) + 1e-9))) : 0;
}

/** Tratti di valori uguali consecutivi (indici inclusi): uno per tratto invece di un rettangolo per residuo. */
export function runs<T>(values: ArrayLike<T>): { start: number; end: number; value: T }[] {
  const out: { start: number; end: number; value: T }[] = [];
  for (let i = 0; i < values.length; i++) {
    const last = out[out.length - 1];
    if (last && last.value === values[i]) last.end = i;
    else out.push({ start: i, end: i, value: values[i] });
  }
  return out;
}

/** Il tratto [start, end] (da start − 0,5 a end + 0,5 sull'asse) entra, anche in parte, nell'intervallo visibile. */
export function inView(run: { start: number; end: number }, d: Domain): boolean {
  return run.end + 0.5 > d.lo && run.start - 0.5 < d.hi;
}

/**
 * Scala dell'identità di sequenza (0–100%): viridis, uguale nei due temi e nel PNG; percettivamente uniforme e
 * leggibile anche da chi non distingue rosso e verde.
 */
export const IDENTITY_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: uniformStops(0, 100, PALETTES.viridis),
  ticks: [0, 25, 50, 75, 100].map((value) => ({ value, label: `${value}%` })),
  digits: 0,
  unit: "%",
};

const BLACK: RGBA = [0, 0, 0, 1];
const WHITE: RGBA = [255, 255, 255, 1];

/** Testo su una cella colorata (colore esadecimale): nero o bianco, quello con il contrasto maggiore. */
export function textOn(fill: string): string {
  const bg = parseColor(fill);
  if (!bg) return "var(--text)";
  return contrastRatio(BLACK, bg) >= contrastRatio(WHITE, bg) ? "#000000" : "#ffffff";
}
