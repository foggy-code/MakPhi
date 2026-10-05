import type {
  Align2dResult,
  AlignResult,
  AnalysisRequest,
  AssemblyList,
  AuthStatus,
  CleanupPreview,
  CleanupRequest,
  CleanupRun,
  ComplexRequest,
  ConsensusResult,
  CoverageCheck,
  CoverageRequest,
  DatabaseStatus,
  DisorderPrediction,
  DocumentItem,
  LicenseStatus,
  ParsedFile,
  UploadedStructure,
  Example,
  InterProDomain,
  Job,
  JobDeleteResult,
  JobSummary,
  LatestToolJob,
  LigandRef,
  PackageSummary,
  PipelineItem,
  PipelinePage,
  PipelineRequest,
  PirReport,
  PreflightRequest,
  PreflightResult,
  PredictRequest,
  Project,
  ProjectDeleteResult,
  ProjectUsage,
  QualityReport,
  ReportFile,
  ReportRequest,
  ResumeScope,
  Sequence,
  SequenceAnalysis,
  Settings,
  StructureSource,
  SystemInfo,
  SystemStats,
  TemplateAnalysis,
  TemplateItem,
  TemplateRef,
  UniProtEntry,
  UniProtHit,
  EntityLabels,
  EntityType,
  FavoriteEntry,
  SearchGroupType,
  SearchResponse,
  TagCount,
  MutateRequest,
  AlaScanRequest,
  VariantMapping,
  MutationHistoryItem,
} from "./types";
import { filenameFromDisposition, reportFallbackName } from "../lib/report";

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  constructor(message: string, status: number, code: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) {
      init.body = body as FormData;
    } else {
      init.body = JSON.stringify(body);
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    }
  }
  let resp: Response;
  try {
    resp = await fetch(path, init);
  } catch {
    throw new ApiError("Server BioLoom non raggiungibile. È ancora in esecuzione?", 0, "network");
  }
  const type = resp.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await resp.json().catch(() => null) : await resp.text();
  if (!resp.ok) {
    const err = payload && typeof payload === "object" ? (payload as { error?: { message?: string; code?: string; details?: unknown } }).error : undefined;
    if (resp.status === 401 && err?.code === "auth_required") notifyAuthRequired();
    throw new ApiError(err?.message || `Errore ${resp.status}`, resp.status, err?.code || "http_error", err?.details);
  }
  return payload as T;
}

/** POST JSON che restituisce un file (report HTML): contenuto e nome da Content-Disposition, errori come `request`. */
async function requestFile(path: string, body: unknown, fallback: string): Promise<ReportFile> {
  let resp: Response;
  try {
    resp = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  } catch {
    throw new ApiError("Server BioLoom non raggiungibile. È ancora in esecuzione?", 0, "network");
  }
  if (!resp.ok) {
    const json = (resp.headers.get("content-type") || "").includes("application/json");
    const payload = json ? await resp.json().catch(() => null) : null;
    const err = payload && typeof payload === "object" ? (payload as { error?: { message?: string; code?: string; details?: unknown } }).error : undefined;
    if (resp.status === 401 && err?.code === "auth_required") notifyAuthRequired();
    throw new ApiError(err?.message || `Errore ${resp.status}`, resp.status, err?.code || "http_error", err?.details);
  }
  return { blob: await resp.blob(), filename: filenameFromDisposition(resp.headers.get("content-disposition"), fallback) };
}

/** GET di un file da salvare (pacchetto di riproducibilità): contenuto e nome, errori come `request`. */
async function downloadFile(path: string, fallback: string): Promise<ReportFile> {
  let resp: Response;
  try {
    resp = await fetch(path);
  } catch {
    throw new ApiError("Server BioLoom non raggiungibile. È ancora in esecuzione?", 0, "network");
  }
  if (!resp.ok) {
    const json = (resp.headers.get("content-type") || "").includes("application/json");
    const payload = json ? await resp.json().catch(() => null) : null;
    const err = payload && typeof payload === "object" ? (payload as { error?: { message?: string; code?: string; details?: unknown } }).error : undefined;
    if (resp.status === 401 && err?.code === "auth_required") notifyAuthRequired();
    throw new ApiError(err?.message || `Errore ${resp.status}`, resp.status, err?.code || "http_error", err?.details);
  }
  return { blob: await resp.blob(), filename: filenameFromDisposition(resp.headers.get("content-disposition"), fallback) };
}

/** Evento globale: il server (modalità server) richiede la chiave d'accesso. */
export const AUTH_EVENT = "bioloom:auth-required";

export function notifyAuthRequired() {
  window.dispatchEvent(new CustomEvent(AUTH_EVENT));
}

const get = <T,>(path: string) => request<T>("GET", path);
const post = <T,>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const put = <T,>(path: string, body: unknown) => request<T>("PUT", path, body);
const patch = <T,>(path: string, body: unknown) => request<T>("PATCH", path, body);
const del = <T,>(path: string) => request<T>("DELETE", path);
const enc = encodeURIComponent;

/** Filtri per etichetta e stella degli elenchi (F8b). */
export interface LabelQuery {
  tag?: string;
  favorite?: boolean;
}

/** Query string senza i valori vuoti (`?` compreso, '' se non resta nulla). */
function queryString(q: object): string {
  const params = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach((x) => params.append(k, String(x)));
    else if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  });
  const text = params.toString();
  return text ? `?${text}` : "";
}

export const api = {
  // sistema
  health: () => get<{ status: string; version: string }>("/api/health"),
  systemInfo: (refresh = false) => get<SystemInfo>(`/api/system/info${refresh ? "?refresh=true" : ""}`),
  stats: () => get<SystemStats>("/api/system/stats"),
  /** byte occupati; `cache` = risposte dei servizi remoti, `compute_cache` = cache dei calcoli */
  storage: () => get<Record<string, number>>("/api/system/storage"),
  examples: () => get<Example[]>("/api/examples"),
  settings: () => get<Settings>("/api/settings"),
  saveSettings: (s: Partial<Settings>) => put<Settings>("/api/settings", s),
  shutdown: () => post<{ status: string }>("/api/system/shutdown"),
  /** file temporanei; `jobs` = resti di job già eliminati (cartelle `.deleting-…`) rimossi */
  cleanup: () => post<{ uploads: number; cache: number; scratch: number; jobs: number; freed_bytes: number }>("/api/system/cleanup"),
  selftest: () => post<JobSummary>("/api/system/selftest"),

  // accesso (modalità server)
  authStatus: () => get<AuthStatus>("/api/auth/status"),
  login: (key: string) => post<AuthStatus>("/api/auth/login", { key }),
  logout: () => post<AuthStatus>("/api/auth/logout"),

  // licenze
  licenses: () => get<LicenseStatus>("/api/licenses"),
  setModellerKey: (key: string) => put<LicenseStatus>("/api/licenses/modeller", { key }),
  removeModellerKey: () => del<LicenseStatus>("/api/licenses/modeller"),
  uploadProsaLicense: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<LicenseStatus>("POST", "/api/licenses/prosa", form, true);
  },
  removeProsaLicense: () => del<LicenseStatus>("/api/licenses/prosa"),

  // database locali
  databases: () => get<DatabaseStatus>("/api/databases"),
  updatePdbaa: () => post<JobSummary>("/api/databases/pdbaa/update"),
  buildMmseqs: () => post<JobSummary>("/api/databases/mmseqs/build"),
  indexMmseqs: () => post<JobSummary>("/api/databases/mmseqs/index"),
  convertUniclust: (database: string) => post<JobSummary>("/api/databases/uniclust/convert", { database }),
  uniclustLocal: (database: string, action: "copy" | "remove") =>
    post<JobSummary>("/api/databases/uniclust/local", { database, action }),

  // caricamento da file
  parseSequenceFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ParsedFile>("POST", "/api/files/sequences", form, true);
  },
  uploadFileUrl: (name: string) => `/api/files/uploads/${enc(name)}`,
  uploadStructure: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadedStructure>("POST", "/api/files/structure", form, true);
  },

  // progetti
  projects: (q: LabelQuery = {}) => get<Project[]>(`/api/projects${queryString(q)}`),
  createProject: (name: string, description = "") => post<Project>("/api/projects", { name, description }),
  updateProject: (id: string, data: Partial<Project>) => patch<Project>(`/api/projects/${id}`, data),
  /** cosa porterebbe via l'eliminazione: sequenze, documenti, job (attivi, con stella, riprendibili) e byte */
  projectUsage: (id: string) => get<ProjectUsage>(`/api/projects/${enc(id)}/usage`),
  /** elimina il progetto con tutti i suoi job (sincrono); se era l'ultimo il server ne crea uno vuoto (`replacement`) */
  deleteProject: (id: string) => del<ProjectDeleteResult>(`/api/projects/${id}`),
  sequences: (pid: string, q: LabelQuery = {}) => get<Sequence[]>(`/api/projects/${pid}/sequences${queryString(q)}`),
  addSequence: (pid: string, name: string, sequence: string, source = "manual", meta: Record<string, unknown> = {}) =>
    post<Sequence>(`/api/projects/${pid}/sequences`, { name, sequence, source, meta }),
  importFasta: (pid: string, fasta: string) => post<Sequence[]>(`/api/projects/${pid}/sequences/import`, { fasta }),
  deleteSequence: (pid: string, sid: string) => del(`/api/projects/${pid}/sequences/${sid}`),
  documents: (pid: string) => get<DocumentItem[]>(`/api/projects/${pid}/documents`),
  document: (pid: string, did: string) => get<DocumentItem>(`/api/projects/${pid}/documents/${did}`),
  saveDocument: (pid: string, doc: { name: string; kind: string; content: string; meta?: Record<string, unknown> }, did?: string) =>
    did ? put<DocumentItem>(`/api/projects/${pid}/documents/${did}`, doc) : post<DocumentItem>(`/api/projects/${pid}/documents`, doc),
  deleteDocument: (pid: string, did: string) => del(`/api/projects/${pid}/documents/${did}`),

  // sequenze e banche dati
  analyzeSequence: (sequence: string) => post<SequenceAnalysis>("/api/sequences/analyze", { sequence }),
  convertAlignment: async (ids: string[], aligned: string[], format: string) =>
    request<string>("POST", "/api/alignments/convert", { ids, aligned, format }),
  validatePir: (content: string) => post<PirReport>("/api/pir/validate", { content }),
  uniprotSearch: (q: string) => get<UniProtHit[]>(`/api/uniprot/search?q=${enc(q)}`),
  uniprot: (acc: string) => get<UniProtEntry>(`/api/uniprot/${enc(acc)}`),
  uniprotDomains: (acc: string) => get<InterProDomain[]>(`/api/uniprot/${enc(acc)}/domains`),
  /** disordine (FoldIndex), regioni disordinate lunghe e bassa complessità (SEG) dalla sola sequenza, senza rete */
  disorder: (sequence: string) => post<DisorderPrediction>("/api/bio/disorder", { sequence }),
  /** avvisi prima di modellare: disordine e domini (InterPro, solo con l'accessione) fuori dalla copertura */
  coverage: (body: CoverageRequest) => post<CoverageCheck>("/api/bio/coverage", body),
  /** controlli preventivi (F8a) di un calcolo, senza avviarlo: `params` = il corpo della rotta di avvio */
  preflight: (body: PreflightRequest) => post<PreflightResult>("/api/compute/preflight", body),

  // template
  templates: (q: LabelQuery = {}) => get<TemplateItem[]>(`/api/templates${queryString(q)}`),
  fetchTemplate: (pdb_id: string, format = "pdb") => post<TemplateItem & { cached: boolean }>("/api/templates/fetch", { pdb_id, format }),
  /** modello di AlphaFold DB per accessione UniProt (o id AF-<ACC>-F<n>): l'accessione va a EBI */
  fetchAlphaFold: (accession: string) => post<TemplateItem & { cached: boolean }>("/api/templates/alphafold", { accession }),
  uploadTemplate: (file: File, templateId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (templateId) form.append("template_id", templateId);
    return request<TemplateItem>("POST", "/api/templates/upload", form, true);
  },
  template: (id: string) => get<TemplateAnalysis>(`/api/templates/${enc(id)}`),
  templateFileUrl: (id: string) => `/api/templates/${enc(id)}/file`,
  templateText: (id: string) => get<string>(`/api/templates/${enc(id)}/file`),
  deleteTemplate: (id: string) => del(`/api/templates/${enc(id)}`),
  /** assemblaggi biologici del file (REMARK 350, pdbx_struct_assembly) e loro importazione come template <ID>_ASM<n> */
  templateAssemblies: (id: string) => get<AssemblyList>(`/api/templates/${enc(id)}/assemblies`),
  importAssembly: (id: string, assemblyId: string) =>
    post<TemplateItem & { cached: boolean }>(`/api/templates/${enc(id)}/assemblies/${enc(assemblyId)}`),

  // job
  jobs: (q: { project_id?: string; kind?: string; status?: string; limit?: number } & LabelQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && v !== "" && params.set(k, String(v)));
    return get<JobSummary[]>(`/api/jobs?${params}`);
  },
  job: <R,>(id: string) => get<Job<R>>(`/api/jobs/${id}`),
  jobStatus: (id: string) => get<JobSummary>(`/api/jobs/${id}/status`),
  jobLog: (id: string, offset = 0) => get<{ text: string; offset: number; size: number }>(`/api/jobs/${id}/log?offset=${offset}`),
  cancelJob: (id: string) => post<JobSummary>(`/api/jobs/${id}/cancel`),
  resumeJob: (id: string, scope: ResumeScope = "incomplete") => post<JobSummary>(`/api/jobs/${id}/resume`, { scope }),
  /** `leftover` > 0: cartella rimossa solo in parte (il resto lo toglie «Pulisci file temporanei») */
  deleteJob: (id: string) => del<JobDeleteResult>(`/api/jobs/${id}`),
  /** anteprima della pulizia dei job (non elimina nulla) */
  cleanupPreview: (req: CleanupRequest) => post<CleanupPreview>("/api/jobs/cleanup/preview", req),
  /** una chiamata della pulizia, a tempo (`max_seconds`): si ripete finché `remaining` non è 0 (lib/cleanup.runCleanup) */
  cleanupJobs: (req: CleanupRequest) => post<CleanupRun>("/api/jobs/cleanup", req),
  jobFileUrl: (id: string, path: string, download = false) =>
    `/api/jobs/${id}/files/${path.split("/").map(enc).join("/")}${download ? "?download=true" : ""}`,
  jobFileText: (id: string, path: string) => get<string>(`/api/jobs/${id}/files/${path.split("/").map(enc).join("/")}`),
  modelQuality: (id: string, model: string) => get<QualityReport>(`/api/jobs/${id}/quality/${model.split("/").map(enc).join("/")}`),
  /** consenso di un'analisi di qualità (senza `model`) o di un modello; `qmeanJob` = job qmean sulla stessa struttura;
   *  `predictJob` = confronto con una predizione esterna sulla stessa struttura (F9.3) */
  consensus: (id: string, model?: string | null, qmeanJob?: string | null, predictJob?: string | null) =>
    get<ConsensusResult>(`/api/jobs/${id}/consensus${model ? `/${model.split("/").map(enc).join("/")}` : ""}${queryString({ qmean_job: qmeanJob, predict_job: predictJob })}`),
  pymolUrl: (id: string, model: string, scheme = "ss") => `/api/jobs/${id}/pymol/${model.split("/").map(enc).join("/")}?scheme=${scheme}`,
  /** report HTML autonomo (stampabile in PDF) di un modello (job model, loop, pipeline) o, senza `model`, di
   * un'analisi di qualità; `qmeanJob` = job qmean sulla stessa struttura da includere; `predictJob` = confronto con
   * una predizione (F9.3; senza, il server include il più recente sulla stessa struttura) */
  reportUrl: (id: string, model?: string | null, qmeanJob?: string | null, predictJob?: string | null) =>
    `/api/jobs/${id}/report${model ? `/${model.split("/").map(enc).join("/")}` : ""}${queryString({ qmean_job: qmeanJob, predict_job: predictJob })}`,
  /** genera il report, con la vista 3D se `body.image_png_base64` c'è, e lo restituisce come file da salvare */
  report: (id: string, model: string | null, body: ReportRequest = {}, qmeanJob?: string | null, predictJob?: string | null): Promise<ReportFile> =>
    requestFile(api.reportUrl(id, model, qmeanJob, predictJob), body, reportFallbackName(model, id)),
  archiveUrl: (id: string) => `/api/jobs/${id}/archive`,
  /** flusso SSE di un job (status, log dal byte `offset`, end); `offset` null = senza righe di log */
  jobEventsUrl: (id: string, offset: number | null) =>
    `/api/jobs/${id}/events?${offset === null ? "log=false" : `offset=${Math.max(0, Math.floor(offset))}`}`,
  /** flusso SSE dell'elenco dei job (ready, job, items, active, deleted, resync), tutti o di un progetto */
  jobsEventsUrl: (projectId?: string) => `/api/jobs/events${projectId ? `?project_id=${enc(projectId)}` : ""}`,

  // calcoli
  search: (body: { project_id?: string; query: { name: string; sequence: string }; engine: string; evalue: number; max_hits: number; taxid?: string; use_cache?: boolean; uniprot?: string }) =>
    post<JobSummary>("/api/search", body),
  align: (body: Record<string, unknown>) => post<JobSummary>("/api/align", body),
  /** allineamento strutturale di B su A (riferimento) con il confronto per residuo; catena vuota = prima catena */
  structuralAlign: (body: { project_id?: string; structures: [StructureSource, StructureSource]; tool?: string; use_cache?: boolean }) =>
    post<JobSummary>("/api/align", { ...body, mode: "structural" }),
  /** `complex` (F5.3): un allineamento per catena del template (target non serve) */
  align2d: (body: { project_id?: string; target?: { name: string; sequence: string } | null; templates: TemplateRef[]; complex?: ComplexRequest | null }) =>
    post<JobSummary>("/api/align2d", body),
  /** `options.hetatm` + `options.ligands`: ligandi, cofattori e metalli da trasferire (elenco = solo quei gruppi);
   *  `complex` + `options.symmetry`: bersaglio a più catene su un solo template (F5.3) */
  model: (body: { project_id?: string; target?: { name: string; sequence: string } | null; alignment?: string | null; templates: TemplateRef[]; options: Record<string, unknown> & { hetatm?: boolean; ligands?: LigandRef[]; symmetry?: boolean; symmetry_weight?: number }; quality: string; extended_quality?: boolean; complex?: ComplexRequest | null }) =>
    post<JobSummary>("/api/model", body),
  loop: (body: { project_id?: string; source: StructureSource; chain: string; start: number; end: number; models: number; level: string }) =>
    post<JobSummary>("/api/model/loop", body),
  quality: (body: { project_id?: string; source: StructureSource; dope: boolean; procheck?: boolean; prosa?: boolean }) =>
    post<JobSummary>("/api/quality", body),
  qualityUpload: (file: File, projectId?: string, dope = true, procheck = true, prosa = true) => {
    const form = new FormData();
    form.append("file", file);
    form.append("dope", String(dope));
    form.append("procheck", String(procheck));
    form.append("prosa", String(prosa));
    if (projectId) form.append("project_id", projectId);
    return request<JobSummary>("POST", "/api/quality/upload", form, true);
  },
  qmean: (body: { project_id?: string; source: StructureSource; mode?: "local" | "remote"; fallback?: boolean; database?: string }) =>
    post<JobSummary>("/api/qmean", body),
  pipeline: (body: PipelineRequest) => post<JobSummary>("/api/pipeline", body),
  pipelineItems: (id: string, q: { page: number; size: number; status?: string; sort?: string; direction?: string; q?: string }) => {
    const params = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && v !== "" && params.set(k, String(v)));
    return get<PipelinePage>(`/api/pipeline/${id}/items?${params}`);
  },
  pipelineItem: (id: string, idx: number) => get<PipelineItem & { sequence: string }>(`/api/pipeline/${id}/items/${idx}`),
  pipelineCsvUrl: (id: string) => `/api/pipeline/${id}/export.csv`,
  pipelineZipUrl: (id: string) => `/api/pipeline/${id}/export.zip`,

  // etichette, preferiti e ricerca globale (F8b)
  /** etichette in uso con i conteggi (tutte o di un tipo) */
  tags: (entityType?: EntityType) => get<TagCount[]>(`/api/tags${queryString({ entity_type: entityType })}`),
  entityLabels: (type: EntityType, id: string) => get<EntityLabels>(`/api/tags/${type}/${enc(id)}`),
  addTag: (type: EntityType, id: string, tag: string) => post<EntityLabels>(`/api/tags/${type}/${enc(id)}`, { tag }),
  removeTag: (type: EntityType, id: string, tag: string) => del<EntityLabels>(`/api/tags/${type}/${enc(id)}/${enc(tag)}`),
  setFavorite: (type: EntityType, id: string, on: boolean) =>
    on ? put<EntityLabels>(`/api/favorites/${type}/${enc(id)}`, {}) : del<EntityLabels>(`/api/favorites/${type}/${enc(id)}`),
  favorites: (entityType?: EntityType) => get<FavoriteEntry[]>(`/api/favorites${queryString({ entity_type: entityType })}`),
  /** ricerca globale: `q` vuoto con `tag` = elementi con l'etichetta, senza nulla = preferiti */
  globalSearch: (q: { q: string; tag?: string | null; type?: SearchGroupType[]; limit?: number }) =>
    get<SearchResponse>(`/api/search${queryString({ q: q.q, tag: q.tag ?? undefined, type: q.type?.length ? q.type : undefined, limit: q.limit })}`),

  // analisi strutturale (F7): interazioni, interfacce, conservazione; il risultato è quello del job
  analysis: (body: AnalysisRequest) => post<JobSummary>("/api/analysis", body),
  /** analisi strutturale completata più recente sulla struttura (stessa sorgente e stesso file), o null */
  latestAnalysis: (source: StructureSource) => {
    const params = new URLSearchParams({ type: source.type });
    for (const key of ["id", "job_id", "file"] as const) if (source[key]) params.set(key, String(source[key]));
    return get<{ job_id: string | null; finished_at: string | null; active_job_id?: string | null }>(`/api/analysis/latest?${params}`);
  },

  // tasche (fpocket), elettrostatica (PDB2PQR + APBS) e rifinitura energetica (OpenMM): job; risultati in api.job
  pockets: (body: { project_id?: string; source: StructureSource }) => post<JobSummary>("/api/pockets", body),
  electrostatics: (body: { project_id?: string; source: StructureSource; forcefield?: "PARSE" | "AMBER"; ph?: number }) =>
    post<JobSummary>("/api/electrostatics", body),
  refine: (body: { project_id?: string; source: StructureSource; md_ps?: number; hydrogens?: boolean }) => post<JobSummary>("/api/refine", body),
  /** calcolo completato più recente (`pockets` o `electrostatics`) sulla struttura, e quello in coda o in corso */
  latestToolJob: (kind: "pockets" | "electrostatics", source: StructureSource) => {
    const params = new URLSearchParams({ type: source.type });
    for (const key of ["id", "job_id", "file"] as const) if (source[key]) params.set(key, String(source[key]));
    return get<LatestToolJob>(`/api/${kind}/latest?${params}`);
  },

  // riproducibilità (F9.4): pacchetto di un job e riesecuzione da pacchetto
  packageUrl: (id: string) => `/api/jobs/${enc(id)}/package`,
  /** pacchetto ZIP di un job completato (manifest, input, output, script, LEGGIMI), da salvare */
  downloadPackage: (id: string): Promise<ReportFile> =>
    downloadFile(api.packageUrl(id), `bioloom_riproducibile_${id}.zip`),
  /** carica e verifica un pacchetto: anteprima del manifest con `package_id` (conservato 24 ore) */
  inspectPackage: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<PackageSummary>("POST", "/api/jobs/rerun/inspect", form, true);
  },
  /** riesecuzione di un pacchetto già caricato (`packageId`) o di un file */
  rerunPackage: (o: { packageId?: string; file?: File; projectId?: string | null }) => {
    const form = new FormData();
    if (o.file) form.append("file", o.file);
    if (o.packageId) form.append("package_id", o.packageId);
    if (o.projectId) form.append("project_id", o.projectId);
    return request<JobSummary>("POST", "/api/jobs/rerun", form, true);
  },
  discardPackage: (packageId: string) => del<{ deleted: string }>(`/api/jobs/rerun/${enc(packageId)}`),
  // mutagenesi in silico e scansione di alanina (F9.2): job; risultati in api.job
  mutate: (body: MutateRequest) => post<JobSummary>("/api/mutate", body),
  alascan: (body: AlaScanRequest) => post<JobSummary>("/api/alascan", body),
  /** varianti di UniProt dell'accessione mappate sulla numerazione della struttura (cache 7 giorni nel server) */
  uniprotVariants: (accession: string, source: StructureSource, chain?: string | null) => {
    const params = new URLSearchParams({ accession, type: source.type });
    for (const key of ["id", "job_id", "file"] as const) if (source[key]) params.set(key, String(source[key]));
    if (chain) params.set("chain", chain);
    return get<VariantMapping>(`/api/mutations/variants?${params}`);
  },
  /** job di mutagenesi e scansione di alanina sulla stessa struttura, dal più recente */
  mutationHistory: (source: StructureSource) => {
    const params = new URLSearchParams({ type: source.type });
    for (const key of ["id", "job_id", "file"] as const) if (source[key]) params.set(key, String(source[key]));
    return get<MutationHistoryItem[]>(`/api/mutations/history?${params}`);
  },
  // confronto con un predittore esterno (F9.3): job `predict`; risultato in api.job
  /** predizione (ESMFold: la sequenza lascia il computer, serve il consenso esmfold_remote) e confronto con il modello */
  predict: (body: PredictRequest) => post<JobSummary>("/api/predict", body),
  /** confronto con una predizione completato più recente sulla struttura, e quello in coda o in corso */
  latestPrediction: (source: StructureSource) => {
    const params = new URLSearchParams({ type: source.type });
    for (const key of ["id", "job_id", "file"] as const) if (source[key]) params.set(key, String(source[key]));
    return get<LatestToolJob>(`/api/predict/latest?${params}`);
  },
};

export type { Align2dResult, AlignResult };
