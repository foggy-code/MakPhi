// Cornice dell'app e pagine generali (Panoramica, Job, Pipeline, Modellazione): famiglie di job e indicatori, passi del
// flusso, saluto, filtro per stato, estratto del log, composizione degli stati di una pipeline e punteggi dei modelli.
// Logica pura, testata in shellView.test.ts.
import type { JobKind, JobSummary, ModelEntry, Project } from "../api/types";
import type { StackPart } from "../components/charts";
import type { ChartData } from "./exportImage";
import { isActive, JOB_STATUS_TONE, KIND_LABEL, plural, STATUS_LABEL } from "./format";

/** "1 sequenza", "3 sequenze" (in lib/format; riesportato per le pagine generali). */
export { plural };

/** Saluto della Panoramica secondo l'ora del giorno. */
export function greeting(hour: number): string {
  if (hour >= 5 && hour < 13) return "Buongiorno";
  if (hour >= 13 && hour < 18) return "Buon pomeriggio";
  return "Buonasera";
}

// ---------------------------------------------------------------------------------- famiglie di job e indicatori
/** Famiglie di job degli indicatori: preparazione (ricerche, allineamenti), modelli, analisi, servizio. */
export type JobGroup = "prep" | "model" | "analysis" | "service";

export const JOB_GROUP: Record<JobKind, JobGroup> = {
  search: "prep", align: "prep", align2d: "prep",
  model: "model", loop: "model", refine: "model", mutate: "model", pipeline: "model",
  quality: "analysis", qmean: "analysis", analysis: "analysis", pockets: "analysis", electrostatics: "analysis", predict: "analysis", alascan: "analysis",
  selftest: "service", db_update_pdbaa: "service", db_build_mmseqs: "service", db_index_mmseqs: "service", db_convert_uniclust: "service",
  db_local_uniclust: "service", rerun: "service",
};

export interface JobCounts {
  total: number;
  active: number;
  /** completati e falliti per famiglia */
  done: Record<JobGroup, number>;
  failed: Record<JobGroup, number>;
  /** completati per tipo (dettaglio degli indicatori) */
  byKind: Partial<Record<JobKind, number>>;
}

const zeroGroups = (): Record<JobGroup, number> => ({ prep: 0, model: 0, analysis: 0, service: 0 });

export function jobCounts(jobs: Pick<JobSummary, "kind" | "status">[]): JobCounts {
  const out: JobCounts = { total: jobs.length, active: 0, done: zeroGroups(), failed: zeroGroups(), byKind: {} };
  for (const j of jobs) {
    const group = JOB_GROUP[j.kind] ?? "service";
    if (isActive(j.status)) out.active++;
    else if (j.status === "completed") {
      out.done[group]++;
      out.byKind[j.kind] = (out.byKind[j.kind] ?? 0) + 1;
    } else if (j.status === "failed") out.failed[group]++;
  }
  return out;
}

/** Job completati di una famiglia per tipo, dal più frequente: «Modellazione 4», «Pipeline batch 1». */
export function kindBreakdown(counts: JobCounts, group: JobGroup): { kind: JobKind; label: string; count: number }[] {
  return (Object.entries(counts.byKind) as [JobKind, number][])
    .filter(([kind]) => JOB_GROUP[kind] === group)
    .map(([kind, count]) => ({ kind, label: KIND_LABEL[kind], count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "it"));
}

export interface FlowStep {
  count: number;
  label: string;
  /** pipeline completate che possono aver fatto il passo (Template, Qualità): solo una nota, il passo non risulta fatto */
  pipelines: number;
}

/**
 * Lavoro fatto per ogni passo del flusso della Panoramica (Sequenza, Template, Allineamento, Modello, Qualità):
 * sequenze del progetto e job completati. Una pipeline allinea e modella sempre, quindi conta per quei due passi; la
 * ricerca dei template (non con il template fisso) e l'analisi di qualità (facoltativa) restano una nota.
 */
export function flowProgress(project: Pick<Project, "sequences"> | null, counts: JobCounts): FlowStep[] {
  const k = (kind: JobKind) => counts.byKind[kind] ?? 0;
  const pipelines = k("pipeline");
  const aligns = k("align") + k("align2d") + pipelines;
  const models = k("model") + k("loop") + pipelines;
  return [
    { count: project?.sequences ?? 0, label: plural(project?.sequences ?? 0, "sequenza", "sequenze"), pipelines: 0 },
    { count: k("search"), label: plural(k("search"), "ricerca", "ricerche"), pipelines },
    { count: aligns, label: plural(aligns, "allineamento", "allineamenti"), pipelines: 0 },
    { count: models, label: plural(models, "modellazione", "modellazioni"), pipelines: 0 },
    { count: counts.done.analysis, label: plural(counts.done.analysis, "analisi", "analisi"), pipelines },
  ];
}

/** Primo passo del flusso ancora da fare (-1 se sono stati fatti tutti). */
export function nextFlowStep(progress: { count: number }[]): number {
  return progress.findIndex((p) => p.count === 0);
}

// ---------------------------------------------------------------------------------- elenco dei job
export type StatusFilter = "all" | "active" | "completed" | "problems";

export const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "Tutti gli stati" },
  { value: "active", label: "Attivi" },
  { value: "completed", label: "Completati" },
  { value: "problems", label: "Falliti e annullati" },
];

export function matchesStatus(job: Pick<JobSummary, "status">, filter: StatusFilter): boolean {
  if (filter === "active") return isActive(job.status);
  if (filter === "completed") return job.status === "completed";
  if (filter === "problems") return job.status === "failed" || job.status === "cancelled";
  return true;
}

/**
 * Ultime `n` righe del log (estratto nei dettagli di un job), senza le righe vuote finali. Il taglio si cerca dalla fine:
 * il log può arrivare a 256 KB e serve solo la coda.
 */
export function logTail(log: string, n = 12): string {
  const text = log.trimEnd();
  let cut = text.length;
  for (let k = 0; k < n && cut >= 0; k++) cut = cut === 0 ? -1 : text.lastIndexOf("\n", cut - 1);
  return text.slice(cut + 1).replace(/\r\n/g, "\n");
}

// ---------------------------------------------------------------------------------- pipeline
/**
 * Ordine degli stati degli elementi di una pipeline nella barra della composizione: conclusi, in corso, in attesa, poi
 * gli esiti senza modello (gli stati con lo stesso tono restano vicini; la legenda li distingue).
 */
const ITEM_STATUS = ["completed", "running", "searching", "modeling", "pending", "failed", "filtered", "cancelled"];

/** Colore di uno stato: lo stesso tono dei badge (JOB_STATUS_TONE di lib/format), "var(--muted)" se manca. */
export const statusColor = (status: string) => (JOB_STATUS_TONE[status] ? `var(--${JOB_STATUS_TONE[status]})` : "var(--muted)");

/** Composizione degli stati degli elementi (percentuali, con il numero nell'etichetta); vuota senza elementi. */
export function pipelineStatusParts(counts: Record<string, number>): StackPart[] {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return [];
  return ITEM_STATUS.filter((s) => (counts[s] ?? 0) > 0).map((s) => ({
    label: `${STATUS_LABEL[s] ?? s} (${counts[s]})`,
    value: (counts[s] / total) * 100,
    color: statusColor(s),
  }));
}

// ---------------------------------------------------------------------------------- punteggi dei modelli
export type ModelMetric = "dope" | "zdope" | "ga341" | "molpdf" | "grade";
export type ModelOrder = "table" | "value";

export interface ModelMetricDef {
  label: string;
  digits: number;
  /** verso del valore migliore */
  better: "low" | "high";
  value: (m: ModelEntry) => number | null;
  /** soglie tratteggiate */
  references: { value: number; label: string }[];
  /** estremi fissi dell'asse y */
  domain?: [number, number];
  note: string;
}

export const MODEL_METRICS: Record<ModelMetric, ModelMetricDef> = {
  dope: {
    label: "DOPE", digits: 0, better: "low", value: (m) => m.dope, references: [],
    note: "energia statistica del modello, più negativa è migliore; si confronta tra modelli della stessa sequenza",
  },
  zdope: {
    label: "zDOPE", digits: 2, better: "low", value: (m) => m.zdope, references: [{ value: -1, label: "−1 simile alle strutture native" }],
    note: "DOPE normalizzato: sotto −1 simile alle strutture native, sopra 0 probabilmente scarso",
  },
  ga341: {
    label: "GA341", digits: 2, better: "high", value: (m) => m.ga341 ?? null, references: [{ value: 0.7, label: "0,7 fold affidabile" }], domain: [0, 1],
    note: "affidabilità del ripiegamento (0–1): da 0,7 il fold è considerato corretto",
  },
  molpdf: {
    label: "molpdf", digits: 0, better: "low", value: (m) => m.molpdf, references: [],
    note: "funzione obiettivo di MODELLER (violazioni dei vincoli spaziali): più bassa è migliore",
  },
  grade: {
    label: "Voto BioLoom", digits: 1, better: "high", value: (m) => m.quality?.grade_score ?? null,
    references: [{ value: 85, label: "A" }, { value: 70, label: "B" }, { value: 55, label: "C" }], domain: [0, 100],
    note: "punteggio di qualità 0–100 (Ramachandran, contatti sterici, zDOPE): A da 85, B da 70, C da 55",
  },
};

const usable = (v: number | null | undefined): v is number => v !== null && v !== undefined && Number.isFinite(v);

/** Metriche con almeno un valore tra i modelli riusciti, nell'ordine di MODEL_METRICS. */
export function availableMetrics(models: ModelEntry[]): ModelMetric[] {
  return (Object.keys(MODEL_METRICS) as ModelMetric[]).filter((k) => models.some((m) => !m.failure && usable(MODEL_METRICS[k].value(m))));
}

export interface ModelPoint {
  file: string;
  /** numero del modello nella tabella (da 1) */
  n: number;
  value: number | null;
  best: boolean;
  loop: boolean;
  failed: boolean;
}

/** Punti del grafico nell'ordine della tabella o dal migliore al peggiore (senza valore in fondo). */
export function modelPoints(models: ModelEntry[], metric: ModelMetric, order: ModelOrder): ModelPoint[] {
  const def = MODEL_METRICS[metric];
  const points = models.map((m, i) => {
    const v = m.failure ? null : def.value(m);
    return { file: m.file, n: i + 1, value: usable(v) ? v : null, best: m.best, loop: m.kind === "loop", failed: !!m.failure };
  });
  if (order === "table") return points;
  const sign = def.better === "low" ? 1 : -1;
  return [...points].sort((a, b) => {
    if (a.value === null || b.value === null) return a.value === null ? (b.value === null ? a.n - b.n : 1) : -1;
    return (a.value - b.value) * sign || a.n - b.n;
  });
}

/** Estremi dell'asse y: i valori e le soglie, con un margine dell'8%; un dominio fisso (GA341, voto) li contiene. */
export function scoreRange(values: number[], references: number[] = [], domain?: [number, number]): { min: number; max: number } {
  const all = [...values, ...references].filter(usable);
  if (domain) {
    const lo = Math.min(domain[0], ...all);
    const hi = Math.max(domain[1], ...all);
    return { min: lo, max: hi };
  }
  if (!all.length) return { min: 0, max: 1 };
  let min = Math.min(...all);
  let max = Math.max(...all);
  const pad = (max - min) * 0.08 || Math.abs(max) * 0.05 || 1;
  min -= pad;
  max += pad;
  return { min, max };
}

/** DOPE per residuo, per confrontare modelli di lunghezze diverse; null senza DOPE o senza il numero di residui. */
export function dopePerResidue(m: Pick<ModelEntry, "dope" | "quality">): number | null {
  const n = m.quality?.residues;
  return usable(m.dope) && n ? m.dope / n : null;
}

/** Tabella dei dati (e CSV) dei punteggi: una riga per modello, nell'ordine della tabella. */
export function modelsData(models: ModelEntry[]): ChartData {
  return {
    columns: [
      { key: "n", label: "N.", num: true },
      { key: "file", label: "Modello" },
      { key: "dope", label: "DOPE", num: true },
      { key: "zdope", label: "zDOPE", num: true },
      { key: "ga341", label: "GA341", num: true },
      { key: "molpdf", label: "molpdf", num: true },
      { key: "grade", label: "Voto" },
      { key: "score", label: "Punteggio del voto", num: true },
      { key: "favored", label: "Ramachandran favorite (%)", num: true },
      { key: "clashscore", label: "Clashscore", num: true },
      { key: "best", label: "Migliore" },
      { key: "failure", label: "Errore" },
    ],
    rows: models.map((m, i) => ({
      n: i + 1,
      file: m.file,
      dope: m.dope,
      zdope: m.zdope,
      ga341: m.ga341 ?? null,
      molpdf: m.molpdf,
      grade: m.quality?.grade ?? null,
      score: m.quality?.grade_score ?? null,
      favored: m.quality?.rama_favored ?? null,
      clashscore: m.quality?.clashscore ?? null,
      best: m.best ? "sì" : "no",
      failure: m.failure,
    })),
  };
}
