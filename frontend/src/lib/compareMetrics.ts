// Lettura delle metriche (pagine Confronto e Allineamenti): cosa misurano, soglie interpretative e toni delle
// statistiche, dettagli di una coppia allineata e di un tratto che devia, intervallo visibile del profilo.
// Logica pura e testata; le metriche le calcola il backend (domain/comparison.py, domain/sequences.py).

import type { StructuralPair } from "../api/types";
import { TWILIGHT_IDENTITY } from "./alignConfidence";
import { AMINO, residueClass } from "./aminoAcids";
import { DISTANCE_MAX, type DeviatingSegment } from "./compare";
import type { Tone } from "./format";
import { resiNumber } from "./residueColoring";

// tono e valore con unità sono comuni (lib/format); classi chimiche in lib/aminoAcids: riesportati per chi li prende da qui
export { fmtUnit, type Tone } from "./format";
export { RESIDUE_CLASSES, residueClass } from "./aminoAcids";

export interface Threshold {
  range: string;
  reading: string;
  tone: Tone;
}

export interface MetricGuide {
  name: string;
  /** scala e soglia principale in poche parole: il riepilogo della sezione chiusa (guida della pagina Confronto) */
  brief: string;
  /** cosa misura e come si calcola */
  what: string;
  /** soglie di lettura, dalla migliore */
  thresholds: Threshold[];
  source: string;
}

export type MetricKey = "tm" | "rmsd" | "gdt" | "lddt" | "identity";

/** Spiegazioni e soglie (indicative: vanno lette insieme, e con la lunghezza allineata). */
export const METRIC_GUIDE: Record<MetricKey, MetricGuide> = {
  tm: {
    name: "TM-score",
    brief: "0–1: oltre 0,5 stesso ripiegamento, sotto 0,17 casuale",
    what: "Somiglianza della forma da 0 a 1 dopo la sovrapposizione che la massimizza: pesa di più le coppie vicine e dipende poco dalla lunghezza, quindi si confronta anche tra proteine diverse. Normalizzato sulla lunghezza di A (riferimento) o di B.",
    thresholds: [
      { range: "> 0,5", reading: "stesso ripiegamento", tone: "ok" },
      { range: "0,17–0,5", reading: "somiglianza debole", tone: "warn" },
      { range: "< 0,17", reading: "come due strutture casuali", tone: "err" },
    ],
    source: "Zhang & Skolnick 2004; soglia di 0,5: Xu & Zhang 2010",
  },
  rmsd: {
    name: "RMSD",
    brief: "Å sui Cα allineati: fino a 2 Å strutture molto simili",
    what: "Scarto quadratico medio tra i Cα delle coppie allineate dopo la loro sovrapposizione, in Å. Cresce con il numero di coppie e basta un tratto lontano ad alzarlo: si legge con il TM-score e con l'RMSD entro 5 Å (solo le coppie vicine, il nucleo comune).",
    thresholds: [
      { range: "≤ 2 Å", reading: "strutture molto simili", tone: "ok" },
      { range: "2–4 Å", reading: "stesso ripiegamento, differenze locali", tone: "info" },
      { range: "> 4 Å", reading: "differenze estese o domini spostati", tone: "warn" },
    ],
    source: "Kabsch 1976 (sovrapposizione); TM-align e US-align riportano lo stesso RMSD",
  },
  gdt: {
    name: "GDT-TS / GDT-HA",
    brief: "% dei Cα di A entro le soglie: da 50 ripiegamento corretto",
    what: "Percentuale dei Cα di A che stanno entro una soglia dal corrispondente di B, cercando la sovrapposizione migliore per ogni soglia. GDT-TS media le soglie 1, 2, 4 e 8 Å; GDT-HA (alta accuratezza) 0,5, 1, 2 e 4 Å ed è più severo.",
    thresholds: [
      { range: "≥ 80", reading: "alta accuratezza", tone: "ok" },
      { range: "50–80", reading: "ripiegamento corretto, differenze locali", tone: "ok" },
      { range: "30–50", reading: "somiglianza parziale", tone: "warn" },
      { range: "< 30", reading: "strutture diverse", tone: "err" },
    ],
    source: "Zemla 2003 (LGA); usato nelle valutazioni CASP",
  },
  lddt: {
    name: "lDDT",
    brief: "0–1, senza sovrapposizione: da 0,7 accordo buono",
    what: "Frazione delle distanze tra Cα di A entro 15 Å che B conserva entro 0,5, 1, 2 e 4 Å (media delle quattro). Non usa sovrapposizioni: misura l'accordo locale ed è poco sensibile ai movimenti di dominio. Anche per residuo.",
    thresholds: [
      { range: "≥ 0,9", reading: "accordo molto alto", tone: "ok" },
      { range: "0,7–0,9", reading: "accordo buono", tone: "ok" },
      { range: "0,5–0,7", reading: "accordo basso", tone: "warn" },
      { range: "< 0,5", reading: "il residuo o la regione diverge", tone: "err" },
    ],
    source: "Mariani et al. 2013; fasce come il pLDDT di AlphaFold",
  },
  identity: {
    name: "Identità di sequenza",
    brief: `da 30% modellazione affidabile, sotto ${TWILIGHT_IDENTITY}% zona crepuscolare`,
    what: "Residui identici sulle posizioni allineate senza gap. Per la modellazione per omologia è il primo indizio dell'accuratezza attesa del modello.",
    thresholds: [
      { range: "≥ 50%", reading: "modelli in genere accurati", tone: "ok" },
      { range: "30–50%", reading: "modellazione affidabile, controlla loop e inserzioni", tone: "ok" },
      { range: `${TWILIGHT_IDENTITY}–30%`, reading: "al limite: verifica l'allineamento", tone: "warn" },
      { range: `< ${TWILIGHT_IDENTITY}%`, reading: "zona crepuscolare: omologia incerta dalla sola sequenza", tone: "err" },
    ],
    source: "Rost 1999 (zona crepuscolare); soglie indicative",
  },
};

// ---------------------------------------------------------------------------------- toni delle statistiche
const missing = (v: number | null | undefined): v is null | undefined => v === null || v === undefined || Number.isNaN(v);

/** Come `tmVerdict`: oltre 0,5 stesso ripiegamento, sotto 0,17 casuale. */
export function tmTone(v: number | null | undefined): Tone | undefined {
  if (missing(v)) return undefined;
  return v > 0.5 ? "ok" : v >= 0.17 ? "warn" : "err";
}

/** Esito del TM-score a parole, per la statistica: la lettura della sua soglia (senza i numeri di `tmVerdict`). */
export function tmReading(v: number | null | undefined): string | undefined {
  const tone = tmTone(v);
  return tone && METRIC_GUIDE.tm.thresholds.find((t) => t.tone === tone)?.reading;
}

export function rmsdTone(v: number | null | undefined): Tone | undefined {
  if (missing(v)) return undefined;
  return v <= 2 ? "ok" : v <= 4 ? "info" : "warn";
}

/** GDT-TS in percentuale. */
export function gdtTone(v: number | null | undefined): Tone | undefined {
  if (missing(v)) return undefined;
  return v >= 50 ? "ok" : v >= 30 ? "warn" : "err";
}

/** lDDT da 0 a 1 (globale o per residuo). */
export function lddtTone(v: number | null | undefined): Tone | undefined {
  if (missing(v)) return undefined;
  return v >= 0.7 ? "ok" : v >= 0.5 ? "warn" : "err";
}

/** Identità in percentuale. */
export function identityTone(v: number | null | undefined): Tone | undefined {
  if (missing(v)) return undefined;
  return v >= 30 ? "ok" : v >= TWILIGHT_IDENTITY ? "warn" : "err";
}

/** Percentuale di `part` su `total`; null senza totale. */
export function percentOf(part: number | null | undefined, total: number | null | undefined): number | null {
  if (missing(part) || missing(total) || total <= 0) return null;
  return (100 * part) / total;
}

// ---------------------------------------------------------------------------------- lettura per coppia
/** Distanza Cα di una coppia dopo la sovrapposizione. */
export function distanceReading(d: number): { tone: Tone; text: string } {
  if (d < 1) return { tone: "ok", text: "coincidenti (< 1 Å)" };
  if (d < 2) return { tone: "ok", text: "vicine (1–2 Å)" };
  if (d < DISTANCE_MAX) return { tone: "warn", text: `spostate (2–${DISTANCE_MAX} Å)` };
  return { tone: "err", text: `lontane (≥ ${DISTANCE_MAX} Å)` };
}

/** lDDT di un residuo (fasce come il pLDDT). */
export function lddtReading(v: number | null): { tone: Tone; text: string } | null {
  if (missing(v)) return null;
  if (v >= 0.9) return { tone: "ok", text: "accordo molto alto" };
  if (v >= 0.7) return { tone: "ok", text: "accordo buono" };
  if (v >= 0.5) return { tone: "warn", text: "accordo basso" };
  return { tone: "err", text: "diverge" };
}

/** Residuo per esteso: "K (LYS)"; il codice a tre lettere solo per i 20 standard. */
export function residueText(aa: string): string {
  const three = AMINO[aa.toUpperCase()];
  return three ? `${aa} (${three})` : aa || "—";
}

/** Differenza tra i due residui di una coppia: identici, sostituzione nella stessa classe chimica o tra classi. */
export function residueChange(a: string, b: string): { kind: "identical" | "conservative" | "different"; text: string } {
  if (a.toUpperCase() === b.toUpperCase()) return { kind: "identical", text: "identici" };
  const ca = residueClass(a);
  const cb = residueClass(b);
  if (ca && ca === cb) return { kind: "conservative", text: `sostituzione conservativa (entrambi: ${ca.one})` };
  return { kind: "different", text: `sostituzione: ${ca?.one ?? a} → ${cb?.one ?? b}` };
}

/** Scarto tra le numerazioni dei due file: "stessa numerazione", "B = A + 2". */
export function numberingText(p: Pick<StructuralPair, "resnum_a" | "resnum_b">): string {
  const offset = resiNumber(p.resnum_b) - resiNumber(p.resnum_a);
  if (!Number.isFinite(offset)) return "—";
  if (offset === 0) return "stessa numerazione";
  return `B = A ${offset > 0 ? "+" : "−"} ${Math.abs(offset)}`;
}

/** Residuo di A o B come nelle etichette: "K48", con la catena davanti se richiesta ("A:K48"). */
export function pairResidue(p: StructuralPair, side: "a" | "b", withChain = false): string {
  const chain = side === "a" ? p.chain_a : p.chain_b;
  const label = side === "a" ? `${p.aa_a}${p.resnum_a}` : `${p.aa_b}${p.resnum_b}`;
  return withChain && chain ? `${chain}:${label}` : label;
}

/** Percentuale di coppie con lo stesso residuo (null senza coppie). */
export function pairIdentity(pairs: StructuralPair[]): number | null {
  if (!pairs.length) return null;
  return (100 * pairs.filter((p) => p.aa_a.toUpperCase() === p.aa_b.toUpperCase()).length) / pairs.length;
}

// ---------------------------------------------------------------------------------- tratti e intervallo visibile
export interface SegmentDetail {
  count: number;
  /** residui di B, estremi del tratto */
  fromB: string;
  toB: string;
  chainA: string;
  chainB: string;
  meanDistance: number;
  maxDistance: number;
  /** null se nessun residuo del tratto ha l'lDDT */
  meanLddt: number | null;
  /** coppie con residui diversi */
  differing: number;
}

export function segmentDetail(pairs: StructuralPair[], seg: Pick<DeviatingSegment, "start" | "end">): SegmentDetail {
  const part = pairs.slice(seg.start, seg.end + 1);
  const lddt = part.map((p) => p.lddt).filter((v): v is number => v !== null && Number.isFinite(v));
  const distances = part.map((p) => p.distance);
  return {
    count: part.length,
    fromB: part[0]?.resnum_b ?? "",
    toB: part[part.length - 1]?.resnum_b ?? "",
    chainA: part[0]?.chain_a ?? "",
    chainB: part[0]?.chain_b ?? "",
    meanDistance: distances.length ? distances.reduce((s, v) => s + v, 0) / distances.length : 0,
    maxDistance: distances.length ? Math.max(...distances) : 0,
    meanLddt: lddt.length ? lddt.reduce((s, v) => s + v, 0) / lddt.length : null,
    differing: part.filter((p) => p.aa_a.toUpperCase() !== p.aa_b.toUpperCase()).length,
  };
}

/** Intervallo visibile del profilo per la barra dello zoom: "A: 12–48 · 37 di 300 coppie" (coppie con il centro dentro). */
export function pairSpan(pairs: StructuralPair[], lo: number, hi: number): string {
  const n = pairs.length;
  const first = Math.max(0, Math.ceil(lo - 1e-6));
  const last = Math.min(n - 1, Math.floor(hi + 1e-6));
  if (!n || last < first) return `${n} coppie`;
  const multi = pairs.some((p) => p.chain_a !== pairs[0].chain_a);
  const at = (i: number) => (multi ? `${pairs[i].chain_a}${pairs[i].resnum_a}` : pairs[i].resnum_a);
  const count = last - first + 1;
  return `A: ${at(first)}–${at(last)} · ${count === n ? n : `${count} di ${n}`} coppie`;
}
