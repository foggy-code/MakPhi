// Riproducibilità (F9.4, services/reproducibility.py del backend): quali job hanno un pacchetto, anteprima del
// manifest (contenuto, versioni, parametri), esiti e confronto della riesecuzione. Logica pura, testata.
import type {
  PackageCheck, PackageEnvironment, PackageRole, PackageSummary, PackageVersion, PreflightLevel, PreflightRequest,
  RerunFile, RerunFileStatus, RerunMetricRow, RerunResult, RerunScientificCheck, RerunVerdict,
} from "../api/types";

/** Tipi di job con un pacchetto di riproducibilità (domain/reproducibility.EXPORTABLE). */
export const EXPORTABLE_KINDS: readonly string[] = [
  "model", "loop", "align2d", "quality", "align", "analysis", "refine", "pockets", "electrostatics", "mutate", "alascan", "search", "qmean",
  "predict",
];
/** Quelli che si possono rieseguire in locale (RERUNNABLE): ricerca, QMEAN e confronto con una predizione dipendono da
 *  servizi remoti. */
export const RERUNNABLE_KINDS: readonly string[] = EXPORTABLE_KINDS.filter((k) => k !== "search" && k !== "qmean" && k !== "predict");

/** Il pulsante «Pacchetto riproducibile» si mostra per i job completati dei tipi con un pacchetto. */
export function canExport(kind: string, status: string): boolean {
  return status === "completed" && EXPORTABLE_KINDS.includes(kind);
}

export const VERDICT_LABEL: Record<RerunVerdict, string> = { identico: "Identico", equivalente: "Equivalente", compatibile: "Compatibile", diverso: "Diverso" };
export const VERDICT_TONE: Record<RerunVerdict, "ok" | "info" | "warn" | "err"> = { identico: "ok", equivalente: "info", compatibile: "warn", diverso: "err" };
export const VERDICT_HELP: Record<RerunVerdict, string> = {
  identico: "Tutti i file sono uguali (a parte la data di MODELLER nella riga EXPDTA, la durata dei passi e i thread).",
  equivalente: "Nessuna differenza oltre le tolleranze: valori uguali entro la tolleranza, formattazione o file accessori diversi.",
  compatibile: "Differenze oltre l'arrotondamento ma entro le tolleranze scientifiche del calcolo (per esempio la rifinitura con OpenMM su più thread): rumore numerico atteso, non un risultato diverso.",
  diverso: "Ci sono differenze oltre le tolleranze o file mancanti: vedi le righe evidenziate.",
};

/** Riga del controllo con le tolleranze scientifiche: "Energia dopo (kJ/mol): Δ -38.23 (limite 62.2) entro". */
export function scientificLine(c: RerunScientificCheck): string {
  const delta = c.delta == null ? "—" : fmtValue(c.delta);
  const limit = c.limit == null ? "—" : fmtValue(c.limit);
  return `${c.what}: Δ ${delta} (limite ${limit}) ${c.within ? "entro" : "oltre"}${c.detail ? `; ${c.detail}` : ""}`;
}
export const FILE_STATUS_LABEL: Record<RerunFileStatus, string> = {
  identical: "identico", equivalent: "equivalente", different: "diverso", missing: "mancante", extra: "in più",
};
export const FILE_STATUS_TONE: Record<RerunFileStatus, "ok" | "info" | "warn" | "err"> = {
  identical: "ok", equivalent: "info", different: "err", missing: "err", extra: "warn",
};
const STATUS_ORDER: Record<RerunFileStatus, number> = { missing: 0, different: 1, equivalent: 2, extra: 3, identical: 4 };

export const ROLE_LABEL: Record<PackageRole, string> = {
  params: "parametri", sequences: "sequenze", template: "template della libreria", template_meta: "metadati dei template",
  template_prepared: "template preparati", alignment: "allineamento PIR", structure: "strutture", msa: "allineamento della conservazione",
  output: "risultati", script: "script MODELLER", log: "log", documentation: "LEGGIMI",
};
const INPUT_ROLES: PackageRole[] = ["params", "sequences", "template", "template_meta", "template_prepared", "alignment", "structure", "msa"];

/** "4 file d'ingresso, 12 risultati, 2 script MODELLER, log" dai conteggi per ruolo dell'anteprima. */
export function contentText(byRole: PackageSummary["files"]["by_role"]): string {
  const count = (roles: PackageRole[]) => roles.reduce((n, r) => n + (byRole[r]?.count ?? 0), 0);
  const parts: string[] = [];
  const inputs = count(INPUT_ROLES);
  if (inputs) parts.push(`${inputs} file d'ingresso`);
  const outputs = count(["output"]);
  if (outputs) parts.push(`${outputs} ${outputs === 1 ? "risultato" : "risultati"}`);
  const scripts = count(["script"]);
  if (scripts) parts.push(`${scripts} script MODELLER`);
  if (count(["log"])) parts.push("log");
  return parts.join(", ") || "nessun file";
}

export interface VersionRow {
  label: string;
  packaged: string;
  current: string;
  /** stessa versione (o strumento non usato dal calcolo) */
  same: boolean;
}

const dash = (v: string | null | undefined) => (v ? v : "—");

/** Versioni del pacchetto e di questa installazione, riga per riga (BioLoom, immagine, Python, librerie, strumenti
 *  usati dal calcolo): le differenze sono avvisi, non errori. */
export function versionRows(packaged: { bioloom: PackageVersion; environment: PackageEnvironment },
  current: { bioloom: PackageVersion; environment: PackageEnvironment }): VersionRow[] {
  const rows: VersionRow[] = [];
  const add = (label: string, a: string | null | undefined, b: string | null | undefined) =>
    rows.push({ label, packaged: dash(a), current: dash(b), same: !a || !b || a === b });
  add("BioLoom", packaged.bioloom?.version, current.bioloom?.version);
  if (packaged.bioloom?.image || current.bioloom?.image) add("Immagine Docker", packaged.bioloom?.image, current.bioloom?.image);
  add("Python", packaged.environment?.python, current.environment?.python);
  const libs = { ...(packaged.environment?.libraries ?? {}) };
  for (const [name, version] of Object.entries(libs)) add(name === "biopython" ? "Biopython" : name === "numpy" ? "NumPy" : name, version, current.environment?.libraries?.[name]);
  for (const [key, tool] of Object.entries(packaged.environment?.tools ?? {})) {
    const here = current.environment?.tools?.[key];
    const now = here?.available ? here.version ?? "versione non nota" : "non installato";
    const before = tool.available ? tool.version ?? "versione non nota" : "non installato";
    rows.push({ label: tool.label || key, packaged: before, current: now, same: !tool.available || before === now });
  }
  return rows;
}

/** Esiti dell'anteprima per livello. */
export function checksByLevel(checks: PackageCheck[] | null | undefined): Record<PreflightLevel, PackageCheck[]> {
  const out: Record<PreflightLevel, PackageCheck[]> = { error: [], warning: [], info: [] };
  for (const c of checks ?? []) out[c.level]?.push(c);
  return out;
}

/** Controlli preventivi della riesecuzione (POST /api/compute/preflight); null finché non c'è un pacchetto
 *  rieseguibile (l'anteprima dice già perché non lo è). */
export function rerunPreflight(summary: PackageSummary | null | undefined): PreflightRequest | null {
  if (!summary?.package_id || !summary.rerun?.supported) return null;
  return { kind: "rerun", params: { package_id: summary.package_id } };
}

/** Valore leggibile: numeri con al più 4 decimali (senza zeri finali), testi e oggetti abbreviati. */
export function fmtValue(v: unknown, max = 60): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return String(v);
    if (Number.isInteger(v)) return String(v);
    const abs = Math.abs(v);
    if (abs >= 1e5 || abs < 1e-4) return v.toExponential(3);
    return String(Number(v.toFixed(4)));
  }
  if (typeof v === "boolean") return v ? "sì" : "no";
  const text = typeof v === "string" ? v : JSON.stringify(v);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** "Δ +12.3", "Δ 0", "" (non numerica). */
export function fmtDelta(row: Pick<RerunMetricRow, "delta">): string {
  if (row.delta === null || row.delta === undefined) return "";
  if (row.delta === 0) return "Δ 0";
  return `Δ ${row.delta > 0 ? "+" : ""}${fmtValue(row.delta)}`;
}

/** Parametri del job in righe chiave/valore (primo livello, valori abbreviati); `max` righe al più. */
export function paramRows(params: Record<string, unknown> | null | undefined, max = 14): [string, string][] {
  const rows: [string, string][] = [];
  for (const [key, value] of Object.entries(params ?? {})) {
    if (key === "project_id" || value === null || value === undefined) continue;
    rows.push([key, fmtValue(value, 90)]);
    if (rows.length >= max) break;
  }
  return rows;
}

/** File del confronto: prima mancanti e diversi, poi equivalenti e in più; a parità, i principali prima. */
export function sortFiles(files: RerunFile[] | null | undefined): RerunFile[] {
  return [...(files ?? [])].sort((a, b) =>
    STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || Number(b.principal) - Number(a.principal) || a.path.localeCompare(b.path));
}

/** Dettaglio di un file non identico: coordinate (RMSD, scarto massimo) o differenze dei valori. */
export function fileDetail(f: RerunFile): string {
  if (f.status === "missing") return "non prodotto dalla riesecuzione";
  if (f.status === "extra") return "prodotto solo dalla riesecuzione";
  const parts: string[] = [];
  if (f.coordinates) {
    const c = f.coordinates;
    parts.push(`RMSD ${fmtValue(c.rmsd)} Å, scarto massimo ${fmtValue(c.max_deviation)} Å${c.max_atom && c.max_deviation ? ` (${c.max_atom})` : ""}`);
    if (c.only_original || c.only_rerun) parts.push(`${c.only_original} atomi solo nell'originale, ${c.only_rerun} solo nella riesecuzione`);
  }
  const d = f.diff;
  if (d && (d.beyond_tolerance || d.text || d.structure || d.numbers)) {
    const bits: string[] = [];
    if (d.beyond_tolerance) bits.push(`${d.beyond_tolerance} ${d.beyond_tolerance === 1 ? "numero" : "numeri"} oltre la tolleranza (Δ massimo ${fmtValue(d.max_delta)}${d.max_delta_at ? ` in ${d.max_delta_at}` : ""})`);
    else if (d.numbers && d.max_delta) bits.push(`numeri entro la tolleranza (Δ massimo ${fmtValue(d.max_delta)})`);
    if (d.text) bits.push(`${d.text} ${d.text === 1 ? "testo diverso" : "testi diversi"}`);
    if (d.structure) bits.push(`${d.structure} ${d.structure === 1 ? "differenza" : "differenze"} di struttura`);
    if (bits.length) parts.push(bits.join(", "));
    const ex = d.examples?.[0];
    if (ex && (d.beyond_tolerance || d.text || d.structure)) parts.push(`es. ${ex.where}: ${fmtValue(ex.original, 30)} → ${fmtValue(ex.rerun, 30)}`);
  }
  if (!parts.length && f.detail) parts.push(f.detail);
  return parts.join("; ");
}

/** Nota sulle tolleranze del confronto (domain/reproducibility.py): "… numeri |Δ| ≤ max(0.001; 1e-6·|valore|) …". */
export function toleranceText(t: RerunResult["tolerance"]): string {
  const num = (x: number) => (x > 0 && x < 1e-3 ? x.toExponential(0) : String(x));
  return `Tolleranze: coordinate ${t.coordinates_angstrom} Å per atomo, senza sovrapposizione (tre decimali del formato PDB); `
    + `numeri |Δ| ≤ max(${num(t.score_abs)}; ${num(t.score_rel)}·|valore|). Non confrontati: ${t.ignored.join(", ")}, `
    + "data di MODELLER nella riga EXPDTA.";
}

/** Nome del file del pacchetto proposto quando il server non lo indica. */
export function packageFallbackName(kind: string, jobId: string): string {
  return `bioloom_riproducibile_${kind.replace(/[^A-Za-z0-9_-]+/g, "_") || "job"}_${jobId}.zip`;
}
