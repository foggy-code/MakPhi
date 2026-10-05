// Analisi strutturale (F7): righe delle tabelle delle interazioni, filtri per catena e per residuo, residui da
// evidenziare nel 3D, colorazioni per conservazione e per interfaccia, richiesta dell'allineamento. Logica pura.
//
// Convenzioni del backend (domain/interactions.py, domain/interfaces.py, domain/conservation.py): ogni interazione ha
// due estremità `a` e `b` (catena, residuo, atomo o anello); nei legami idrogeno a = donatore e b = accettore, nei ponti
// salini e nel catione-π a = catione. Un residuo d'interfaccia ha ΔSASA > 1 Å²; la conservazione va da 0 a 1 (null oltre
// il 30% di gap o per un residuo non allineato alla riga).

import type {
  AnalysisRequest, AnalysisResult, ConservationResult, InteractionEnd, InterfacePair, InterfacesResult, ParsedFile, PlausibilityLevel, ResidueRow,
} from "../api/types";
import { fmtScaleValue, type BinnedScale, type ContinuousScale } from "./colorscale";
import { plural } from "./format";
import { resiNumber, type ResidueColoring, type ResidueRef } from "./residueColoring";

export type InteractionKind = "hbonds" | "salt_bridges" | "disulfides" | "aromatic";

export const INTERACTION_LABEL: Record<InteractionKind, string> = {
  hbonds: "Legami idrogeno",
  salt_bridges: "Ponti salini",
  disulfides: "Ponti disolfuro",
  aromatic: "Aromatiche",
};

const HBOND_KIND: Record<string, string> = { backbone: "scheletro–scheletro", mixed: "scheletro–catena laterale", sidechain: "catene laterali" };
const AROMATIC_TYPE: Record<string, string> = { pi_parallel: "π-stacking parallelo", pi_tshaped: "π-stacking a T", cation_pi: "catione-π" };

/** Riga uniforme delle tabelle delle interazioni. */
export interface InteractionRow {
  /** chiave stabile (selezione) */
  key: string;
  kind: InteractionKind;
  a: InteractionEnd;
  b: InteractionEnd;
  /** Å: D…A, N–O più vicini, SG–SG, centroidi */
  distance: number;
  /** tipo leggibile ("scheletro–scheletro", "π-stacking a T"…) */
  type: string;
  /** valori geometrici del criterio, leggibili */
  detail: string;
  interChain: boolean;
}

const deg = (v: number) => `${fmtScaleValue(v, 0)}°`;
const ang = (v: number, digits = 2) => `${fmtScaleValue(v, digits)} Å`;

function rowKey(kind: InteractionKind, a: InteractionEnd, b: InteractionEnd, extra = ""): string {
  return `${kind}${extra}:${a.chain}${a.resnum}${a.atom}-${b.chain}${b.resnum}${b.atom}`;
}

/** Righe di un tipo di interazione, nell'ordine del backend (ordine dei residui nel file). */
export function interactionRows(result: AnalysisResult | null | undefined, kind: InteractionKind): InteractionRow[] {
  const inter = result?.interactions;
  if (!inter) return [];
  switch (kind) {
    case "hbonds":
      return inter.hbonds.map((h) => ({
        key: rowKey(kind, h.a, h.b), kind, a: h.a, b: h.b, distance: h.distance, interChain: h.inter_chain,
        type: HBOND_KIND[h.kind] ?? h.kind,
        detail: h.angle != null && h.ha_distance != null
          ? `H…A ${ang(h.ha_distance)} · D–H…A ${deg(h.angle)}${h.hydrogen === "placed" ? " (H posizionato)" : ""}`
          : `senza H: AA–D…A ${h.donor_angle != null ? deg(h.donor_angle) : "—"}`,
      }));
    case "salt_bridges":
      return inter.salt_bridges.map((s) => ({
        key: rowKey(kind, s.a, s.b), kind, a: s.a, b: s.b, distance: s.distance, interChain: s.inter_chain,
        type: s.his ? "con istidina" : "ionico",
        detail: `${plural(s.pairs, "coppia", "coppie")} N–O entro 4 Å${s.his ? " · His carica solo se protonata" : ""}`,
      }));
    case "disulfides":
      return inter.disulfides.map((d) => ({
        key: rowKey(kind, d.a, d.b), kind, a: d.a, b: d.b, distance: d.distance, interChain: d.inter_chain,
        type: "S–S", detail: `Cα–Cα ${ang(d.ca_distance)}${d.chi3 != null ? ` · χ3 ${deg(d.chi3)}` : ""}`,
      }));
    case "aromatic":
      return inter.aromatic.map((e) => ({
        key: rowKey(kind, e.a, e.b, e.type), kind, a: e.a, b: e.b, distance: e.distance, interChain: e.inter_chain,
        type: AROMATIC_TYPE[e.type] ?? e.type,
        detail: `${e.type === "cation_pi" ? "angolo con la normale" : "angolo fra i piani"} ${deg(e.angle)} · spostamento ${ang(e.offset)}`,
      }));
  }
}

/** Numero di interazioni per tipo (etichette delle schede). */
export function interactionCounts(result: AnalysisResult | null | undefined): Record<InteractionKind, number> {
  const i = result?.interactions;
  return { hbonds: i?.hbonds.length ?? 0, salt_bridges: i?.salt_bridges.length ?? 0, disulfides: i?.disulfides.length ?? 0, aromatic: i?.aromatic.length ?? 0 };
}

// ---------------------------------------------------------------------------------- filtri
/** Valore del filtro per catena: "all", "inter" (solo fra catene diverse) oppure "chain:<id>". */
export type ChainFilter = string;

export function chainFilterOptions(chains: string[]): { value: ChainFilter; label: string }[] {
  const out = [{ value: "all", label: "Tutte le catene" }];
  if (chains.length > 1) out.push({ value: "inter", label: "Solo fra catene diverse" });
  return out.concat(chains.map((c) => ({ value: `chain:${c}`, label: `Catena ${c}` })));
}

const THREE_TO_ONE: Record<string, string> = {
  ALA: "A", ARG: "R", ASN: "N", ASP: "D", CYS: "C", GLN: "Q", GLU: "E", GLY: "G", HIS: "H", ILE: "I",
  LEU: "L", LYS: "K", MET: "M", PHE: "F", PRO: "P", SER: "S", THR: "T", TRP: "W", TYR: "Y", VAL: "V",
};

export interface ResidueQuery {
  /** catena esplicita ("A:27") */
  chain?: string;
  /** nome a tre lettere ("LYS27") */
  name?: string;
  /** una lettera ("K27", "A27"): vale sia come catena sia come codice dell'amminoacido */
  letter?: string;
  resnum?: string;
  /** testo libero (senza numero): cercato in nome, residuo e atomo */
  text?: string;
}

/**
 * Filtro per residuo: "27", "A27" o "K27" (una lettera: catena oppure amminoacido), "LYS27", "A:27", "A:LYS27",
 * "27A" (codice di inserzione); senza numero, testo libero ("lys", "NZ"). Maiuscole e spazi indifferenti.
 */
export function parseResidueQuery(input: string): ResidueQuery | null {
  const text = input.trim().toUpperCase().replace(/\s+/g, "");
  if (!text) return null;
  const m = /^(?:([A-Z0-9]{1,4}):)?([A-Z]{3}|[A-Z])?(-?\d+[A-Z]?)$/.exec(text);
  if (!m) return { text };
  const [, chain, prefix, resnum] = m;
  const q: ResidueQuery = { resnum };
  if (chain) q.chain = chain;
  if (prefix?.length === 3) q.name = prefix;
  else if (prefix) q.letter = prefix;
  return q;
}

export function endMatches(end: InteractionEnd, q: ResidueQuery): boolean {
  if (q.text !== undefined) return `${end.resname} ${end.chain}${end.resnum} ${end.atom}`.toUpperCase().includes(q.text);
  if (q.resnum !== undefined && end.resnum.toUpperCase() !== q.resnum) return false;
  if (q.chain !== undefined && end.chain.toUpperCase() !== q.chain) return false;
  if (q.name !== undefined && end.resname.toUpperCase() !== q.name) return false;
  if (q.letter !== undefined && end.chain.toUpperCase() !== q.letter && THREE_TO_ONE[end.resname.toUpperCase()] !== q.letter) return false;
  return true;
}

/** Righe che passano il filtro per catena e quello per residuo (su una delle due estremità). */
export function filterRows(rows: InteractionRow[], chain: ChainFilter, residue: string): InteractionRow[] {
  const q = parseResidueQuery(residue);
  return rows.filter((r) => {
    if (chain === "inter" && !r.interChain) return false;
    if (chain.startsWith("chain:")) {
      const c = chain.slice(6);
      if (r.a.chain !== c && r.b.chain !== c) return false;
    }
    return !q || endMatches(r.a, q) || endMatches(r.b, q);
  });
}

/** Testo di un residuo nel filtro, da un clic nel 3D: "A:27" (con la catena, senza ambiguità). */
export function residueFilterText(ref: ResidueRef): string {
  return `${ref.chain}:${ref.resnum}`;
}

// ---------------------------------------------------------------------------------- evidenza nel 3D
export function endLabel(e: InteractionEnd, withAtom = true): string {
  return `${e.resname} ${e.chain}${e.resnum}${withAtom && e.atom ? ` ${e.atom}` : ""}`;
}

/** I due residui di un'interazione da evidenziare (uno solo se coincidono). */
export function highlightFor(row: { a: InteractionEnd; b: InteractionEnd }): { chain: string; resi: number }[] {
  const out = [{ chain: row.a.chain, resi: resiNumber(row.a.resnum) }];
  if (row.b.chain !== row.a.chain || row.b.resnum !== row.a.resnum) out.push({ chain: row.b.chain, resi: resiNumber(row.b.resnum) });
  return out.filter((h) => Number.isFinite(h.resi));
}

/** Residui d'interfaccia di una coppia di catene (entrambi i lati), da evidenziare. */
export function interfaceHighlight(pair: InterfacePair): { chain: string; resi: number }[] {
  return pair.chains.flatMap((c) => (pair.residues[c] ?? []).map((r) => ({ chain: c, resi: resiNumber(r.resnum) })));
}

export const PLAUSIBILITY_TONE: Record<PlausibilityLevel, "ok" | "warn" | "err"> = { compatibile: "ok", incerta: "warn", bassa: "err" };

// ---------------------------------------------------------------------------------- colorazioni
/**
 * Conservazione 0–1 con la tavolozza di ConSurf (Ashkenazy et al. 2016: turchese variabile → bianco → bordeaux
 * conservato), qui su una scala assoluta e continua invece che sulle 9 classi relative di ConSurf.
 */
export const CONSERVATION_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: [
    { value: 0, color: "#10c8d1" },
    { value: 0.25, color: "#d7ffff" },
    { value: 0.5, color: "#ffffff" },
    { value: 0.75, color: "#fac9de" },
    { value: 1, color: "#a02560" },
  ],
  ticks: [{ value: 0, label: "0 variabile" }, { value: 0.5, label: "0,5" }, { value: 1, label: "1 conservato" }],
  digits: 2,
};

/** ΔSASA per residuo: oltre 1 Å² il residuo è d'interfaccia (soglia del backend, estremo escluso). */
export const INTERFACE_SCALE: BinnedScale = {
  kind: "binned",
  bins: [
    { gt: 40, color: "#b91c1c", label: "ΔSASA > 40 Å²" },
    { gt: 10, color: "#f97316", label: "10–40 Å²" },
    { gt: 1, color: "#facc15", label: "1–10 Å²" },
    { color: "#cbd5e1", label: "≤ 1 Å² (fuori dall'interfaccia)" },
  ],
  digits: 1,
  unit: "Å²",
};

export type AnalysisColorBy = "none" | "interface" | "conservation";

export function analysisColorOptions(result: AnalysisResult | null | undefined): { value: AnalysisColorBy; label: string; available: boolean; note?: string }[] {
  const pairs = result?.interfaces.pairs.length ?? 0;
  return [
    { value: "none", label: "Schemi del viewer", available: true },
    { value: "interface", label: "Residui d'interfaccia (ΔSASA)", available: pairs > 0, note: "nessuna interfaccia fra catene" },
    { value: "conservation", label: "Conservazione", available: !!result?.conservation?.residues.length, note: "serve un allineamento multiplo" },
  ];
}

export function conservationColoring(cons: ConservationResult | null | undefined): ResidueColoring | null {
  if (!cons?.residues.length) return null;
  return {
    key: "conservation",
    title: "Conservazione (1 − entropia normalizzata, penalità dei gap)",
    values: cons.residues.map((r) => ({
      chain: r.chain, resnum: r.resnum, value: r.score,
      note: r.column == null ? "non allineato alla riga" : r.score == null ? `colonna ${r.column}: oltre il ${Math.round(cons.gap_cutoff * 100)}% di gap` : `colonna ${r.column}${r.row_aa && r.row_aa !== r.aa ? `, ${r.row_aa} nella riga` : ""}`,
    })),
    scale: CONSERVATION_SCALE,
    describe: (v) => (typeof v === "number" ? fmtScaleValue(v, 2) : "—"),
  };
}

export function interfaceColoring(iface: InterfacesResult | null | undefined): ResidueColoring | null {
  if (!iface?.pairs.length || !iface.residues.length) return null;
  return {
    key: "interface",
    title: "Superficie sepolta nel complesso (ΔSASA)",
    values: iface.residues.map((r) => ({ chain: r.chain, resnum: r.resnum, value: r.delta })),
    scale: INTERFACE_SCALE,
    describe: (v) => (typeof v === "number" ? `ΔSASA ${fmtScaleValue(v, 1, "Å²")}${v > iface.min_delta ? " · interfaccia" : ""}` : "—"),
  };
}

/** Residui per la striscia della conservazione (`ResidueStrip` vuole righe del rapporto di qualità: qui servono solo
 *  catena, numero e nome, gli altri campi sono vuoti). */
export function conservationStripRows(cons: ConservationResult | null | undefined): ResidueRow[] {
  return (cons?.residues ?? []).map((r) => ({
    chain: r.chain, resnum: r.resnum, resname: r.aa, aa: r.aa, ss: "-", ss3: "C", rsa: null, bfactor: null, phi: null, psi: null,
    rama: null, clashes: 0, dope: null,
  }));
}

export function analysisColoring(result: AnalysisResult | null | undefined, by: AnalysisColorBy): ResidueColoring | null {
  if (by === "interface") return interfaceColoring(result?.interfaces);
  if (by === "conservation") return conservationColoring(result?.conservation);
  return null;
}

// ---------------------------------------------------------------------------------- allineamento per la conservazione
export type MsaMode = "none" | "job" | "text";

/** Stato della scheda Interazioni conservato dalla pagina Qualità, che smonta il pannello cambiando scheda: job
 *  seguito (anche in coda o in corso) e scelte dell'allineamento, per la struttura `sourceKey`. */
export interface AnalysisPanelMemory {
  sourceKey: string;
  jobId: string | null;
  msaMode: MsaMode;
  msaJob: string;
  msaText: string;
  row: string;
}

/** Stato iniziale del pannello: quello conservato per la stessa struttura, altrimenti vuoto; `initialJob` (link a un
 *  job) vale sempre. */
export function initialPanelState(sourceKey: string, memory: AnalysisPanelMemory | null | undefined, initialJob?: string | null): AnalysisPanelMemory {
  const kept = memory && memory.sourceKey === sourceKey ? memory : null;
  return {
    sourceKey, jobId: initialJob ?? kept?.jobId ?? null, msaMode: kept?.msaMode ?? "none", msaJob: kept?.msaJob ?? "",
    msaText: kept?.msaText ?? "", row: kept?.row ?? "",
  };
}

/** Job da seguire dopo la ricerca dell'analisi più recente: quello già seguito, poi quello in coda o in corso sulla
 *  stessa struttura (lanciato dopo l'ultima completata), poi l'ultimo completato. */
export function followedAnalysis(current: string | null, latest: { job_id: string | null; active_job_id?: string | null }): string | null {
  return current ?? latest.active_job_id ?? latest.job_id ?? null;
}

/** Allineamento della richiesta, oppure il motivo per cui non si può ancora lanciare. */
export function msaRequest(mode: MsaMode, jobId: string, text: string, row: string): { msa: AnalysisRequest["msa"]; error: string | null } {
  const r = row.trim() || undefined;
  if (mode === "job") return jobId ? { msa: { job_id: jobId, row: r }, error: null } : { msa: null, error: "Scegli un job di allineamento." };
  if (mode === "text") {
    const records = (text.match(/^>/gm) ?? []).length;
    if (!text.trim()) return { msa: null, error: "Incolla o carica l'allineamento (o le sequenze)." };
    if (text.trim().startsWith(">") && records < 2) return { msa: null, error: "Servono almeno due sequenze." };
    return { msa: { text, row: r }, error: null };
  }
  return { msa: null, error: null };
}

/** FASTA da un file letto dal server (`/api/files/sequences`): le righe allineate se ci sono, con i gap. */
export function fastaFromParsed(parsed: ParsedFile): string {
  return parsed.records.map((r) => `>${r.name.replace(/\s+/g, "_") || "seq"}\n${r.aligned ?? r.sequence}\n`).join("");
}
