// Mutagenesi in silico e scansione di alanina (F9.2): notazione (stessa sintassi del backend, per un riscontro mentre
// si scrive), richieste e controlli preventivi, toni delle classi e dei segnali, colorazione del 3D per effetto stimato,
// varianti di UniProt, link a Qualità e Confronto. Logica pura e testata; protocollo, soglie e fonti nel backend
// (domain/mutations.py).

import type {
  AlaScanRequest, AlaScanResult, AlaScanRow, MappedVariant, MutateRequest, MutateResult, MutationClass, MutationDope,
  MutationEnergy, MutationHistoryItem, MutationSignalLevel, MutationSite, PreflightRequest, ScanMode, StructureSource, VariantMapping,
} from "../api/types";
import type { CategoricalScale } from "./colorscale";
import { compareLink } from "./compare";
import { fmtNum, fmtSigned, STATUS_LABEL } from "./format";
import { resiNumber, residueKey, type ResidueColoring, type ResidueValue } from "./residueColoring";

/** I 20 amminoacidi standard (lettera → codice a tre lettere). */
export const AMINO: Record<string, string> = {
  A: "ALA", C: "CYS", D: "ASP", E: "GLU", F: "PHE", G: "GLY", H: "HIS", I: "ILE", K: "LYS", L: "LEU", M: "MET", N: "ASN",
  P: "PRO", Q: "GLN", R: "ARG", S: "SER", T: "THR", V: "VAL", W: "TRP", Y: "TYR",
};
const THREE: Record<string, string> = Object.fromEntries(Object.entries(AMINO).map(([k, v]) => [v, k]));

/** Limiti come domain/mutations.py: sostituzioni per variante, residui per scansione (con l'energia di OpenMM). */
export const MAX_MUTATIONS = 10;
export const MAX_SCAN = 50;
export const MAX_SCAN_ENERGY = 20;
/** Catena senza identificativo nella notazione. */
export const BLANK = "_";

export interface ParsedMutation {
  /** null = omessa (la struttura deve avere una sola catena) */
  chain: string | null;
  resnum: string;
  wt: string;
  mut: string;
  token: string;
}

const TOKEN = /^(?:([A-Za-z0-9_]{1,4}):)?([A-Za-z]{1,3}-?\d+[A-Za-z]{1,4})$/;
const BODY = /^([A-Z]{3}|[A-Z])(-?\d+)([A-Z]{1,4})$/;

function amino(code: string): string | null {
  if (code.length === 3) return THREE[code] ?? null;
  return AMINO[code] ? code : null;
}

/**
 * Mutazioni dalla notazione `[catena:]<originale><numero>[inserzione]<nuovo>` (A:K48R, A:LYS48ARG, A:G52AA), separate da
 * virgole, punti e virgola o spazi; come il backend la parte dopo il numero si legge dalla lunghezza (1 = residuo, 2 =
 * inserzione + residuo, 3 = residuo a tre lettere, 4 = inserzione + tre lettere). Il primo errore, con il testo del
 * backend; la verifica sulla struttura (catena, residuo originale) la fa il server.
 */
export function parseMutations(text: string): { items: ParsedMutation[]; error: string | null } {
  const tokens = text.trim().split(/[,;\s]+/).filter(Boolean);
  if (!tokens.length) return { items: [], error: "Indica almeno una mutazione (es. A:K48R)." };
  if (tokens.length > MAX_MUTATIONS) return { items: [], error: `Troppe mutazioni in una variante (${tokens.length}, al massimo ${MAX_MUTATIONS}).` };
  const items: ParsedMutation[] = [];
  for (const token of tokens) {
    const m = TOKEN.exec(token);
    const body = m ? BODY.exec(m[2].toUpperCase()) : null;
    if (!m || !body) return { items, error: `Mutazione non valida: «${token}». Esempio: A:K48R.` };
    const rest = body[3];
    const [icode, mutCode] = rest.length === 2 || rest.length === 4 ? [rest[0], rest.slice(1)] : ["", rest];
    const wt = amino(body[1]);
    const mut = amino(mutCode);
    if (!wt || !mut) return { items, error: `Amminoacido non valido in «${token}» (ammessi i 20 standard).` };
    if (wt === mut) return { items, error: `«${token}» non cambia il residuo.` };
    items.push({ chain: m[1] ?? null, resnum: `${parseInt(body[2], 10)}${icode}`, wt, mut, token });
  }
  const sites = items.map((p) => `${p.chain ?? ""}:${p.resnum}`);
  const dup = sites.find((s, i) => sites.indexOf(s) !== i);
  if (dup) return { items, error: `Il residuo ${dup.replace(/^:/, "")} compare in più mutazioni.` };
  return { items, error: null };
}

/** Forma canonica: "A:K48R" ("K48R" se la catena è omessa). */
export function formatMutation(p: Pick<ParsedMutation, "chain" | "resnum" | "wt" | "mut">): string {
  return `${p.chain ? `${p.chain}:` : ""}${p.wt}${p.resnum}${p.mut}`;
}

/** Aggiunge una mutazione (es. da una variante di UniProt) al testo, senza doppioni né un secondo cambio sullo stesso
 *  residuo: in quel caso sostituisce quella che c'era. */
export function addNotation(text: string, notation: string): string {
  const current = text.trim().split(/[,;\s]+/).filter(Boolean);
  const added = parseMutations(notation).items[0];
  const kept = current.filter((t) => {
    const p = parseMutations(t).items[0];
    return !(p && added && (p.chain ?? "") === (added.chain ?? "") && p.resnum === added.resnum);
  });
  return [...kept, notation].join(", ");
}

// ---------------------------------------------------------------------------------- classi e segnali
export const CLASS_LABEL: Record<MutationClass, string> = {
  destabilizing: "probabilmente destabilizzante",
  uncertain: "incerta",
  neutral: "probabilmente neutra",
};
export const CLASS_TONE: Record<MutationClass, "err" | "warn" | "ok"> = { destabilizing: "err", uncertain: "warn", neutral: "ok" };
/** Colori delle classi nel 3D: tavolozza di Okabe & Ito (2008), distinguibile con le discromatopsie più comuni. */
export const CLASS_COLOR: Record<MutationClass | "excluded", string> = {
  destabilizing: "#D55E00", uncertain: "#E69F00", neutral: "#009E73", excluded: "#999999",
};
/** Residui non scanditi: grigio chiaro, così i residui scanditi risaltano. */
export const OUTSIDE_COLOR = "#e2e8f0";
export const LEVEL_LABEL: Record<MutationSignalLevel, string> = { strong: "forte", moderate: "moderato", gain: "possibile guadagno", info: "informativo" };
export const LEVEL_TONE: Record<MutationSignalLevel, "err" | "warn" | "info"> = { strong: "err", moderate: "warn", gain: "info", info: "info" };

/** "+349 (+3,35 residui equivalenti)". */
export function dopeText(d: Pick<MutationDope, "delta" | "delta_eq"> | null | undefined): string {
  if (!d || d.delta == null) return "—";
  return `${fmtSigned(d.delta, 0)}${d.delta_eq != null ? ` (${fmtSigned(d.delta_eq, 2)} residui equivalenti)` : ""}`;
}

/** "+73 kJ/mol (+17,4 kcal/mol)"; il motivo se l'energia non c'è. */
export function energyText(e: MutationEnergy | null | undefined): string {
  if (!e) return "non calcolata";
  if (!e.available) return "OpenMM non disponibile";
  if (e.error) return `non riuscita (${e.error})`;
  if (e.ddE == null) return "—";
  return `${fmtSigned(e.ddE, 0)} kJ/mol${e.ddE_kcal != null ? ` (${fmtSigned(e.ddE_kcal, 1)} kcal/mol)` : ""}`;
}

const SS_TEXT: Record<string, string> = { H: "α-elica", G: "elica 3₁₀", I: "π-elica", E: "filamento β", B: "ponte β", T: "turn", S: "bend" };

/** "α-elica (8 di 12) · sepolto (RSA 4%) · BLOSUM62 −3". */
export function siteSummary(s: Pick<MutationSite, "ss" | "ss_position" | "ss_length" | "rsa_wt" | "core" | "blosum62">): string {
  const ss = SS_TEXT[s.ss] ? `${SS_TEXT[s.ss]}${s.ss_length > 1 ? ` (${s.ss_position + 1} di ${s.ss_length})` : ""}` : "coil";
  const rsa = s.rsa_wt != null ? `${s.core ? "sepolto" : "esposto"} (RSA ${fmtNum(s.rsa_wt * 100, 0)}%)` : "esposizione non calcolata";
  return `${ss} · ${rsa} · BLOSUM62 ${fmtSigned(s.blosum62, 0)}`;
}

// ---------------------------------------------------------------------------------- richieste
/** Corpo di POST /api/mutate dalle scelte del pannello, oppure il motivo per cui non si può lanciare. */
export function mutateRequest(o: { source: StructureSource | null; text: string; energy: boolean; projectId?: string | null }): { body: MutateRequest | null; error: string | null } {
  if (!o.source) return { body: null, error: "Scegli la struttura." };
  const parsed = parseMutations(o.text);
  if (parsed.error) return { body: null, error: parsed.error };
  return { body: { project_id: o.projectId ?? undefined, source: o.source, mutations: parsed.items.map(formatMutation).join(", "), energy: o.energy }, error: null };
}

/** Corpo di POST /api/alascan, oppure il motivo per cui non si può lanciare. */
export function scanRequest(o: {
  source: StructureSource | null; mode: ScanMode; residues: string; chain: string; pocketJob: string | null; pocket: number | null;
  energy: boolean; projectId?: string | null;
}): { body: AlaScanRequest | null; error: string | null } {
  if (!o.source) return { body: null, error: "Scegli la struttura." };
  const base = { project_id: o.projectId ?? undefined, source: o.source, energy: o.energy };
  if (o.mode === "residues") {
    if (!o.residues.trim()) return { body: null, error: "Indica i residui (es. A:30, A:43, A:45-48)." };
    return { body: { ...base, selection: { mode: "residues", residues: o.residues.trim() } }, error: null };
  }
  if (o.mode === "interface") return { body: { ...base, selection: { mode: "interface", chain: o.chain.trim() || null } }, error: null };
  if (!o.pocketJob || o.pocket == null) return { body: null, error: "Scegli una tasca (calcolo delle tasche della scheda Tasche)." };
  return { body: { ...base, selection: { mode: "pocket", pocket_job: o.pocketJob, pocket: o.pocket } }, error: null };
}

/** Controlli preventivi (POST /api/compute/preflight) con lo stesso corpo della rotta di avvio; null senza corpo. */
export function mutationPreflight(kind: "mutate" | "alascan", body: MutateRequest | AlaScanRequest | null): PreflightRequest | null {
  if (!body) return null;
  return { kind, params: body as unknown as Record<string, unknown> };
}

// ---------------------------------------------------------------------------------- risultati
export function mutantSource(jobId: string, result: Pick<MutateResult, "files">): StructureSource {
  return { type: "job", job_id: jobId, file: result.files.mutant };
}

export function wildTypeSource(jobId: string, result: Pick<MutateResult, "files">): StructureSource {
  return { type: "job", job_id: jobId, file: result.files.wild_type };
}

/** Pagina Qualità con il rapporto di un modello del job (salvato accanto al modello). */
export function modelQualityLink(jobId: string, file: string): string {
  return `/quality?job=${encodeURIComponent(jobId)}&file=${encodeURIComponent(file)}`;
}

/** Confronti strutturali del mutante (B): con il selvatico riottimizzato (stesso protocollo) e con la struttura di
 *  partenza (A, riferimento). */
export function mutationCompareLinks(jobId: string, result: Pick<MutateResult, "files" | "source">): { wildType: string; original: string } {
  const b = mutantSource(jobId, result);
  return { wildType: compareLink(wildTypeSource(jobId, result), b), original: compareLink(result.source, b) };
}

/** Residui da evidenziare nel 3D dei modelli costruiti (i siti mutati): la catena dei modelli, che per i nomi di più
 *  caratteri è la lettera di MODELLER. */
export function sitesFocus(items: { chain: string; resnum: string; model_chain?: string }[]): { chain: string; resi: number }[] {
  return items.map((m) => ({ chain: m.model_chain ?? m.chain, resi: resiNumber(m.resnum) }));
}

/** Righe della scansione filtrate (tutte, una classe, solo gli hot spot), nell'ordine del backend. */
export function scanRows(result: Pick<AlaScanResult, "rows">, filter: MutationClass | "all" | "hotspot" = "all"): AlaScanRow[] {
  if (filter === "all") return result.rows;
  if (filter === "hotspot") return result.rows.filter((r) => !!r.assessment.binding?.hotspot);
  return result.rows.filter((r) => r.assessment.code === filter);
}

/** Colorazione del 3D per effetto stimato: residui scanditi per classe, esclusi in grigio, il resto chiaro. */
export function scanColoring(result: Pick<AlaScanResult, "rows" | "skipped"> | null | undefined, chains: string[] = []): ResidueColoring | null {
  if (!result?.rows.length) return null;
  const values: ResidueValue[] = result.rows.map((r) => ({
    chain: r.mutation.chain, resnum: r.mutation.resnum, value: r.assessment.code,
    note: `${r.label}: ${r.assessment.label}, ΔDOPE ${dopeText(r.dope)}${r.assessment.binding?.hotspot ? `, hot spot ${r.assessment.binding.hotspot}` : ""}`,
  }));
  for (const s of result.skipped) values.push({ chain: s.chain, resnum: s.resnum, value: "excluded", note: s.reason });
  const categories: CategoricalScale["categories"] = (["destabilizing", "uncertain", "neutral"] as MutationClass[])
    .map((k) => ({ key: k, color: CLASS_COLOR[k], label: CLASS_LABEL[k] }));
  if (result.skipped.length) categories.push({ key: "excluded", color: CLASS_COLOR.excluded, label: "escluso (Gly, Pro, Ala)" });
  const scale: CategoricalScale = { kind: "categorical", categories };
  const all = new Set<string>([...chains, ...result.rows.map((r) => r.mutation.chain)]);
  return {
    key: "mutation", title: "Scansione di alanina: effetto stimato", values, scale,
    describe: (v) => (v == null ? "non scandito" : v === "excluded" ? "escluso" : CLASS_LABEL[v as MutationClass] ?? String(v)),
    chainColors: Object.fromEntries([...all].map((c) => [c, OUTSIDE_COLOR])),
    legendExtra: [{ color: OUTSIDE_COLOR, label: "non scandito" }],
  };
}

/** Riga della scansione del residuo cliccato nel 3D, oppure null. */
export function scanRowOf(result: Pick<AlaScanResult, "rows"> | null | undefined, chain: string, resnum: string): AlaScanRow | null {
  const key = residueKey(chain, resnum);
  return result?.rows.find((r) => residueKey(r.mutation.chain, r.mutation.resnum) === key) ?? null;
}

// ---------------------------------------------------------------------------------- varianti di UniProt
export const CATEGORY_LABEL: Record<MappedVariant["category"], string> = { malattia: "associata a malattia", naturale: "naturale", mutagenesi: "mutagenesi sperimentale" };
export const CATEGORY_TONE: Record<MappedVariant["category"], "err" | "info" | "accent"> = { malattia: "err", naturale: "info", mutagenesi: "accent" };

export type VariantFilter = "all" | "applicable" | "malattia" | "mutagenesi";

/** Varianti da mostrare: filtro e testo libero (posizione, residuo, notazione, descrizione); le applicabili prima,
 *  poi per posizione. */
export function variantRows(mapping: Pick<VariantMapping, "variants"> | null | undefined, filter: VariantFilter = "all", query = ""): MappedVariant[] {
  if (!mapping) return [];
  const q = query.trim().toLowerCase();
  return mapping.variants
    .filter((v) => filter === "all" || (filter === "applicable" ? v.applicable : v.category === filter))
    .filter((v) => !q || `${v.original}${v.position}${v.variation} ${v.notation ?? ""} ${v.description} ${v.diseases.join(" ")}`.toLowerCase().includes(q))
    .slice()
    .sort((a, b) => Number(b.applicable) - Number(a.applicable) || a.position - b.position || a.variation.localeCompare(b.variation));
}

/** "K53R (UniProt) → A:K48R"; il motivo se non è applicabile. */
export function variantText(v: MappedVariant): string {
  const uni = `${v.original}${v.position}${v.variation}`;
  return v.applicable && v.notation ? `${uni} → ${v.notation}` : `${uni}: ${v.reason ?? "non applicabile"}`;
}

/** Riga della cronologia: "Mutagenesi A:K48R (con energia)" o "Scansione di alanina". */
export function historyText(item: MutationHistoryItem): string {
  if (item.kind === "alascan") return item.title.replace(/^Scansione di alanina · [^(]*/, "Scansione di alanina ").trim();
  return `Mutagenesi ${item.mutations ?? ""}${item.energy ? " (con energia)" : ""}`.trim();
}

/** Voce del menu della cronologia: il calcolo e, se completato, quando (`when`), altrimenti lo stato in italiano. */
export function historyOption(item: MutationHistoryItem, when: (iso: string | null) => string): string {
  return `${historyText(item)} · ${item.status === "completed" ? when(item.finished_at) : STATUS_LABEL[item.status] ?? item.status}`;
}

/** Stato della scelta delle tasche per la scansione: lettura, errore, nessun calcolo, calcolo in corso (seguito),
 *  non riuscito, pronto. Il calcolo seguito è quello in corso sulla struttura, altrimenti l'ultimo completato. */
export type PocketsView = "loading" | "error" | "none" | "running" | "failed" | "ready";

export function pocketsView(s: { loading: boolean; error: string | null; jobId: string | null; status: string | null | undefined; ready: boolean }): PocketsView {
  if (s.error) return "error";
  if (!s.jobId) return s.loading ? "loading" : "none";
  if (s.ready) return "ready";
  if (s.status === "failed" || s.status === "cancelled") return "failed";
  return s.status === "completed" ? "loading" : "running";
}
