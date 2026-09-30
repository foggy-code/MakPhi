import type {
  Align2dResult,
  AlignResult,
  AuthStatus,
  DatabaseStatus,
  DocumentItem,
  LicenseStatus,
  ParsedFile,
  UploadedStructure,
  Example,
  InterProDomain,
  Job,
  JobSummary,
  PipelineItem,
  PipelinePage,
  PirReport,
  Project,
  QualityReport,
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
} from "./types";

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
  cleanup: () => post<{ uploads: number; cache: number; scratch: number; freed_bytes: number }>("/api/system/cleanup"),
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
  projects: () => get<Project[]>("/api/projects"),
  createProject: (name: string, description = "") => post<Project>("/api/projects", { name, description }),
  updateProject: (id: string, data: Partial<Project>) => patch<Project>(`/api/projects/${id}`, data),
  deleteProject: (id: string) => del(`/api/projects/${id}`),
  sequences: (pid: string) => get<Sequence[]>(`/api/projects/${pid}/sequences`),
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

  // template
  templates: () => get<TemplateItem[]>("/api/templates"),
  fetchTemplate: (pdb_id: string, format = "pdb") => post<TemplateItem & { cached: boolean }>("/api/templates/fetch", { pdb_id, format }),
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

  // job
  jobs: (q: { project_id?: string; kind?: string; status?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && v !== "" && params.set(k, String(v)));
    return get<JobSummary[]>(`/api/jobs?${params}`);
  },
  job: <R,>(id: string) => get<Job<R>>(`/api/jobs/${id}`),
  jobStatus: (id: string) => get<JobSummary>(`/api/jobs/${id}/status`),
  jobLog: (id: string, offset = 0) => get<{ text: string; offset: number; size: number }>(`/api/jobs/${id}/log?offset=${offset}`),
  cancelJob: (id: string) => post<JobSummary>(`/api/jobs/${id}/cancel`),
  resumeJob: (id: string, scope: ResumeScope = "incomplete") => post<JobSummary>(`/api/jobs/${id}/resume`, { scope }),
  deleteJob: (id: string) => del(`/api/jobs/${id}`),
  jobFileUrl: (id: string, path: string, download = false) =>
    `/api/jobs/${id}/files/${path.split("/").map(enc).join("/")}${download ? "?download=true" : ""}`,
  jobFileText: (id: string, path: string) => get<string>(`/api/jobs/${id}/files/${path.split("/").map(enc).join("/")}`),
  modelQuality: (id: string, model: string) => get<QualityReport>(`/api/jobs/${id}/quality/${model.split("/").map(enc).join("/")}`),
  pymolUrl: (id: string, model: string, scheme = "ss") => `/api/jobs/${id}/pymol/${model.split("/").map(enc).join("/")}?scheme=${scheme}`,
  archiveUrl: (id: string) => `/api/jobs/${id}/archive`,

  // calcoli
  search: (body: { project_id?: string; query: { name: string; sequence: string }; engine: string; evalue: number; max_hits: number; taxid?: string; use_cache?: boolean }) =>
    post<JobSummary>("/api/search", body),
  align: (body: Record<string, unknown>) => post<JobSummary>("/api/align", body),
  align2d: (body: { project_id?: string; target: { name: string; sequence: string }; templates: TemplateRef[] }) =>
    post<JobSummary>("/api/align2d", body),
  model: (body: { project_id?: string; target?: { name: string; sequence: string } | null; alignment?: string | null; templates: TemplateRef[]; options: Record<string, unknown>; quality: string; extended_quality?: boolean }) =>
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
  pipeline: (body: Record<string, unknown>) => post<JobSummary>("/api/pipeline", body),
  pipelineItems: (id: string, q: { page: number; size: number; status?: string; sort?: string; direction?: string; q?: string }) => {
    const params = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && v !== "" && params.set(k, String(v)));
    return get<PipelinePage>(`/api/pipeline/${id}/items?${params}`);
  },
  pipelineItem: (id: string, idx: number) => get<PipelineItem & { sequence: string }>(`/api/pipeline/${id}/items/${idx}`),
  pipelineCsvUrl: (id: string) => `/api/pipeline/${id}/export.csv`,
  pipelineZipUrl: (id: string) => `/api/pipeline/${id}/export.zip`,
};

export type { Align2dResult, AlignResult };
