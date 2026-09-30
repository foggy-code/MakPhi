// Tipi delle risposte dell'API BioLoom v4 (specchio dei payload del backend).

export type JobStatus = "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
export type JobKind =
  | "search" | "align" | "align2d" | "model" | "loop" | "quality" | "qmean" | "pipeline"
  | "selftest" | "db_update_pdbaa" | "db_build_mmseqs" | "db_index_mmseqs" | "db_convert_uniclust" | "db_local_uniclust";

export type SearchEngine = "rcsb" | "ncbi" | "local" | "mmseqs";

export interface JobSummary {
  id: string;
  kind: JobKind;
  title: string;
  project_id: string | null;
  status: JobStatus;
  progress: number;
  stage: string;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface JobFile {
  path: string;
  size: number;
}

export interface Job<R = unknown> extends JobSummary {
  params: Record<string, unknown>;
  result: R | null;
  files: JobFile[];
  /** pipeline fallita o annullata con elementi non conclusi: si può riprendere */
  resumable: boolean;
  /** conteggi per i pulsanti di ripresa (solo pipeline fallite o annullate, altrimenti null) */
  resumable_items: ResumeItems | null;
}

/** Ambito della ripresa: tutti gli elementi non conclusi oppure solo i falliti. */
export type ResumeScope = "incomplete" | "failed";

export interface ResumeItems {
  total: number;
  /** non completati né filtrati: falliti, annullati, interrotti, in attesa */
  incomplete: number;
  failed: number;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  sequences?: number;
  documents?: number;
  jobs?: number;
}

export interface Sequence {
  id: string;
  project_id: string;
  name: string;
  sequence: string;
  source: string;
  meta: Record<string, unknown>;
  created_at: string;
  length: number;
}

export interface DocumentItem {
  id: string;
  project_id: string;
  name: string;
  kind: "pir" | "fasta" | "clustal" | "text";
  content?: string;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  size?: number;
}

export interface ToolStatus {
  label: string;
  use: string;
  path: string | null;
  available: boolean;
}

export interface SystemInfo {
  version: string;
  python: string;
  platform: string;
  data_dir: string;
  uptime: number;
  workers: number;
  modeller: {
    available: boolean;
    python: string | null;
    version: string | null;
    python_version?: string;
    attempts: { python: string; error: string }[];
  };
  tools: Record<string, ToolStatus>;
  services: { qmean_remote: boolean; contact_email: boolean };
  runtime?: {
    container: boolean;
    image: string;
    arch: string;
    auth: boolean;
    allow_shutdown: boolean;
    modeller_key: "file" | "env" | null;
  };
}

export interface LicenseStatus {
  modeller: {
    configured: boolean;
    source: "file" | "env" | null;
    masked: string;
    available: boolean;
    version: string | null;
    message: string | null;
  };
  prosa: {
    installed: boolean;
    license: boolean;
    error: string | null;
    program: string | null;
    expires: string | null;
    mac_required: string | null;
    mac_current: string | null;
    mac_ok: boolean;
  };
  procheck: { installed: boolean };
}

export interface UniclustDatabase {
  id: string;
  name: string;
  family: string;
  version: string | null;
  state: "ready" | "needs_conversion" | "converting" | "incomplete";
  problems: string[];
  format: "HH-suite 3" | "HH-suite 2";
  hhm: boolean;
  cs219_source: "database" | "converted" | null;
  legacy_cs219: boolean;
  size: number;
  /** filesystem del disco nel container; `shared` = condivisione di Docker Desktop o di rete (letture piccole lente) */
  filesystem?: string | null;
  shared?: boolean;
  /** copia di indici e cs219 nel volume locale di questo computer */
  local?: { state: "none" | "ready" | "stale" | "copying"; size: number; used: number; progress?: number };
  /** la memoria di Docker basta a HHblits con questo database? (null = non si sa) */
  memory?: "ok" | "swap" | "insufficient" | null;
}

export interface QmeanServiceStatus {
  configured: boolean;
  reachable: boolean;
  available: boolean;
  message: string | null;
  uniclust?: {
    valid: boolean; prefix: string | null; id?: string | null; version?: string | null; size?: number; hhm?: boolean; error: string | null;
    memory?: "ok" | "swap" | "insufficient" | null; memory_message?: string | null;
  };
  /** memoria del container QMEAN (byte) */
  memory?: { total: number; swap: number } | null;
  selected?: string | null;
  preferred?: string | null;
  databases: UniclustDatabase[];
  archives: { file: string; size: number }[];
  unsupported: { file: string; reason: string }[];
  /** copie locali di database non più collegati (spazio recuperabile) */
  local_orphans?: { id: string; size: number }[];
  qmtl?: { present: boolean; version: string | null };
  busy?: boolean;
  queued?: number;
  qmean_version?: string | null;
}

export interface ParsedRecord {
  name: string;
  description: string;
  sequence: string;
  aligned: string | null;
  length: number;
}

export interface ParsedFile {
  format: string;
  format_label: string;
  aligned: boolean;
  records: ParsedRecord[];
  warnings: string[];
  filename: string;
}

export interface UploadedStructure {
  source: StructureSource;
  label: string;
  filename: string;
  chains: { chain: string; residues: number; first: string; last: string }[];
}

export interface DatabaseStatus {
  pdbaa: { present: boolean; size?: number | null; updated?: string | null; usable: boolean; blast: boolean };
  mmseqs: { present: boolean; size?: number | null; updated?: string | null; usable: boolean; tool: boolean; indexed_here: boolean; index_size: number };
  qmean: QmeanServiceStatus;
}

export interface SelfTestResult {
  checks: { tool: string; status: "ok" | "missing" | "error"; detail: string; seconds: number }[];
  counts: { ok: number; missing: number; error: number };
}

export interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
}

export interface SystemStats {
  cpu_percent: number;
  cpu_count: number;
  memory: { total: number; used: number; percent: number };
  disk: { total: number; free: number; percent: number };
  process_memory: number;
  active_jobs: number;
}

export interface Settings {
  contact_email: string;
  modeller_python: string;
  max_workers: number;
  job_timeout_minutes: number;
  qmean_remote: boolean;
  qmean_mode: "local" | "remote";
  qmean_fallback_remote: boolean;
  qmean_uniclust: string;
  qmean_timeout_minutes: number;
  default_search_engine: SearchEngine;
  keep_intermediates: boolean;
  uploads_retention_days: number;
  /** riusa i risultati di ricerche e allineamenti già calcolati (cache dei calcoli) */
  compute_cache: boolean;
}

export interface Example {
  name: string;
  title: string;
  accession?: string;
  sequence?: string;
  note: string;
}

export interface SequenceAnalysis {
  sequence: string;
  kind: string;
  length: number;
  properties?: {
    molecular_weight: number;
    isoelectric_point: number;
    instability_index: number;
    gravy: number;
    aromaticity: number;
    extinction_coefficient: number;
    propensity: { helix: number; turn: number; sheet: number };
    composition: Record<string, number>;
  };
  secondary_prediction?: { method: string; prediction: string; segments: SSSegment[] };
}

export interface SSSegment {
  type: "helix" | "strand";
  start: number;
  end: number;
}

export interface UniProtEntry {
  accession: string;
  entry_name: string;
  reviewed: boolean;
  protein_name: string;
  genes: string[];
  organism: string;
  taxonomy_id: number | null;
  existence: string;
  sequence: string;
  length: number;
  mass: number | null;
  comments: Record<string, string[]>;
  features: { type: string; start: number; end: number; description: string; ligand: string }[];
  pdb: { id: string; method: string; resolution: number | null; chains: string }[];
  go: { id: string; term: string }[];
  keywords: string[];
}

export interface UniProtHit {
  accession: string;
  entry_name: string;
  protein_name: string;
  genes: string[];
  organism: string;
  length: number;
  reviewed: boolean;
}

export interface InterProDomain {
  accession: string;
  name: string;
  type: string;
  locations: { start: number; end: number }[];
}

export interface TemplateItem {
  id: string;
  format: string;
  size: number;
  source: string;
  added_at: string | null;
  title: string;
  method: string;
  resolution: number | null;
  chains: string[];
}

export interface ChainSummary {
  chain: string;
  first: string;
  last: string;
  residues: number;
  sequence: string;
  breaks: { after: string; before: string }[];
  ligands: number;
  ss?: string;
  missing?: number;
}

export interface TemplateAnalysis {
  id: string;
  format: string;
  header: {
    title: string;
    method: string;
    resolution: number | null;
    missing_residues: { resname: string; chain: string; resnum: string }[];
    seqres: Record<string, string>;
    secondary_structure: { type: string; chain: string; start: number; end: number }[];
  };
  chains: ChainSummary[];
  ligands: { resname: string; chain: string; resnum: string; atoms: number; additive: boolean }[];
  stats: { chains: number; residues: number; heavy_atoms: number };
  models: number;
  meta: TemplateItem;
}

export interface SearchHit {
  pdb_id: string;
  chain: string;
  template_id: string;
  title: string;
  description: string;
  organism: string;
  method: string;
  resolution: number | null;
  identity: number;
  positives: number | null;
  coverage: number;
  evalue: number;
  bitscore: number;
  align_length: number;
  query_start: number;
  query_end: number;
  subject_start: number;
  subject_end: number;
  query_aligned: string;
  subject_aligned: string;
  subject_length: number;
  source: string;
}

/** Provenienza di un risultato: presente e vera solo se riusato dalla cache dei calcoli. */
export interface CacheInfo {
  cached?: boolean;
  /** data ISO (UTC) del calcolo riusato */
  cached_at?: string;
}

export interface SearchResult extends CacheInfo {
  engine: string;
  engine_label: string;
  query_length: number;
  hits: SearchHit[];
  query: { name: string; sequence: string; length: number };
  best: string | null;
}

export interface AlignStats {
  length: number;
  identities: number;
  identity: number;
  identity_over_shorter: number;
  similarity: number;
  gaps: number;
  gap_percent: number;
  coverage_a: number;
  coverage_b: number;
}

export interface AlignResult extends CacheInfo {
  mode: "pairwise" | "multiple" | "structural";
  method: string;
  ids: string[];
  aligned: string[];
  score?: number;
  stats?: AlignStats | null;
  conservation?: string;
  identity_matrix?: { ids: string[]; values: number[][] };
  tm_score?: number;
  tm_score_b?: number;
  rmsd?: number;
  rmsd_close?: number | null;
  aligned_length?: number;
  close_pairs?: number;
  lengths?: number[];
  superposed_file?: string;
  note?: string;
}

export interface PreparedTemplate {
  id: string;
  code: string;
  file: string;
  chains: string[];
  first: string;
  last: string;
  sequence: string;
  ligands: number;
}

export interface PirReport {
  valid: boolean;
  errors: string[];
  warnings: string[];
  entries: { code: string; kind: string; length: number; residues: number; chains: number }[];
}

export interface QualitySummary {
  grade: string;
  grade_score: number;
  rama_favored: number;
  rama_outliers: number;
  clashscore: number;
  helix_pct: number;
  strand_pct: number;
  dope: number | null;
  zdope: number | null;
  prosa_z?: number | null;
  procheck_core?: number | null;
  procheck_g_overall?: number | null;
  residues: number;
}

export interface ModelEntry {
  file: string;
  failure: string | null;
  molpdf: number | null;
  dope: number | null;
  ga341?: number | null;
  zdope: number | null;
  kind: "model" | "loop";
  best: boolean;
  quality?: QualitySummary;
}

export interface ModelResult {
  target: { code: string; sequence: string };
  templates: PreparedTemplate[];
  alignment: string;
  alignment_report: PirReport;
  options: Record<string, unknown>;
  models: ModelEntry[];
  best_model: string;
  warnings: string[];
}

export interface Align2dResult {
  pir: string;
  pap: string;
  report: PirReport;
  templates: PreparedTemplate[];
}

export interface LoopResult {
  source: string;
  region: [string, string];
  models: ModelEntry[];
  best_model: string;
}

export type RamaRect = [number, number, number, number];

export interface ResidueRow {
  chain: string;
  resnum: string;
  resname: string;
  aa: string;
  ss: string;
  ss3: "H" | "E" | "C";
  rsa: number | null;
  bfactor: number | null;
  phi: number | null;
  psi: number | null;
  rama: "favored" | "allowed" | "outlier" | null;
  clashes: number;
  dope: number | null;
}

export interface QualityReport {
  source?: StructureSource;
  label?: string;
  structure: {
    file: string;
    title: string;
    method: string;
    resolution: number | null;
    chains: number;
    residues: number;
    heavy_atoms: number;
    chains_detail: ChainSummary[];
  };
  grade: { score: number; grade: string; penalties: { ramachandran: number; clashes: number; dope: number } };
  ramachandran: {
    summary: {
      total: number;
      favored: number;
      allowed: number;
      outlier: number;
      favored_pct: number;
      allowed_pct: number;
      outlier_pct: number;
      cis_non_proline: number;
    };
    points: {
      chain: string;
      resnum: string;
      resname: string;
      aa: string;
      phi: number;
      psi: number;
      class: string;
      region: "favored" | "allowed" | "outlier";
    }[];
    regions: Record<string, { favored: RamaRect[]; allowed: RamaRect[] }>;
  };
  clashes: {
    clashscore: number;
    count: number;
    atoms: number;
    clashes: { atom1: string; atom2: string; distance: number; overlap: number }[];
  };
  secondary_structure: {
    method: string;
    helix_pct: number;
    strand_pct: number;
    coil_pct: number;
    counts: Record<string, number>;
    detail: Record<string, number>;
  };
  sasa: { method: string; mean_rsa: number | null; buried_pct: number | null };
  bfactor: { mean: number | null; max: number | null };
  dope: { score: number | null; zscore: number | null; profile: { index: number; resname: string; energy: number }[] } | null;
  procheck?: ProcheckResult | null;
  prosa?: ProsaResult | null;
  residues: ResidueRow[];
  warnings: string[];
}

export interface ProcheckResult {
  ramachandran: { core: number | null; allowed: number | null; generous: number | null; disallowed: number | null };
  g_factors: { dihedrals: number | null; covalent: number | null; overall: number | null };
  residues: number | null;
  bad_contacts: number | null;
  max_deviation: number | null;
  bond_len_angle: number | null;
  mc_bond_lengths_ok: number | null;
  mc_bond_angles_ok: number | null;
  planar_groups_ok: number | null;
  summary: string;
  files: string[];
}

export interface ProsaResult {
  zscore: number;
  zscore_pair: number;
  zscore_surface: number;
  profile: { index: number; energy: number; energy_w10: number | null }[];
}

export interface QmeanResult {
  label: string;
  qmean4_z: number | null;
  qmean4: number | null;
  qmean6_z: number | null;
  qmeandisco: number | null;
  components: Record<string, number>;
  local: { chain: string; index: number; score: number | null }[];
  url?: string;
  source?: "local" | "swissmodel";
  method?: string | null;
  uniclust?: string | null;
  uniclust_version?: string | null;
  fallback?: { from: "local"; reason: string };
}

export interface StructureSource {
  type: "template" | "job" | "upload";
  id?: string;
  job_id?: string;
  file?: string;
  chain?: string;
}

export interface PipelineItem {
  idx: number;
  name: string;
  length: number;
  status: "pending" | "running" | "searching" | "modeling" | "completed" | "failed" | "cancelled" | "filtered";
  stage: string;
  template: string | null;
  template_source: string | null;
  identity: number | null;
  coverage: number | null;
  evalue: number | null;
  twilight: number;
  best_model: string | null;
  dope: number | null;
  ga341: number | null;
  grade: string | null;
  grade_score: number | null;
  rama_favored: number | null;
  clashscore: number | null;
  /** metriche del modello; `search_cached_at` = ricerca del template riusata dalla cache (data ISO) */
  metrics: Record<string, unknown> & { search_cached_at?: string };
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface PipelineResult {
  total: number;
  counts: Record<string, number>;
  /** elementi la cui ricerca del template viene dalla cache dei calcoli */
  search_cache_hits?: number;
}

export interface PipelinePage {
  items: PipelineItem[];
  total: number;
  page: number;
  pages: number;
  size: number;
  counts: Record<string, number>;
}

export interface TemplateRef {
  id: string;
  chains: string[];
  hetatm?: boolean;
}
