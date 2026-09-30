// Ligandi, cofattori e metalli (F5.4): scelta dei gruppi da trasferire nella modellazione e presentazione della
// verifica (domain/ligands.py). Logica pura, testata in ligands.test.ts.
import type { HeteroGroup, LigandClass, LigandGroupCheck, LigandRef, LigandStatus, MetalSite, TemplateAnalysis, TemplateRef } from "../api/types";
import { fmtNum } from "./format";

export const CLASS_LABEL: Record<LigandClass, string> = {
  metal: "metallo",
  cofactor: "cofattore noto",
  additive: "additivo",
  ligand: "altro ligando",
  modified: "residuo modificato",
};

export const CLASS_TONE: Record<LigandClass, "accent" | "info" | "warn" | undefined> = {
  metal: "accent",
  cofactor: "info",
  additive: "warn",
  ligand: undefined,
  modified: undefined,
};

export const STATUS_TONE: Record<LigandStatus, "ok" | "warn" | "err"> = { buono: "ok", "da controllare": "warn", problematico: "err" };

/** Classe di un gruppo; le analisi in cache delle versioni precedenti hanno solo `additive`. */
export function groupClass(g: HeteroGroup): LigandClass {
  return g.class ?? (g.additive ? "additive" : "ligand");
}

/** Scelto per default: come il backend, niente additivi, ioni monovalenti e residui modificati. */
export function isDefault(g: HeteroGroup): boolean {
  return g.default ?? !g.additive;
}

export function isTransferable(g: HeteroGroup): boolean {
  return g.transferable ?? true;
}

export function groupTitle(g: { resname: string; chain: string; resnum: string }): string {
  return `${g.resname} ${g.chain}${g.resnum}`;
}

export function ligandKey(template: string, g: { chain: string; resnum: string; resname: string }): string {
  return `${template}|${g.chain}|${g.resnum}|${g.resname}`;
}

export interface LigandCandidate {
  key: string;
  template: string;
  group: HeteroGroup;
}

/** Candidati di un template: gruppi delle catene scelte, nell'ordine del file (i residui modificati restano, non
 *  selezionabili, perché l'utente veda perché non si trasferiscono). */
export function candidateGroups(ref: TemplateRef, analysis: TemplateAnalysis | null | undefined): LigandCandidate[] {
  if (!analysis) return [];
  const chains = new Set(ref.chains);
  return analysis.ligands.filter((g) => chains.has(g.chain)).map((g) => ({ key: ligandKey(ref.id, g), template: ref.id, group: g }));
}

/** Selezione aggiornata ai candidati: i nuovi con la scelta predefinita, quelli già visti come li ha lasciati
 *  l'utente, quelli spariti (template o catena tolti) fuori. */
export function syncSelection(prev: Record<string, boolean>, candidates: LigandCandidate[]): Record<string, boolean> {
  const next: Record<string, boolean> = {};
  for (const c of candidates) {
    if (!isTransferable(c.group)) continue;
    next[c.key] = c.key in prev ? prev[c.key] : isDefault(c.group);
  }
  return next;
}

export function sameSelection(a: Record<string, boolean>, b: Record<string, boolean>): boolean {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => k in b && a[k] === b[k]);
}

/** Opzione `ligands` della modellazione: i gruppi scelti con il template da cui trasferirli. */
export function ligandOptions(candidates: LigandCandidate[], selection: Record<string, boolean>): LigandRef[] {
  return candidates
    .filter((c) => isTransferable(c.group) && selection[c.key])
    .map((c) => ({ template: c.template, chain: c.group.chain, resnum: c.group.resnum, resname: c.group.resname }));
}

/** Riepilogo della scelta: "2 di 4 gruppi scelti (additivi esclusi)". */
export function selectionSummary(candidates: LigandCandidate[], selection: Record<string, boolean>): string {
  const usable = candidates.filter((c) => isTransferable(c.group));
  const chosen = usable.filter((c) => selection[c.key]).length;
  if (!usable.length) return "Nessun ligando, cofattore o metallo nelle catene scelte.";
  const left = usable.filter((c) => !selection[c.key] && !isDefault(c.group)).length;
  return `${chosen} di ${usable.length} ${usable.length === 1 ? "gruppo scelto" : "gruppi scelti"}${left ? " (additivi e ioni del tampone esclusi per default)" : ""}.`;
}

// ---------------------------------------------------------------------------------- verifica
/** "Zn · 3 donatori · piramide a base quadrata (gRMSD 14,1°)" */
export function coordinationText(m: MetalSite): string {
  const parts = [m.symbol, `${m.coordination_number} ${m.coordination_number === 1 ? "donatore" : "donatori"}`];
  if (m.geometry) parts.push(`${m.geometry.name} (gRMSD ${fmtNum(m.geometry.grmsd, 1)}°)`);
  return parts.join(" · ");
}

/** "His A68 NE2 2,10 Å (attesa 2,03)" */
export function donorText(d: MetalSite["donors"][number]): string {
  return `${d.label} ${fmtNum(d.distance, 2)} Å${d.expected != null ? ` (attesa ${fmtNum(d.expected, 2)})` : ""}`;
}

export const DONOR_COLOR = { ok: "#22c55e", warn: "#f59e0b", err: "#ef4444" } as const;

/** Colore della linea di coordinazione per l'esito della distanza del donatore. */
export function donorColor(status: MetalSite["donors"][number]["status"]): string {
  if (status === "nella norma") return DONOR_COLOR.ok;
  if (status === "troppo corta") return DONOR_COLOR.err;
  return DONOR_COLOR.warn;
}

export interface CoordinationLine {
  start: [number, number, number];
  end: [number, number, number];
  color: string;
  label: string;
  /** donatore che il viewer non disegna (acqua): sfera alla fine della linea */
  marker: boolean;
}

/** Linee metallo → donatore per il viewer 3D (una per donatore di ogni metallo del gruppo). */
export function coordinationLines(group: LigandGroupCheck): CoordinationLine[] {
  return group.metals.flatMap((m) =>
    m.donors.map((d) => ({
      start: m.coord, end: d.coord, color: donorColor(d.status), label: `${m.symbol}–${d.label} ${fmtNum(d.distance, 2)} Å`, marker: d.water,
    })),
  );
}

/** Residui da evidenziare nel 3D: il gruppo e i residui (non acqua) dei donatori e degli scontri. */
export function ligandFocus(group: LigandGroupCheck): { chain: string; resi: number }[] {
  const out: { chain: string; resi: number }[] = [];
  const add = (chain: string, resnum: string) => {
    const resi = parseInt(resnum, 10);
    if (!Number.isNaN(resi) && !out.some((r) => r.chain === chain && r.resi === resi)) out.push({ chain, resi });
  };
  add(group.chain, group.resnum);
  for (const m of group.metals) for (const d of m.donors) if (!d.water) add(d.chain, d.resnum);
  for (const c of group.clashes) {
    // partner come "Leu A43 CB": catena e numero nella seconda parola
    const match = /^\S+ ([A-Za-z0-9]+?)(-?\d+)[A-Za-z]? /.exec(c.partner);
    if (match) add(match[1], match[2]);
  }
  return out;
}
