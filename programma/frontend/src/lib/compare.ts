// Confronto strutturale (pagina Confronto e modalità Strutturale di Allineamenti). Logica pura: sorgenti nei link,
// colorazione di B per distanza o lDDT, serie per posizione, tratti che deviano, ordinamento della tabella e link
// dalla tabella dei modelli.
//
// Convenzioni del backend (engines/align.structural, domain/comparison.py): A è il riferimento, B si sovrappone su A;
// nel PDB sovrapposto la catena A è la struttura A e la catena B la struttura B, qualunque fosse la catena originale.

import type { AlignResult, ModelEntry, PreparedTemplate, StructuralPair, StructureSource } from "../api/types";
import { fmtScaleValue, NO_VALUE_COLOR, REFERENCE_COLOR, type ContinuousScale } from "./colorscale";
import { resiNumber, type ResidueColoring, type ResidueRef, type ResidueValue } from "./residueColoring";

/** Distanza oltre la quale una coppia non è più "vicina": la soglia dei ":" di TM-align e dell'RMSD entro 5 Å. */
export const DISTANCE_MAX = 5;

/** Blu (coincidenti) → giallo → rosso (≥ 5 Å, saturato): nessun colore chiaro, che si confonderebbe con A (nel tema
 *  chiaro A è un'ardesia scura, più scura e meno satura degli estremi). */
export const DISTANCE_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: [
    { value: 0, color: "#2166ac" },
    { value: DISTANCE_MAX / 2, color: "#fee090" },
    { value: DISTANCE_MAX, color: "#b2182b" },
  ],
  ticks: [0, 1, 2, 3, 4].map((value) => ({ value, label: `${value}` })).concat([{ value: DISTANCE_MAX, label: `≥ ${DISTANCE_MAX} Å` }]),
  digits: 2,
  unit: "Å",
};

/** lDDT 0–1: rosso (distanze non conservate) → giallo → blu (conservate), come la scala delle distanze rovesciata. */
export const LDDT_SCALE: ContinuousScale = {
  kind: "continuous",
  stops: [
    { value: 0, color: "#b2182b" },
    { value: 0.5, color: "#fee090" },
    { value: 1, color: "#2166ac" },
  ],
  ticks: [{ value: 0 }, { value: 0.5 }, { value: 1 }],
  digits: 2,
};

/** Struttura A (riferimento) nel viewer: grigio chiaro, ardesia scura nel tema chiaro (`REFERENCE_COLOR_LIGHT` di
 *  colorscale.ts, dove sta con gli altri colori per tema). */
export { REFERENCE_COLOR };
/** Catene del PDB sovrapposto. */
export const CHAIN_A = "A";
export const CHAIN_B = "B";

export type CompareColorBy = "distance" | "lddt";

// ---------------------------------------------------------------------------------- sorgenti nei link
/** Sorgente come parametro dell'URL: "template:1UBQ", "job:<id>:<file>", "upload:<file>" (la catena va a parte). */
export function encodeSource(src: StructureSource | null | undefined): string | null {
  if (!src) return null;
  if (src.type === "template" && src.id) return `template:${src.id}`;
  if (src.type === "job" && src.job_id && src.file) return `job:${src.job_id}:${src.file}`;
  if (src.type === "upload" && src.file) return `upload:${src.file}`;
  return null;
}

/** Inverso di `encodeSource`; null se il testo non descrive una sorgente. `chain` vuota = prima catena. */
export function decodeSource(text: string | null | undefined, chain?: string | null): StructureSource | null {
  if (!text) return null;
  const i = text.indexOf(":");
  if (i <= 0) return null;
  const kind = text.slice(0, i);
  const rest = text.slice(i + 1);
  const withChain = (src: StructureSource): StructureSource => (chain?.trim() ? { ...src, chain: chain.trim() } : src);
  if (kind === "template" && rest) return withChain({ type: "template", id: rest });
  if (kind === "upload" && rest) return withChain({ type: "upload", file: rest });
  if (kind === "job") {
    const j = rest.indexOf(":");
    if (j > 0 && j < rest.length - 1) return withChain({ type: "job", job_id: rest.slice(0, j), file: rest.slice(j + 1) });
  }
  return null;
}

/** Parametri della pagina Confronto: a, ca (catena di A), b, cb, job (allineamento già calcolato). */
export function compareParams(a: StructureSource | null | undefined, b: StructureSource | null | undefined, job?: string | null): URLSearchParams {
  const params = new URLSearchParams();
  const ea = encodeSource(a);
  const eb = encodeSource(b);
  if (ea) params.set("a", ea);
  if (ea && a?.chain) params.set("ca", a.chain);
  if (eb) params.set("b", eb);
  if (eb && b?.chain) params.set("cb", b.chain);
  if (job) params.set("job", job);
  return params;
}

export function compareLink(a: StructureSource | null | undefined, b: StructureSource | null | undefined, job?: string | null): string {
  const q = compareParams(a, b, job).toString();
  return `/compare${q ? `?${q}` : ""}`;
}

/** Sorgenti precompilate dai parametri di un link. */
export function linkedSources(params: URLSearchParams): { a: StructureSource | null; b: StructureSource | null; job: string | null } {
  return { a: decodeSource(params.get("a"), params.get("ca")), b: decodeSource(params.get("b"), params.get("cb")), job: params.get("job") };
}

/** Le due strutture di un job di allineamento strutturale (dai suoi parametri), se valide. */
export function jobSources(params: Record<string, unknown> | null | undefined): [StructureSource, StructureSource] | null {
  const list = params?.structures;
  if (!Array.isArray(list) || list.length !== 2) return null;
  const clean = (s: unknown): StructureSource | null => {
    if (!s || typeof s !== "object") return null;
    const src = s as StructureSource;
    const decoded = decodeSource(encodeSource(src), src.chain ?? null);
    return decoded;
  };
  const a = clean(list[0]);
  const b = clean(list[1]);
  return a && b ? [a, b] : null;
}

/**
 * Cosa mostra la colonna del risultato nella pagina Confronto: "empty" senza job (invito a scegliere le strutture),
 * "missing" se il job del link non si è potuto caricare (eliminato, link vecchio: errore e invito a rilanciare con
 * «Confronta», le strutture del link restano scelte), altrimenti "job" (il pannello del job). Un errore dopo il primo
 * caricamento (rete, polling) non nasconde il job già mostrato.
 */
export function compareJobView(jobId: string | null | undefined, loaded: boolean, error: string | null | undefined): "empty" | "missing" | "job" {
  if (!jobId) return "empty";
  return !loaded && error ? "missing" : "job";
}

/** Dalla tabella dei modelli: con quali strutture confrontare un modello (template usati, altri modelli del job). */
export function compareTargets(jobId: string, model: string, models: ModelEntry[], templates: PreparedTemplate[] = []): { label: string; href: string }[] {
  const self: StructureSource = { type: "job", job_id: jobId, file: model };
  const out = templates.map((t) => ({
    label: `Template ${t.code}${t.predicted ? " (AlphaFold)" : ""}`,
    href: compareLink({ type: "template", id: t.id, chain: t.chains[0] }, self),
  }));
  for (const m of models) {
    if (m.file === model || m.failure) continue;
    out.push({ label: `${m.file}${m.best ? " (migliore)" : ""}`, href: compareLink({ type: "job", job_id: jobId, file: m.file }, self) });
  }
  return out;
}

// ---------------------------------------------------------------------------------- colorazione del viewer
/**
 * B colorata per distanza (o lDDT) della coppia, A in grigio chiaro (ardesia scura nel tema chiaro), residui di B senza
 * coppia in grigio medio.
 * I residui di A delle coppie hanno lo stesso valore (per il passaggio del mouse) ma il colore del riferimento.
 */
export function comparisonColoring(pairs: StructuralPair[], by: CompareColorBy = "distance"): ResidueColoring {
  const distance = by === "distance";
  const values: ResidueValue[] = [];
  for (const p of pairs) {
    const value = distance ? p.distance : p.lddt;
    values.push({ chain: CHAIN_B, resnum: p.resnum_b, value, note: `con ${p.aa_a}${p.resnum_a} di A` });
    values.push({ chain: CHAIN_A, resnum: p.resnum_a, value, note: `con ${p.aa_b}${p.resnum_b} di B`, color: REFERENCE_COLOR });
  }
  return {
    key: by,
    title: distance ? "Distanza Cα dopo la sovrapposizione (B)" : "lDDT per residuo (B, riferimento A)",
    values,
    scale: distance ? DISTANCE_SCALE : LDDT_SCALE,
    describe: (v) => (typeof v === "number" ? (distance ? fmtScaleValue(v, 2, "Å") : `lDDT ${fmtScaleValue(v, 2)}`) : "—"),
    chainColors: { [CHAIN_A]: REFERENCE_COLOR, [CHAIN_B]: NO_VALUE_COLOR },
    legendExtra: [
      { color: REFERENCE_COLOR, label: "A (riferimento)" },
      { color: NO_VALUE_COLOR, label: "B senza coppia" },
    ],
  };
}

/** I due residui di una coppia, evidenziati nel viewer. */
export function pairHighlight(p: StructuralPair): { chain: string; resi: number }[] {
  return [{ chain: CHAIN_A, resi: resiNumber(p.resnum_a) }, { chain: CHAIN_B, resi: resiNumber(p.resnum_b) }];
}

/** Coppia di un residuo scelto nel viewer (catena A o B del PDB sovrapposto); −1 se il residuo non ha coppia. */
export function pairIndexOf(pairs: StructuralPair[], ref: ResidueRef): number {
  if (ref.chain === CHAIN_A) return pairs.findIndex((p) => p.resnum_a === ref.resnum);
  if (ref.chain === CHAIN_B) return pairs.findIndex((p) => p.resnum_b === ref.resnum);
  return -1;
}

// ---------------------------------------------------------------------------------- serie per posizione
/** Interruzione prima della coppia i: catena di A diversa o residui di A saltati (senza coppia) nella numerazione. */
export function breaksBefore(pairs: StructuralPair[]): boolean[] {
  return pairs.map((p, i) => {
    if (i === 0) return false;
    const prev = pairs[i - 1];
    if (prev.chain_a !== p.chain_a) return true;
    return resiNumber(p.resnum_a) - resiNumber(prev.resnum_a) > 1;
  });
}

/** Tracciato SVG di una serie (una posizione per coppia), interrotto dove il valore manca o la numerazione salta. */
export function seriesPath(values: (number | null)[], breaks: boolean[], x: (i: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      pen = false;
      return;
    }
    d += `${pen && !breaks[i] ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

export interface DeviatingSegment {
  /** indici delle coppie, estremi compresi */
  start: number;
  end: number;
  /** numerazione di A */
  from: string;
  to: string;
  max: number;
  text: string;
}

/** Tratti di almeno `minLength` coppie consecutive (senza salti nella numerazione di A) con distanza ≥ `threshold` Å. */
export function deviatingSegments(pairs: StructuralPair[], threshold = 2, minLength = 3): DeviatingSegment[] {
  const breaks = breaksBefore(pairs);
  const out: DeviatingSegment[] = [];
  let start = -1;
  const close = (end: number) => {
    if (start >= 0 && end - start + 1 >= minLength) {
      const seg = pairs.slice(start, end + 1);
      const max = Math.max(...seg.map((p) => p.distance));
      const from = pairs[start].resnum_a;
      const to = pairs[end].resnum_a;
      out.push({ start, end, from, to, max, text: `${from}–${to}${pairs[start].chain_a ? ` (catena ${pairs[start].chain_a})` : ""}: fino a ${fmtScaleValue(max, 1, "Å")}` });
    }
    start = -1;
  };
  pairs.forEach((p, i) => {
    if (breaks[i]) close(i - 1);
    if (p.distance >= threshold) {
      if (start < 0) start = i;
    } else close(i - 1);
  });
  close(pairs.length - 1);
  return out;
}

// ---------------------------------------------------------------------------------- tabella
export type PairSort = "position" | "distance" | "lddt";

/** Indici delle coppie da mostrare: filtro sulla distanza minima e ordinamento (lDDT mancante in fondo). */
export function pairRows(pairs: StructuralPair[], sort: PairSort = "position", minDistance = 0): number[] {
  const rows = pairs.map((_, i) => i).filter((i) => pairs[i].distance >= minDistance);
  if (sort === "distance") rows.sort((i, j) => pairs[j].distance - pairs[i].distance || i - j);
  else if (sort === "lddt") rows.sort((i, j) => (pairs[i].lddt ?? Infinity) - (pairs[j].lddt ?? Infinity) || i - j);
  return rows;
}

/**
 * Scorrimento verticale (da aggiungere a `scrollTop`) del contenitore della tabella perché la riga selezionata sia
 * visibile sotto l'intestazione fissa: 0 se lo è già, altrimenti la porta al centro. Si scorre solo il contenitore,
 * mai la pagina (`scrollIntoView` scorrerebbe anche la finestra e porterebbe via il viewer 3D appena cliccato).
 * Rettangoli come `getBoundingClientRect`; `header` = altezza dell'intestazione fissa.
 */
export function rowScrollDelta(row: { top: number; bottom: number }, view: { top: number; bottom: number }, header = 0): number {
  const top = view.top + header;
  if (row.top >= top && row.bottom <= view.bottom) return 0;
  const visible = view.bottom - top;
  const height = row.bottom - row.top;
  return Math.round(row.top - top - (visible - height) / 2);
}

/**
 * Il risultato strutturale è di una versione precedente di MakPhi, senza confronto per residuo: niente coppie e
 * niente `comparison_version`. Non basta l'assenza di una nota: il metodo integrato ha sempre scritto la sua
 * ("Allineamento dipendente dalla sequenza…"), mentre un risultato nuovo senza coppie ha la versione e la sua nota.
 */
export function comparisonOutdated(result: Pick<AlignResult, "pairs" | "comparison_version">): boolean {
  return !result.pairs?.length && result.comparison_version == null;
}

/** Lettura del TM-score (Xu & Zhang, Bioinformatics 26:889, 2010; Zhang & Skolnick, Proteins 57:702, 2004). */
export function tmVerdict(tm: number | null | undefined): string {
  if (tm === null || tm === undefined) return "—";
  if (tm > 0.5) return "stesso ripiegamento (> 0,5)";
  if (tm < 0.17) return "come due strutture casuali (< 0,17)";
  return "somiglianza debole (0,17–0,5)";
}
