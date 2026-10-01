// Conversione del rapporto di qualità in valori per residuo da colorare (viewer 3D, strisce dei profili).
// Logica pura: ogni opzione "Colora per" produce l'elenco {catena, residuo, valore} e la sua scala.

import type { ConsensusResult, QualityReport, ResidueRow } from "../api/types";
import { PLDDT_BAND_LABEL, PLDDT_BAND_MIN, plddtBand } from "./alphafold";
import { colorFor, fmtScaleValue, PALETTES, uniformStops, type BinnedScale, type CategoricalScale, type ColorScale, type ContinuousScale } from "./colorscale";

export type ColorBy = "ss" | "rama" | "clashes" | "dope" | "rsa" | "bfactor" | "plddt" | "qmean" | "consensus";

export interface ResidueRef {
  chain: string;
  resnum: string;
}

export interface ResidueValue extends ResidueRef {
  value: number | string | null;
  /** dettaglio per il tooltip (es. i metodi che segnalano il residuo) */
  note?: string;
  /** colore fisso al posto di quello della scala (es. la struttura di riferimento nel confronto) */
  color?: string;
}

export interface ResidueColoring {
  /** opzione "Colora per" della pagina Qualità, del confronto strutturale (lib/compare.ts), dell'analisi strutturale
   *  (lib/interactions.ts), delle tasche (lib/pockets.ts) o dell'elettrostatica (lib/electrostatics.ts) */
  key: ColorBy | "distance" | "lddt" | "conservation" | "interface" | "pocket" | "potential";
  /** titolo della legenda */
  title: string;
  values: ResidueValue[];
  scale: ColorScale;
  /** testo di un valore (etichette e tooltip) */
  describe: (value: number | string | null) => string;
  /** colore dei residui non elencati, per catena (altrimenti il grigio "senza valore") */
  chainColors?: Record<string, string>;
  /** campioni aggiunti alla legenda (es. "A (riferimento)") */
  legendExtra?: { color: string; label: string }[];
}

/** Chiave di un residuo: catena + numero con eventuale codice di inserzione ("A:52A"). */
export function residueKey(chain: string, resnum: string | number): string {
  return `${chain}:${String(resnum).trim()}`;
}

/** Chiave di un atomo di 3Dmol (resi numerico, icode separato). */
export function atomKey(atom: { chain?: string; resi?: number; icode?: string }): string {
  return residueKey(atom.chain ?? "", `${atom.resi ?? ""}${(atom.icode ?? "").trim()}`);
}

/** Numero del residuo per le selezioni di 3Dmol (senza codice di inserzione). */
export function resiNumber(resnum: string): number {
  return parseInt(resnum, 10);
}

// ---------------------------------------------------------------------------------- scale
export const SS_SCALE: CategoricalScale = {
  kind: "categorical",
  categories: [
    { key: "H", color: "#f472b6", label: "Elica" },
    { key: "E", color: "#fbbf24", label: "Foglietto β" },
    { key: "C", color: "#94a3b8", label: "Coil" },
  ],
};

export const RAMA_SCALE: CategoricalScale = {
  kind: "categorical",
  categories: [
    { key: "favored", color: "#34d399", label: "Favorita" },
    { key: "allowed", color: "#fbbf24", label: "Consentita" },
    { key: "outlier", color: "#ef4444", label: "Outlier" },
  ],
};

export const CLASH_SCALE: BinnedScale = {
  kind: "binned",
  bins: [
    { gt: 1, color: "#dc2626", label: "2 o più clash" },
    { gt: 0, color: "#f59e0b", label: "1 clash" },
    { color: "#cbd5e1", label: "Nessun clash" },
  ],
  digits: 0,
};

/**
 * Colori ufficiali di AlphaFold DB (Varadi et al. 2022; tema pLDDT di Mol*) sulle fasce di `plddtBand`, con il
 * limite inferiore compreso come il ritaglio e i riepiloghi del backend: ≥ 90, 70–90, 50–70, < 50.
 */
export const PLDDT_SCALE: BinnedScale = {
  kind: "binned",
  bins: [
    { gte: PLDDT_BAND_MIN.very_high, color: "#0053D6", label: `Molto alta (≥ ${PLDDT_BAND_MIN.very_high})` },
    { gte: PLDDT_BAND_MIN.confident, color: "#65CBF3", label: `Confidente (${PLDDT_BAND_MIN.confident}–${PLDDT_BAND_MIN.very_high})` },
    { gte: PLDDT_BAND_MIN.low, color: "#FFDB13", label: `Bassa (${PLDDT_BAND_MIN.low}–${PLDDT_BAND_MIN.confident})` },
    { color: "#FF7D45", label: `Molto bassa (< ${PLDDT_BAND_MIN.low})` },
  ],
  digits: 1,
};

/** QMEAN locale 0–1: sotto 0,6 qualità bassa attesa (SWISS-MODEL), da cui il salto di colore. */
export const QMEAN_LOCAL_THRESHOLD = 0.6;
export const QMEAN_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: [
    { value: 0, color: "#b2182b" },
    { value: QMEAN_LOCAL_THRESHOLD, color: "#f4a582" },
    { value: QMEAN_LOCAL_THRESHOLD, color: "#92c5de" },
    { value: 1, color: "#2166ac" },
  ],
  ticks: [{ value: 0 }, { value: QMEAN_LOCAL_THRESHOLD, label: "0,6 soglia" }, { value: 1 }],
  digits: 2,
};

export const CONSENSUS_SCALE: BinnedScale = {
  kind: "binned",
  bins: [
    { gt: 2, color: "#dc2626", label: "3 o più metodi" },
    { gt: 1, color: "#fb923c", label: "2 metodi" },
    { gt: 0, color: "#fde047", label: "1 metodo" },
    { color: "#e2e8f0", label: "Nessuna segnalazione" },
  ],
  digits: 0,
};

export const RSA_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: uniformStops(0, 1, PALETTES.viridis),
  ticks: [{ value: 0, label: "0 sepolto" }, { value: 0.2, label: "0,2" }, { value: 1, label: "≥ 1 esposto" }],
  digits: 2,
};

const SS_LABEL: Record<string, string> = { H: "elica", E: "foglietto β", C: "coil" };
const RAMA_LABEL: Record<string, string> = { favored: "favorita", allowed: "consentita", outlier: "outlier" };
export const SIGNAL_LABEL: Record<string, string> = { rama: "Ramachandran", clash: "clash", dope: "DOPE", qmean: "QMEAN", prosa: "ProSA" };

function numbers(values: (number | null | undefined)[]): number[] {
  return values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
}

/** Scala continua tra minimo e massimo dei valori, con tacche facoltative (es. soglia). */
export function rangeScale(values: (number | null | undefined)[], palette: readonly string[], digits: number, unit?: string, extra: { value: number; label: string }[] = []): ContinuousScale | null {
  const nums = numbers(values);
  if (!nums.length) return null;
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const ticks = [{ value: min }, ...extra.filter((t) => t.value > min && t.value < max), { value: max }];
  return { kind: "continuous", stops: uniformStops(min, max, palette), ticks, digits, unit };
}

/** Valori del consenso allineati ai residui del rapporto (null se non corrispondono). */
function consensusArrays(report: QualityReport, consensus?: ConsensusResult | null) {
  const per = consensus?.per_residue;
  if (!per || per.counts.length !== report.residues.length) return null;
  return per;
}

export function isPlddt(report: QualityReport): boolean {
  return report.structure.bfactor_kind === "plddt";
}

export interface ColorOption {
  value: ColorBy;
  label: string;
  available: boolean;
  /** perché non è disponibile */
  note?: string;
}

/** Opzioni "Colora per" della pagina Qualità, con la disponibilità per questo rapporto. */
export function colorOptions(report: QualityReport, consensus?: ConsensusResult | null): ColorOption[] {
  const res = report.residues;
  const per = consensusArrays(report, consensus);
  const plddt = isPlddt(report);
  const opts: ColorOption[] = [
    { value: "ss", label: "Struttura secondaria", available: true },
    { value: "rama", label: "Ramachandran", available: res.some((r) => r.rama !== null) },
    { value: "clashes", label: "Clash", available: true },
    { value: "dope", label: "DOPE per residuo", available: res.some((r) => r.dope !== null), note: "richiede il calcolo DOPE (MODELLER)" },
    { value: "rsa", label: "RSA", available: res.some((r) => r.rsa !== null) },
    plddt
      ? { value: "plddt", label: "pLDDT (AlphaFold)", available: true }
      : { value: "bfactor", label: "B-factor", available: res.some((r) => r.bfactor !== null) },
    { value: "qmean", label: "QMEAN locale", available: !!per?.values.qmean?.some((v) => v !== null), note: "calcola QMEAN su questa struttura" },
    { value: "consensus", label: "Consenso per residuo", available: !!per, note: "consenso non disponibile" },
  ];
  return opts;
}

/** Valori e scala per un'opzione "Colora per"; null se il dato non c'è. */
export function residueColoring(report: QualityReport, by: ColorBy, consensus?: ConsensusResult | null): ResidueColoring | null {
  const res = report.residues;
  const map = (fn: (r: ResidueRow, i: number) => number | string | null, note?: (r: ResidueRow, i: number) => string | undefined): ResidueValue[] =>
    res.map((r, i) => ({ chain: r.chain, resnum: r.resnum, value: fn(r, i), note: note?.(r, i) }));
  const num = (digits: number, unit?: string) => (v: number | string | null) => (typeof v === "number" ? fmtScaleValue(v, digits, unit) : "—");
  switch (by) {
    case "ss":
      return { key: by, title: "Struttura secondaria (DSSP)", values: map((r) => r.ss3), scale: SS_SCALE, describe: (v) => SS_LABEL[String(v)] ?? "—" };
    case "rama":
      return { key: by, title: "Ramachandran", values: map((r) => r.rama), scale: RAMA_SCALE, describe: (v) => (v ? RAMA_LABEL[String(v)] ?? String(v) : "senza φ/ψ") };
    case "clashes":
      return { key: by, title: "Contatti sterici gravi per residuo", values: map((r) => r.clashes), scale: CLASH_SCALE, describe: (v) => (typeof v === "number" ? `${v} clash` : "—") };
    case "dope": {
      const values = res.map((r) => r.dope);
      const threshold = consensus?.per_residue.dope_threshold;
      const scale = rangeScale(values, PALETTES.blueWhiteRed, 3, undefined, threshold != null ? [{ value: threshold, label: "picco" }] : []);
      if (!scale) return null;
      return { key: by, title: "DOPE per residuo (più alto = peggio)", values: map((r) => r.dope), scale, describe: num(3) };
    }
    case "rsa":
      if (!res.some((r) => r.rsa !== null)) return null;
      return { key: by, title: "Accessibilità relativa (RSA)", values: map((r) => r.rsa), scale: RSA_SCALE, describe: num(2) };
    case "bfactor": {
      const scale = rangeScale(res.map((r) => r.bfactor), PALETTES.blueWhiteRed, 1, "Å²");
      if (!scale) return null;
      return { key: by, title: "B-factor medio", values: map((r) => r.bfactor), scale, describe: num(1, "Å²") };
    }
    case "plddt":
      if (!res.some((r) => r.bfactor !== null)) return null;
      return {
        key: by, title: "pLDDT (AlphaFold)", values: map((r) => r.bfactor), scale: PLDDT_SCALE,
        describe: (v) => (typeof v === "number" ? `${fmtScaleValue(v, 1)} · ${PLDDT_BAND_LABEL[plddtBand(v)]}` : "—"),
      };
    case "qmean": {
      const per = consensusArrays(report, consensus);
      const q = per?.values.qmean;
      if (!q || !q.some((v) => v !== null)) return null;
      return { key: by, title: "QMEAN locale (0–1)", values: map((_r, i) => q[i] ?? null), scale: QMEAN_SCALE, describe: num(2) };
    }
    case "consensus": {
      const per = consensusArrays(report, consensus);
      if (!per) return null;
      return {
        key: by, title: "Metodi che segnalano il residuo", values: map((_r, i) => per.counts[i], (_r, i) => per.flags[i].map((k) => SIGNAL_LABEL[k] ?? k).join(", ") || undefined),
        scale: CONSENSUS_SCALE, describe: (v) => (typeof v === "number" ? (v === 1 ? "1 metodo" : `${v} metodi`) : "—"),
      };
    }
  }
}

/** Colore di ogni residuo (chiave `residueKey`). */
export function coloringMap(coloring: ResidueColoring): Map<string, string> {
  const out = new Map<string, string>();
  coloring.values.forEach((v) => out.set(residueKey(v.chain, v.resnum), v.color ?? colorFor(coloring.scale, v.value)));
  return out;
}

/** Ci sono residui senza valore (in grigio)? Serve alla legenda. Un residuo con colore fisso non conta. */
export function hasMissing(coloring: ResidueColoring): boolean {
  return coloring.values.some((v) => !v.color && (v.value === null || v.value === undefined));
}

/** Testo del valore di un residuo per il tooltip ("—" se il residuo non è nell'elenco). */
export function describeResidue(coloring: ResidueColoring, ref: ResidueRef): string {
  const key = residueKey(ref.chain, ref.resnum);
  const v = coloring.values.find((x) => residueKey(x.chain, x.resnum) === key);
  if (!v) return "—";
  const text = coloring.describe(v.value);
  return v.note ? `${text} (${v.note})` : text;
}

/** Residui di una regione del consenso (indici inclusi nel rapporto), per evidenziarli nel viewer. */
export function regionResidues(report: QualityReport, region: { start_index: number; end_index: number }): ResidueRef[] {
  return report.residues.slice(region.start_index, region.end_index + 1).map((r) => ({ chain: r.chain, resnum: r.resnum }));
}
