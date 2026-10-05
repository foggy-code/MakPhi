// Pannelli di analisi (mutagenesi, tasche, elettrostatica, interazioni, confronto con una predizione, job): serie dei
// grafici a barre (una barra per elemento o per residuo), dati per la tabella e il CSV dei grafici, campi dei dettagli
// espansi. Logica pura; i componenti sono ValueBars e i pannelli che la usano.

import type {
  AlaScanResult, AlaScanRow, AnalysisResult, ConservationResult, ElectrostaticsResidue, ElectrostaticsResult, InterfacePair, JobSummary,
  MutationDope, MutationEnergy, MutationInteraction, MutationSite, Plausibility, Pocket,
} from "../api/types";
import { PLDDT_BAND_LABEL, type PlddtBand } from "./alphafold";
import { colorFor } from "./colorscale";
import type { ChartData } from "./exportImage";
import { fmtDate, fmtDuration, fmtNum, fmtSigned, fmtUnit, isActive } from "./format";
import { endLabel, INTERFACE_SCALE, interactionRows, type InteractionKind } from "./interactions";
import { CLASS_COLOR, CLASS_LABEL } from "./mutations";
import { DRUGGABLE, pocketColor } from "./pockets";
import { RAMA_SHORT_LABEL, type RamaRegion } from "./ramachandran";
import { PLDDT_SCALE } from "./residueColoring";
import type { Domain } from "./zoom";

/** Campo di un dettaglio espanso (DetailGrid). */
export interface DetailItem {
  label: string;
  value: string;
}

// ---------------------------------------------------------------------------------- barre
/** Barra di un grafico: un elemento (tasca, mutazione) o un residuo. */
export interface BarItem {
  /** chiave stabile (selezione condivisa con tabelle e 3D) */
  key: string;
  /** nome completo (suggerimento): "LEU A43", "Tasca 3" */
  label: string;
  /** etichetta breve sull'asse x: "43", "A:L43", "3" */
  tick: string;
  /** null: nessun valore (residuo sepolto, colonna con troppi gap), segnato sulla linea dello zero */
  value: number | null;
  /** colore della barra: token del tema o esadecimale */
  color: string;
  /** barra attenuata (sotto la soglia d'interesse) */
  faded?: boolean;
}

/** Linea di riferimento tratteggiata (soglia), con l'etichetta a destra. */
export interface BarReference {
  value: number;
  label: string;
  color?: string;
}

export interface BarSeries {
  items: BarItem[];
  /** nome del valore: asse y, suggerimento, colonna dei dati */
  label: string;
  unit?: string;
  digits: number;
  references: BarReference[];
  /** estremi fissi dell'asse y (es. 0–1) */
  domain?: [number, number];
  /** asse y simmetrico attorno allo zero (valori con segno) */
  symmetric?: boolean;
}

/** Estremi dell'asse y: comprendono lo zero, i valori e le soglie; un margine del 6% oltre il valore più lontano. */
export function barRange(values: (number | null)[], references: number[] = [], opts: { domain?: [number, number]; symmetric?: boolean } = {}): { min: number; max: number } {
  let min = 0;
  let max = 0;
  for (const v of [...values, ...references]) {
    if (v === null || !Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (opts.domain) {
    min = Math.min(min, opts.domain[0]);
    max = Math.max(max, opts.domain[1]);
  } else {
    if (opts.symmetric) {
      const m = Math.max(Math.abs(min), Math.abs(max));
      min = -m;
      max = m;
    }
    const pad = (max - min) * 0.06;
    if (max > 0) max += pad;
    if (min < 0) min -= pad;
  }
  if (!(max > min)) max = min + 1;
  return { min, max };
}

/** Ogni quante barre un'etichetta sull'asse x: passi tondi (1, 2, 5, 10, 20…) con almeno `minPx` fra due etichette. */
export function labelEvery(pxPerBar: number, minPx: number): number {
  if (!(pxPerBar > 0)) return 1;
  const raw = Math.max(1, Math.ceil(minPx / pxPerBar));
  for (let mag = 1; ; mag *= 10) {
    for (const m of [1, 2, 5]) if (m * mag >= raw) return m * mag;
  }
}

/** Intervallo visibile per la barra dello zoom: "A12–A48 di 300 residui". */
export function barSpan(items: Pick<BarItem, "tick">[], d: Domain, noun: string): string {
  const first = Math.max(0, Math.ceil(d.lo));
  const last = Math.min(items.length - 1, Math.floor(d.hi));
  if (last < first) return `${items.length} ${noun}`;
  return `${items[first].tick}–${items[last].tick} di ${items.length} ${noun}`;
}

/** Ordine dei residui: catena, numero, codice d'inserzione ("52" < "52A" < "100"). */
export function compareResidues(a: { chain: string; resnum: string }, b: { chain: string; resnum: string }): number {
  if (a.chain !== b.chain) return a.chain.localeCompare(b.chain);
  const na = parseInt(a.resnum, 10);
  const nb = parseInt(b.resnum, 10);
  if (na !== nb && Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return a.resnum.localeCompare(b.resnum, "it", { numeric: true });
}

const yesNo = (v: boolean) => (v ? "sì" : "no");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${fmtNum(v * 100, 0)}%`);

// ---------------------------------------------------------------------------------- scansione di alanina
export type ScanMetric = "eq" | "dope" | "interchain" | "energy";
export type ScanOrder = "position" | "effect";

const SCAN_METRIC: Record<ScanMetric, { short: string; label: string; unit?: string; digits: number; value: (r: AlaScanRow) => number | null }> = {
  eq: { short: "ΔDOPE (eq.)", label: "ΔDOPE in residui equivalenti", digits: 2, value: (r) => r.dope.delta_eq },
  dope: { short: "ΔDOPE", label: "ΔDOPE", digits: 0, value: (r) => r.dope.delta },
  interchain: { short: "Fra catene", label: "ΔDOPE fra le catene (residui equivalenti)", digits: 2, value: (r) => r.dope.interchain_delta_eq },
  energy: { short: "ΔΔE", label: "ΔΔE (OpenMM)", unit: "kJ/mol", digits: 0, value: (r) => r.energy?.ddE ?? null },
};

/** Valori disponibili per il grafico della scansione: fra catene solo con più catene, ΔΔE solo se calcolata. */
export function scanMetrics(result: Pick<AlaScanResult, "rows" | "interface">): { value: ScanMetric; label: string }[] {
  const keys: ScanMetric[] = ["eq", "dope"];
  if (result.interface) keys.push("interchain");
  if (result.rows.some((r) => r.energy?.ddE != null)) keys.push("energy");
  return keys.map((k) => ({ value: k, label: SCAN_METRIC[k].short }));
}

function references(pairs: [number | undefined, string, string][]): BarReference[] {
  return pairs.filter((p): p is [number, string, string] => p[0] != null && Number.isFinite(p[0])).map(([value, label, color]) => ({ value, label, color }));
}

/** Barre della scansione (colore della classe stimata) nell'ordine della sequenza o dell'effetto, con le soglie dei
 *  segnali del backend; `rows` sono le righe nello stesso ordine delle barre. */
export function scanSeries(result: Pick<AlaScanResult, "rows" | "protocol">, metric: ScanMetric, order: ScanOrder): BarSeries & { rows: AlaScanRow[] } {
  const m = SCAN_METRIC[metric];
  const rows = order === "position" ? [...result.rows].sort((a, b) => compareResidues(a.mutation, b.mutation)) : result.rows;
  const multi = new Set(result.rows.map((r) => r.mutation.chain)).size > 1;
  const t = result.protocol.thresholds ?? {};
  const refs = metric === "eq"
    ? references([[t.dope_moderate, "segnale moderato", "var(--warn)"], [t.dope_strong, "segnale forte", "var(--err)"]])
    : metric === "energy"
      ? references([[t.energy_moderate, "segnale moderato", "var(--warn)"], [t.energy_strong, "segnale forte", "var(--err)"]])
      : [];
  return {
    rows,
    items: rows.map((r) => ({
      key: r.label, label: `${r.label} · ${r.assessment.label}`, tick: `${multi ? `${r.mutation.chain}:` : ""}${r.mutation.wt}${r.mutation.resnum}`,
      value: m.value(r), color: CLASS_COLOR[r.assessment.code],
    })),
    label: m.label, unit: m.unit, digits: m.digits, references: refs,
  };
}

/** Tabella dei dati (e CSV) del grafico della scansione, nell'ordine delle barre. */
export function scanData(rows: AlaScanRow[]): ChartData {
  return {
    columns: [
      { key: "mutazione", label: "Mutazione" }, { key: "classe", label: "Effetto stimato" },
      { key: "dope", label: "ΔDOPE", num: true }, { key: "eq", label: "ΔDOPE (residui equivalenti)", num: true },
      { key: "fra", label: "ΔDOPE fra le catene (eq.)", num: true }, { key: "ddE", label: "ΔΔE (kJ/mol)", num: true },
      { key: "rsa", label: "RSA (%)", num: true }, { key: "dssp", label: "DSSP" }, { key: "hotspot", label: "Hot spot" },
      { key: "motivo", label: "Motivo principale" },
    ],
    rows: rows.map((r) => ({
      mutazione: r.label, classe: CLASS_LABEL[r.assessment.code], dope: r.dope.delta, eq: r.dope.delta_eq, fra: r.dope.interchain_delta_eq,
      ddE: r.energy?.ddE ?? null, rsa: r.site.rsa_wt != null ? Math.round(r.site.rsa_wt * 1000) / 10 : null, dssp: r.site.ss,
      hotspot: r.assessment.binding?.hotspot ?? "", motivo: r.assessment.reasons[0] ?? "",
    })),
  };
}

// ---------------------------------------------------------------------------------- siti, DOPE ed energia di una mutazione
const INTERACTION_TYPE: Record<MutationInteraction["type"], string> = { hbond: "legame H", salt_bridge: "ponte salino", disulfide: "disolfuro", aromatic: "aromatica" };
const ramaText = (region: string) => RAMA_SHORT_LABEL[region as RamaRegion] ?? region;

/** "VAL A26 (legame H, NH della catena principale)". */
export function interactionText(x: MutationInteraction): string {
  return `${x.partner} (${x.type === "hbond" ? `${INTERACTION_TYPE.hbond}, ${x.role}` : INTERACTION_TYPE[x.type] ?? x.type})`;
}

/** Interazione con atomo, distanza e catene: "VAL A26 (legame H, NH della catena principale) · atomo N · 2,97 Å". */
export function interactionLine(x: MutationInteraction): string {
  return [interactionText(x), x.atom ? `atomo ${x.atom}` : "", x.distance != null ? `${fmtNum(x.distance, 2)} Å` : "", x.inter_chain ? "fra catene" : ""]
    .filter(Boolean).join(" · ");
}

/** Campi del dettaglio di un sito mutato: esposizione, superficie sepolta, geometria, contatti, interfaccia. */
export function siteDetail(s: MutationSite): DetailItem[] {
  const out: DetailItem[] = [
    { label: "Esposizione (RSA)", value: `${pct(s.rsa_wt)} → ${pct(s.rsa_mut)} · ${s.core ? "nel nucleo" : "in superficie"}` },
    { label: "Superficie sepolta", value: `${fmtNum(s.buried_wt.total, 0)} → ${fmtNum(s.buried_mut.total, 0)} Å² (${fmtSigned(s.buried_delta.total, 0)})` },
    { label: "Superficie apolare sepolta", value: `${fmtNum(s.buried_wt.apolar, 0)} → ${fmtNum(s.buried_mut.apolar, 0)} Å² (${fmtSigned(s.buried_delta.apolar, 0)})` },
    { label: "DSSP", value: s.ss ? `${s.ss}${s.ss_length > 1 ? ` · posizione ${s.ss_position + 1} di ${s.ss_length}` : ""}` : "coil" },
    {
      label: "Ramachandran (φ, ψ)",
      value: s.rama ? `${fmtNum(s.rama.phi, 0)}°, ${fmtNum(s.rama.psi, 0)}° · ${ramaText(s.rama.wt)} → ${ramaText(s.rama.mut)}` : "—",
    },
    { label: "BLOSUM62", value: fmtSigned(s.blosum62, 0) },
  ];
  if (s.helix_propensity != null) out.push({ label: "Propensione per l'elica", value: `${fmtSigned(s.helix_propensity, 2)} kcal/mol` });
  out.push(
    { label: "Interazioni", value: `${s.interactions.wild_type} nel selvatico, ${s.interactions.mutant} nel mutante` },
    { label: "Contatti sterici gravi", value: `${s.clashes.wild_type} → ${s.clashes.mutant}${s.clashes.worst != null ? ` (peggiore ${fmtNum(s.clashes.worst, 2)} Å)` : ""}` },
    {
      label: "Interfaccia",
      value: s.interface
        ? `ΔSASA ${fmtNum(s.interface.delta, 0)} Å²${s.interface.rsa_complex != null ? ` · RSA nel complesso ${pct(s.interface.rsa_complex)}` : ""} · ${s.interface.lost_inter_chain} interazioni fra catene perse`
        : "no",
    },
  );
  return out;
}

/** Campi del DOPE di una variante: globale, locale, fra le catene, unità dei residui equivalenti, rumore dei modelli. */
export function dopeDetail(d: MutationDope): DetailItem[] {
  const out: DetailItem[] = [
    { label: "DOPE selvatico → mutante", value: `${fmtNum(d.wild_type, 1)} → ${fmtNum(d.mutant, 1)}` },
    {
      label: `DOPE locale (${d.local_residues} residui entro ${fmtNum(d.local_radius, 0)} Å)`,
      value: `${fmtNum(d.local_wild_type, 1)} → ${fmtNum(d.local_mutant, 1)}${d.delta_local != null ? ` (${fmtSigned(d.delta_local, 0)})` : ""}`,
    },
  ];
  if (d.interchain_delta != null) {
    out.push({
      label: "DOPE fra le catene",
      value: `${fmtNum(d.interchain_wild_type, 1)} → ${fmtNum(d.interchain_mutant, 1)} (${fmtSigned(d.interchain_delta, 0)}${d.interchain_delta_eq != null ? `; ${fmtSigned(d.interchain_delta_eq, 2)} eq.` : ""})`,
    });
  }
  out.push({ label: "Residuo equivalente", value: d.per_residue != null ? `${fmtNum(d.per_residue, 1)} (|DOPE del selvatico| / residui)` : "—" });
  const spread = [d.spread.wild_type != null ? `selvatico ${fmtNum(d.spread.wild_type, 1)}` : "", d.spread.mutant != null ? `mutante ${fmtNum(d.spread.mutant, 1)}` : ""].filter(Boolean);
  if (spread.length) out.push({ label: "Scarto fra i modelli", value: spread.join(", ") });
  return out;
}

/** Campi dell'energia di OpenMM (kJ/mol); vuoto se non calcolata. */
export function energyDetail(e: MutationEnergy | null | undefined): DetailItem[] {
  if (!e || e.ddE == null) return [];
  const kj = (v: number | null | undefined) => fmtUnit(v, 1, "kJ/mol");
  return [
    { label: "E selvatico → mutante", value: `${fmtNum(e.wild_type ?? null, 1)} → ${kj(e.mutant)}` },
    { label: "Riferimenti (tripeptidi)", value: `${fmtNum(e.reference_wild_type ?? null, 1)} → ${kj(e.reference_mutant)}` },
    { label: "Δ ripiegato − Δ riferimento", value: `${kj(e.delta_folded)} − ${kj(e.delta_reference)}` },
    { label: "ΔΔE", value: `${kj(e.ddE)}${e.ddE_kcal != null ? ` (${fmtNum(e.ddE_kcal, 1)} kcal/mol)` : ""}` },
  ];
}

// ---------------------------------------------------------------------------------- tasche
export type PocketMetric = "druggability" | "score" | "volume" | "hydrophobicity" | "spheres";

const POCKET_METRIC: Record<PocketMetric, { short: string; label: string; unit?: string; digits: number; value: (p: Pocket) => number | null; domain?: [number, number] }> = {
  druggability: { short: "Druggability", label: "Druggability (0–1)", digits: 2, value: (p) => p.druggability ?? null, domain: [0, 1] },
  score: { short: "Punteggio", label: "Punteggio di fpocket", digits: 3, value: (p) => p.score ?? null },
  volume: { short: "Volume", label: "Volume", unit: "Å³", digits: 0, value: (p) => p.volume ?? null },
  hydrophobicity: { short: "Idrofobicità", label: "Punteggio di idrofobicità", digits: 1, value: (p) => p.hydrophobicity_score ?? null },
  spheres: { short: "Sfere α", label: "Sfere alfa", digits: 0, value: (p) => p.alpha_spheres ?? null },
};

export const POCKET_METRICS: { value: PocketMetric; label: string }[] = (Object.keys(POCKET_METRIC) as PocketMetric[]).map((k) => ({ value: k, label: POCKET_METRIC[k].short }));

/** Barre delle tasche nell'ordine della tabella (druggability), con il colore della tasca nel 3D. */
export function pocketSeries(pockets: Pocket[], metric: PocketMetric): BarSeries {
  const m = POCKET_METRIC[metric];
  return {
    items: pockets.map((p) => ({ key: String(p.id), label: `Tasca ${p.id} (posto ${p.rank})`, tick: String(p.id), value: m.value(p), color: pocketColor(p.rank) })),
    label: m.label, unit: m.unit, digits: m.digits, domain: m.domain,
    references: metric === "druggability" ? [{ value: DRUGGABLE, label: "probabilmente druggabile", color: "var(--ok)" }] : [],
  };
}

/** Tutti i descrittori delle tasche (tabella dei dati e CSV), nell'ordine della tabella. */
export function pocketsData(pockets: Pocket[]): ChartData {
  return {
    columns: [
      { key: "posto", label: "Posto", num: true }, { key: "tasca", label: "Tasca", num: true }, { key: "druggability", label: "Druggability", num: true },
      { key: "punteggio", label: "Punteggio", num: true }, { key: "volume", label: "Volume (Å³)", num: true }, { key: "sfere", label: "Sfere α", num: true },
      { key: "raggio", label: "Raggio medio delle sfere (Å)", num: true }, { key: "idrofobicita", label: "Idrofobicità", num: true },
      { key: "polarita", label: "Polarità", num: true }, { key: "carica", label: "Carica", num: true }, { key: "polari", label: "Atomi polari (%)", num: true },
      { key: "sasa", label: "SASA (Å²)", num: true }, { key: "sasa_polare", label: "SASA polare (Å²)", num: true }, { key: "sasa_apolare", label: "SASA apolare (Å²)", num: true },
      { key: "flessibilita", label: "Flessibilità", num: true }, { key: "n", label: "Residui", num: true }, { key: "residui", label: "Elenco dei residui" },
    ],
    rows: pockets.map((p) => ({
      posto: p.rank, tasca: p.id, druggability: p.druggability ?? null, punteggio: p.score ?? null, volume: p.volume ?? null, sfere: p.alpha_spheres ?? null,
      raggio: p.mean_sphere_radius ?? null, idrofobicita: p.hydrophobicity_score ?? null, polarita: p.polarity_score ?? null, carica: p.charge_score ?? null,
      polari: p.polar_atoms_pct ?? null, sasa: p.sasa_total ?? null, sasa_polare: p.sasa_polar ?? null, sasa_apolare: p.sasa_apolar ?? null,
      flessibilita: p.flexibility ?? null, n: p.residues.length, residui: p.residues.map((r) => `${r.resname} ${r.chain}${r.resnum}`).join(" "),
    })),
  };
}

/** Campi del dettaglio di una tasca: descrittori di fpocket, superfici, centro, altri valori del programma. */
export function pocketDetail(p: Pocket): DetailItem[] {
  const out: DetailItem[] = [
    { label: "Druggability", value: p.druggability != null ? `${fmtNum(p.druggability, 3)} (${p.druggability >= DRUGGABLE ? "≥" : "<"} ${fmtNum(DRUGGABLE, 1)})` : "—" },
    { label: "Punteggio di fpocket", value: fmtNum(p.score ?? null, 3) },
    { label: "Volume", value: fmtUnit(p.volume, 0, "Å³") },
    { label: "Sfere alfa", value: p.alpha_spheres != null ? `${p.alpha_spheres}${p.mean_sphere_radius != null ? ` (raggio medio ${fmtNum(p.mean_sphere_radius, 2)} Å)` : ""}` : "—" },
    { label: "SASA totale", value: fmtUnit(p.sasa_total, 0, "Å²") },
    { label: "SASA polare / apolare", value: `${fmtNum(p.sasa_polar ?? null, 0)} / ${fmtNum(p.sasa_apolar ?? null, 0)} Å²` },
    { label: "Idrofobicità", value: fmtNum(p.hydrophobicity_score ?? null, 1) },
    { label: "Polarità · carica", value: `${p.polarity_score ?? "—"} · ${p.charge_score ?? "—"}` },
    { label: "Atomi polari", value: p.polar_atoms_pct != null ? `${fmtNum(p.polar_atoms_pct, 0)}%` : "—" },
    { label: "Flessibilità", value: fmtNum(p.flexibility ?? null, 2) },
    { label: "Atomi che la rivestono", value: String(p.atoms) },
    { label: "Centro (x, y, z)", value: p.center ? `${p.center.map((v) => fmtNum(v, 1)).join(", ")} Å` : "—" },
  ];
  for (const [k, v] of Object.entries(p.other ?? {})) if (v != null) out.push({ label: k, value: fmtNum(v, Number.isInteger(v) ? 0 : 3) });
  return out;
}

// ---------------------------------------------------------------------------------- potenziale elettrostatico
const residueTick = (r: { chain: string; resnum: string }, multi: boolean) => (multi ? `${r.chain}${r.resnum}` : r.resnum);
const manyChains = (rows: { chain: string }[]) => rows.some((r) => r.chain !== rows[0].chain);

/** Potenziale per residuo: rosso negativo, blu positivo come nel 3D; attenuate le barre entro la soglia dei residui
 *  marcati, nessuna barra per i residui sepolti. */
export function potentialSeries(result: Pick<ElectrostaticsResult, "residues" | "summary">): BarSeries {
  const multi = manyChains(result.residues);
  const t = result.summary.threshold;
  return {
    items: result.residues.map((r) => ({
      key: `${r.chain}:${r.resnum}`, label: `${r.resname} ${r.chain}${r.resnum}`, tick: residueTick(r, multi), value: r.potential,
      color: (r.potential ?? 0) < 0 ? "var(--err)" : "var(--info)", faded: r.potential == null || Math.abs(r.potential) <= t,
    })),
    label: "Potenziale di superficie", unit: "kT/e", digits: 2, symmetric: true,
    references: [
      { value: t, label: `+${fmtNum(t, 0)} kT/e`, color: "var(--info)" },
      { value: -t, label: `−${fmtNum(t, 0)} kT/e`, color: "var(--err)" },
    ],
  };
}

export function potentialData(result: Pick<ElectrostaticsResult, "residues" | "scale">): ChartData {
  const mv = result.scale?.mv_per_unit;
  return {
    columns: [
      { key: "residuo", label: "Residuo" }, { key: "catena", label: "Catena" }, { key: "numero", label: "Numero" },
      { key: "potenziale", label: "Potenziale (kT/e)", num: true }, { key: "mv", label: "Potenziale (mV)", num: true },
      { key: "carica", label: "Carica", num: true }, { key: "atomi", label: "Atomi esposti", num: true }, { key: "punti", label: "Punti di superficie", num: true },
    ],
    rows: result.residues.map((r) => ({
      residuo: r.resname, catena: r.chain, numero: r.resnum, potenziale: r.potential, mv: r.potential != null && mv ? Math.round(r.potential * mv) : null,
      carica: r.charge, atomi: r.exposed_atoms, punti: r.points,
    })),
  };
}

/** Campi del dettaglio di un residuo nel calcolo elettrostatico. */
export function potentialDetail(r: ElectrostaticsResidue, mvPerUnit: number, threshold: number): DetailItem[] {
  const v = r.potential;
  return [
    { label: "Potenziale", value: v == null ? "sepolto (nessun atomo esposto)" : `${fmtSigned(v, 2)} kT/e (${fmtSigned(Math.round(v * mvPerUnit), 0)} mV)` },
    { label: "Marcato", value: v == null ? "—" : Math.abs(v) > threshold ? `sì (|potenziale| > ${fmtNum(threshold, 0)} kT/e)` : "no" },
    { label: "Carica nel PQR", value: r.charge == null ? "—" : fmtSigned(r.charge, 2) },
    { label: "Atomi esposti", value: String(r.exposed_atoms) },
    { label: "Punti di superficie", value: String(r.points) },
  ];
}

// ---------------------------------------------------------------------------------- conservazione e interfacce
/** Conservazione 0–1 per residuo (barre dell'accento: l'altezza è il punteggio); senza barra i residui senza
 *  punteggio (troppi gap o non allineati). */
export function conservationSeries(cons: Pick<ConservationResult, "residues">): BarSeries {
  const multi = manyChains(cons.residues);
  return {
    items: cons.residues.map((r) => ({
      key: `${r.chain}:${r.resnum}`, label: `${r.chain}:${r.aa}${r.resnum}`, tick: residueTick(r, multi), value: r.score, color: "var(--accent)",
    })),
    label: "Conservazione (0–1)", digits: 2, domain: [0, 1], references: [],
  };
}

export function conservationData(cons: Pick<ConservationResult, "residues">): ChartData {
  return {
    columns: [
      { key: "catena", label: "Catena" }, { key: "numero", label: "Numero" }, { key: "aa", label: "Residuo" }, { key: "colonna", label: "Colonna", num: true },
      { key: "riga", label: "Nella riga" }, { key: "punteggio", label: "Conservazione", num: true }, { key: "entropia", label: "Entropia", num: true },
      { key: "gap", label: "Frazione di gap", num: true },
    ],
    rows: cons.residues.map((r) => ({
      catena: r.chain, numero: r.resnum, aa: r.aa, colonna: r.column, riga: r.row_aa ?? "", punteggio: r.score, entropia: r.entropy, gap: r.gap_fraction,
    })),
  };
}

/** ΔSASA dei residui d'interfaccia di una coppia di catene (prima una catena, poi l'altra), con i colori del 3D. */
export function interfaceSeries(pair: Pick<InterfacePair, "chains" | "residues">): BarSeries {
  const rows = pair.chains.flatMap((c) => [...(pair.residues[c] ?? [])].sort(compareResidues));
  return {
    items: rows.map((r) => ({
      key: `${r.chain}:${r.resnum}`, label: `${r.resname} ${r.chain}${r.resnum}`, tick: `${r.chain}${r.resnum}`, value: r.delta, color: colorFor(INTERFACE_SCALE, r.delta),
    })),
    label: "ΔSASA", unit: "Å²", digits: 1, references: [],
  };
}

export function interfaceData(pair: Pick<InterfacePair, "chains" | "residues">): ChartData {
  const rows = pair.chains.flatMap((c) => [...(pair.residues[c] ?? [])].sort(compareResidues));
  return {
    columns: [
      { key: "catena", label: "Catena" }, { key: "numero", label: "Numero" }, { key: "nome", label: "Residuo" },
      { key: "delta", label: "ΔSASA (Å²)", num: true }, { key: "apolare", label: "ΔSASA apolare (Å²)", num: true },
    ],
    rows: rows.map((r) => ({ catena: r.chain, numero: r.resnum, nome: r.resname, delta: r.delta, apolare: r.apolar_delta })),
  };
}

// ---------------------------------------------------------------------------------- interazioni
const HYDROGEN_TEXT: Record<string, string> = { explicit: "del file", placed: "posizionato" };
const deg = (v: number | null | undefined) => (v == null ? "—" : `${fmtNum(v, 0)}°`);
const ang = (v: number | null | undefined) => fmtUnit(v, 2, "Å");

/** Campi del dettaglio di un'interazione (tutti i valori geometrici del backend); null se la chiave non c'è. */
export function interactionDetail(result: AnalysisResult, kind: InteractionKind, key: string): DetailItem[] | null {
  const rows = interactionRows(result, kind);
  const i = rows.findIndex((r) => r.key === key);
  if (i < 0) return null;
  const row = rows[i];
  const inter = result.interactions;
  const chains = { label: "Fra catene diverse", value: yesNo(row.interChain) };
  switch (kind) {
    case "hbonds": {
      const h = inter.hbonds[i];
      return [
        { label: "Donatore", value: endLabel(h.a) }, { label: "Accettore", value: endLabel(h.b) }, { label: "Tipo", value: row.type },
        { label: "Distanza D…A", value: ang(h.distance) }, { label: "Distanza H…A", value: ang(h.ha_distance) }, { label: "Angolo D–H…A", value: deg(h.angle) },
        { label: "Angolo AA–D…A", value: deg(h.donor_angle) }, { label: "Angolo D…A–AA", value: deg(h.acceptor_angle) },
        { label: "Idrogeno", value: h.hydrogen ? HYDROGEN_TEXT[h.hydrogen] : "nessuno (gruppo che ruota)" }, chains,
      ];
    }
    case "salt_bridges": {
      const s = inter.salt_bridges[i];
      return [
        { label: "Catione", value: endLabel(s.a) }, { label: "Anione", value: endLabel(s.b) }, { label: "Distanza N–O minima", value: ang(s.distance) },
        { label: "Coppie N–O entro 4 Å", value: String(s.pairs) }, { label: "Istidina", value: s.his ? "sì (carica solo se protonata)" : "no" }, chains,
      ];
    }
    case "disulfides": {
      const d = inter.disulfides[i];
      return [
        { label: "Cisteina 1", value: endLabel(d.a) }, { label: "Cisteina 2", value: endLabel(d.b) }, { label: "Distanza SG–SG", value: ang(d.distance) },
        { label: "Distanza Cα–Cα", value: ang(d.ca_distance) }, { label: "χ3 (CB–SG–SG–CB)", value: deg(d.chi3) }, chains,
      ];
    }
    case "aromatic": {
      const e = inter.aromatic[i];
      const cation = e.type === "cation_pi";
      return [
        { label: cation ? "Catione" : "Anello 1", value: endLabel(e.a) }, { label: cation ? "Anello" : "Anello 2", value: endLabel(e.b) }, { label: "Tipo", value: row.type },
        { label: cation ? "Distanza catione–centroide" : "Distanza fra i centroidi", value: ang(e.distance) },
        { label: cation ? "Angolo con la normale" : "Angolo fra i piani", value: deg(e.angle) }, { label: "Spostamento", value: ang(e.offset) }, chains,
      ];
    }
  }
}

// ---------------------------------------------------------------------------------- confronto con una predizione
export type LddtMetric = "lddt" | "plddt";

/** Accordo per residuo con la predizione: lDDT (accento; arancione dove diverge e la predizione è sicura, grigio dove
 *  diverge ma la predizione è incerta o vicino a una giunzione) oppure il pLDDT con i colori di AlphaFold DB. */
export function lddtSeries(p: Pick<Plausibility, "residues" | "thresholds">, metric: LddtMetric): BarSeries {
  const multi = manyChains(p.residues);
  const t = p.thresholds ?? {};
  if (metric === "plddt") {
    return {
      items: p.residues.map((r) => ({ key: `${r.chain}:${r.resnum}`, label: `Residuo ${r.chain}${r.resnum}`, tick: residueTick(r, multi), value: r.plddt, color: colorFor(PLDDT_SCALE, r.plddt) })),
      label: "pLDDT della predizione", digits: 1, domain: [0, 100],
      references: references([[t.plddt_confident ?? 70, "predizione sicura", "var(--ok)"]]),
    };
  }
  return {
    items: p.residues.map((r) => ({
      key: `${r.chain}:${r.resnum}`, label: `Residuo ${r.chain}${r.resnum}`, tick: residueTick(r, multi), value: r.lddt,
      color: r.junction || (r.divergent && !r.confident) ? "var(--coil)" : r.divergent ? "var(--warn)" : "var(--accent)",
    })),
    label: "lDDT-Cα rispetto alla predizione", digits: 2, domain: [0, 1],
    references: references([[t.lddt_divergent ?? 0.5, "diverge sotto", "var(--warn)"]]),
  };
}

/** "molto alta 82% · confidente 12% · bassa 6%": frazioni dei residui per fascia di pLDDT (vuote omesse). */
export function plddtFractionsText(fractions: Record<string, number>): string {
  return (Object.keys(PLDDT_BAND_LABEL) as PlddtBand[])
    .filter((k) => (fractions[k] ?? 0) > 0)
    .map((k) => `${PLDDT_BAND_LABEL[k]} ${fmtNum(fractions[k] * 100, 0)}%`)
    .join(" · ");
}

export function lddtData(p: Pick<Plausibility, "residues">): ChartData {
  return {
    columns: [
      { key: "catena", label: "Catena" }, { key: "numero", label: "Numero" }, { key: "lddt", label: "lDDT", num: true }, { key: "plddt", label: "pLDDT", num: true },
      { key: "diverge", label: "Diverge" }, { key: "sicura", label: "Predizione sicura" }, { key: "giunzione", label: "Vicino a una giunzione" },
    ],
    rows: p.residues.map((r) => ({
      catena: r.chain, numero: r.resnum, lddt: r.lddt, plddt: r.plddt, diverge: yesNo(r.divergent), sicura: yesNo(r.confident), giunzione: yesNo(!!r.junction),
    })),
  };
}

// ---------------------------------------------------------------------------------- job
/** Tempi di un job leggibili: creazione, attesa in coda, avvio, durata (in corso o finale), fine. */
export function jobTimes(s: Pick<JobSummary, "status" | "created_at" | "started_at" | "finished_at">): DetailItem[] {
  const running = isActive(s.status);
  const out: DetailItem[] = [{ label: "Creato", value: fmtDate(s.created_at) }];
  if (s.started_at) {
    out.push({ label: "Avviato", value: fmtDate(s.started_at) });
    const wait = fmtDuration(s.created_at, s.started_at);
    if (wait !== "0 s") out.push({ label: "In coda per", value: wait });
    out.push({ label: running ? "In corso da" : "Durata", value: fmtDuration(s.started_at, s.finished_at) });
  } else if (running) {
    out.push({ label: "In coda da", value: fmtDuration(s.created_at, null) });
  }
  if (s.finished_at && !running) out.push({ label: "Terminato", value: fmtDate(s.finished_at) });
  return out;
}

/** Righe del log (l'ultima riga senza a capo conta). */
export function logLineCount(log: string): number {
  if (!log) return 0;
  let n = 0;
  for (let i = 0; i < log.length; i++) if (log.charCodeAt(i) === 10) n++;
  return log.endsWith("\n") ? n : n + 1;
}
