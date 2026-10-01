// Elettrostatica (F7b): colorazione del potenziale di superficie per residuo, residui più carichi, testi. Logica pura;
// il calcolo è nel backend (PDB2PQR + APBS, domain/electrostatics.py).

import type { ElectrostaticsResidue, ElectrostaticsResult, ResidueRow } from "../api/types";
import { fmtScaleValue, type ContinuousScale } from "./colorscale";
import type { ResidueColoring } from "./residueColoring";

/** Estremi della scala (kT/e): oltre si satura. ±5 kT/e è l'intervallo usato di solito per colorare le superfici con il
 *  potenziale di APBS (PyMOL, Chimera); a 298,15 K 1 kT/e = 25,69 mV. */
export const POTENTIAL_LIMIT = 5;
export const MV_PER_KT_E = 25.69;

/** Divergente rosso (negativo) – bianco (0) – blu (positivo), la convenzione dei visualizzatori di APBS; estremi della
 *  tavolozza RdBu di ColorBrewer, come lo schema blu-bianco-rosso di colorscale.ts. */
export const POTENTIAL_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: [
    { value: -POTENTIAL_LIMIT, color: "#b2182b" },
    { value: -POTENTIAL_LIMIT / 2, color: "#ef8a62" },
    { value: 0, color: "#f7f7f7" },
    { value: POTENTIAL_LIMIT / 2, color: "#67a9cf" },
    { value: POTENTIAL_LIMIT, color: "#2166ac" },
  ],
  ticks: [
    { value: -POTENTIAL_LIMIT, label: `≤ −${POTENTIAL_LIMIT}` },
    { value: 0, label: "0" },
    { value: POTENTIAL_LIMIT, label: `≥ +${POTENTIAL_LIMIT} kT/e` },
  ],
  digits: 2,
  unit: "kT/e",
};

export function toMillivolt(kte: number, mvPerUnit = MV_PER_KT_E): number {
  return kte * mvPerUnit;
}

/** "+2,42 kT/e (62 mV)"; "sepolto" senza valore. */
export function potentialText(v: number | null | undefined, mvPerUnit = MV_PER_KT_E): string {
  if (v == null || !Number.isFinite(v)) return "sepolto (nessun atomo esposto)";
  const sign = v > 0 ? "+" : "";
  return `${sign}${fmtScaleValue(v, 2, "kT/e")} (${sign}${Math.round(toMillivolt(v, mvPerUnit))} mV)`;
}

export function chargeText(q: number | null | undefined): string {
  if (q == null) return "—";
  const r = Math.round(q * 100) / 100;
  return r > 0 ? `+${r.toLocaleString("it-IT")}` : r.toLocaleString("it-IT");
}

/** Colorazione del 3D e della striscia: potenziale per residuo con la scala divergente ±5 kT/e. */
export function potentialColoring(result: ElectrostaticsResult | null | undefined): ResidueColoring | null {
  if (!result?.residues.length) return null;
  const mv = result.scale?.mv_per_unit ?? MV_PER_KT_E;
  return {
    key: "potential",
    title: `Potenziale di superficie (kT/e, ${result.forcefield}, pH ${result.ph.toLocaleString("it-IT")})`,
    values: result.residues.map((r) => ({
      chain: r.chain, resnum: r.resnum, value: r.potential,
      note: r.charge != null && Math.abs(r.charge) >= 0.5 ? `carica ${chargeText(r.charge)}` : undefined,
    })),
    scale: POTENTIAL_SCALE,
    describe: (v) => potentialText(typeof v === "number" ? v : null, mv),
  };
}

/** Punti di superficie minimi perché un residuo entri fra gli estremi: un residuo quasi sepolto (un atomo, pochi punti
 *  in una fessura) ha un valore rumoroso, che resta nel 3D ma non va elencato come "il più carico". */
export const EXTREME_MIN_POINTS = 10;

/** I residui con il potenziale più positivo e più negativo (esposti, almeno `EXTREME_MIN_POINTS` punti), per l'elenco
 *  accanto al 3D. */
export function extremeResidues(result: ElectrostaticsResult | null | undefined, n = 5): { positive: ElectrostaticsResidue[]; negative: ElectrostaticsResidue[] } {
  const exposed = (result?.residues ?? []).filter((r) => r.potential != null && r.points >= EXTREME_MIN_POINTS);
  const sorted = [...exposed].sort((a, b) => (b.potential as number) - (a.potential as number));
  return {
    positive: sorted.filter((r) => (r.potential as number) > 0).slice(0, n),
    negative: sorted.filter((r) => (r.potential as number) < 0).reverse().slice(0, n),
  };
}

/** "7 LYS+, 4 ARG+, 1 N-term+ · 6 GLU−, 5 ASP−, 1 C-term−": residui carichi per tipo, prima i positivi. */
export function chargeBreakdown(result: ElectrostaticsResult | null | undefined): string {
  const entries = Object.entries(result?.charges.by_type ?? {});
  const part = (sign: string) => entries.filter(([k]) => k.endsWith(sign)).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n} ${k}`).join(", ");
  return [part("+"), part("−")].filter(Boolean).join(" · ") || "nessun residuo carico";
}

/** Righe per la striscia del potenziale (`ResidueStrip` vuole righe del rapporto di qualità: qui catena, numero, nome). */
export function potentialStripRows(result: ElectrostaticsResult | null | undefined): ResidueRow[] {
  return (result?.residues ?? []).map((r) => ({
    chain: r.chain, resnum: r.resnum, resname: r.resname, aa: r.resname, ss: "-", ss3: "C", rsa: null, bfactor: null, phi: null, psi: null,
    rama: null, clashes: 0, dope: null,
  }));
}
