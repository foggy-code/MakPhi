// Tipi delle risposte dell'API BioLoom v4 (specchio dei payload del backend).

export type JobStatus = "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
export type JobKind =
  | "search" | "align" | "align2d" | "model" | "loop" | "quality" | "qmean" | "pipeline"
  | "selftest" | "db_update_pdbaa" | "db_build_mmseqs" | "db_index_mmseqs" | "db_convert_uniclust" | "db_local_uniclust"
  | "analysis";

export type SearchEngine = "rcsb" | "ncbi" | "local" | "mmseqs" | "alphafold";

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
  /** secondi rimanenti stimati, solo per i job in esecuzione; null se non stimabile */
  eta_seconds?: number | null;
  /** posizione (da 1) di un job in coda nella coda del suo pool, in ordine di partenza; null per gli altri */
  queue_position?: number | null;
}

/** Evento "log" del flusso SSE di un job: righe complete dal byte `start` al byte `offset` del log. */
export interface JobLogChunk {
  start: number;
  offset: number;
  text: string;
}

/** Evento "items" del flusso dell'elenco: elementi di una pipeline cambiati (`idx` null = molti insieme). */
export interface JobItemsEvent {
  job_id: string;
  idx: number[] | null;
}

export interface JobFile {
  path: string;
  size: number;
}

export interface Job<R = unknown> extends JobSummary {
  params: Record<string, unknown>;
  result: R | null;
  files: JobFile[];
  /** pipeline terminata con elementi non conclusi (anche completata, con elementi falliti): si può riprendere */
  resumable: boolean;
  /** conteggi per i pulsanti di ripresa (solo pipeline terminate, altrimenti null) */
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
  /** memoria da assegnare a Docker per il database scelto: misurata (UniRef30_2020_06) o stimata dalla dimensione;
   *  `sufficient` = esito del controllo del server (giudizio del worker compreso), null se non si sa */
  memory_required?: { bytes: number; basis: "measured" | "estimate"; sufficient?: boolean | null } | null;
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
  /** budget di CPU condiviso fra i job: slot totali, occupati dai processi di calcolo, processi in attesa */
  cpu_slots?: { total: number; used: number; waiting: number };
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

// ---------------------------------------------------------------------------------- disordine e domini (domain/disorder.py, F5.5)
export interface DisorderRegion {
  /** 1-based, estremi compresi */
  start: number;
  end: number;
  length: number;
  /** residui con FoldIndex < 0 (i tollerati non contano) */
  disordered: number;
  mean_score: number;
}

export interface LowComplexityRegion {
  start: number;
  end: number;
  length: number;
  /** lettere più frequenti: "Q 27" */
  composition: string;
}

/** POST /api/bio/disorder: FoldIndex per residuo, regioni disordinate lunghe e regioni a bassa complessità (SEG). */
export interface DisorderPrediction {
  method: string;
  length: number;
  window: number;
  scale: { name: string; threshold: number; disordered: "below"; formula: string; min: number | null; max: number | null };
  /** FoldIndex per residuo: sotto 0 probabilmente disordinato */
  scores: (number | null)[];
  regions: DisorderRegion[];
  /** frazione dei residui nelle regioni disordinate lunghe */
  fraction: number;
  /** frazione dei residui con FoldIndex < 0, anche fuori dalle regioni */
  below_fraction: number;
  params: { window: number; threshold: number; min_region: number; max_gap: number };
  low_complexity: { method: string; params: { window: number; k1: number; k2: number }; regions: LowComplexityRegion[]; fraction: number };
  limits: string[];
}

/** Dominio di InterPro riportato sul bersaglio (voci dello stesso dominio fuse). */
export interface TargetDomain {
  accession: string;
  accessions: string[];
  name: string;
  type: string;
  start: number;
  end: number;
}

export type CoverageWarningKind = "disorder_uncovered" | "disorder_covered" | "domain_uncovered" | "domain_partial";

export interface CoverageWarning {
  kind: CoverageWarningKind;
  tone: "warn" | "info";
  start: number;
  end: number;
  text: string;
}

export interface CoverageAssessment {
  /** % dei residui del bersaglio con un residuo del template di fronte */
  coverage: number;
  covered: [number, number][];
  warnings: CoverageWarning[];
  domains: (TargetDomain & { covered_pct: number; status: "covered" | "partial" | "uncovered" })[];
  suggestion: string | null;
}

export interface CoverageTemplate extends Partial<CoverageAssessment> {
  label: string;
  id?: string;
  chains?: string[];
  note?: string | null;
  /** template non valutabile (assente dalla libreria, catena inesistente) */
  error?: string;
}

/** POST /api/bio/coverage: avvisi prima di modellare. */
export interface CoverageCheck {
  length: number;
  note: string | null;
  disorder: DisorderPrediction | null;
  domains: TargetDomain[];
  domains_info: { source: string | null; accession: string | null; available: boolean; note: string | null } | null;
  /** template della libreria o del PIR, poi gli intervalli (hit della ricerca), nell'ordine della richiesta */
  templates: CoverageTemplate[];
  /** template (o PIR) usati insieme; null per i soli intervalli */
  combined: (CoverageAssessment & { label: string }) | null;
}

export interface CoverageRequest {
  sequence?: string;
  accession?: string | null;
  templates?: TemplateRef[];
  alignment?: string | null;
  ranges?: { label: string; start: number; end: number }[];
  /** soglia del ritaglio pLDDT dei template AlphaFold (come in modellazione); null = nessun ritaglio */
  plddt_min?: number | null;
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
  /** modello predetto (AlphaFold DB, o file di AlphaFold caricato): non una struttura sperimentale */
  predicted?: boolean;
  /** pLDDT medio (0–100) dei modelli predetti */
  plddt_mean?: number | null;
  plddt_fractions?: PlddtFractions | null;
  /** solo nella scheda del template (meta): "modello predetto, non struttura sperimentale" */
  note?: string;
  /** voce di AlphaFold DB da cui viene il modello (versione, intervallo UniProt coperto...) */
  alphafold?: AlphaFoldInfo | null;
  /** assemblaggio biologico importato (F5.3): template d'origine e origine di ogni catena */
  assembly?: TemplateAssemblyMeta | null;
}

/** Frazione di residui per fascia di pLDDT: ≥ 90, 70–90, 50–70, < 50. */
export interface PlddtFractions {
  very_high: number | null;
  confident: number | null;
  low: number | null;
  very_low: number | null;
}

export interface AlphaFoldInfo {
  entry_id: string;
  accession: string;
  fragment: number;
  version: number | null;
  uniprot_start: number | null;
  uniprot_end: number | null;
  uniprot_id: string;
  gene: string;
  description: string;
  organism: string;
  tax_id: number | null;
  created: string;
  tool: string;
  pae_url: string | null;
}

/** Residui tolti da un template AlphaFold perché con pLDDT sotto la soglia (gap nell'allineamento). */
export interface PlddtTrim {
  threshold: number;
  total: number;
  /** totale: sotto soglia + frammenti isolati */
  removed: number;
  /** sotto soglia (assente nei risultati precedenti: = removed) */
  removed_threshold?: number;
  /** tratti sopra soglia più corti di `min_fragment` residui rimasti isolati tra residui tolti */
  removed_fragments?: number;
  min_fragment?: number;
  kept: number;
  mean_before: number | null;
  mean_after: number | null;
  /** [primo, ultimo, catena] nella numerazione del template */
  removed_ranges: [string, string, string][];
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
  /** gruppi eteroatomici non d'acqua con la classificazione (domain/ligands.candidates) */
  ligands: HeteroGroup[];
  stats: { chains: number; residues: number; heavy_atoms: number };
  models: number;
  meta: TemplateItem;
}

// ---------------------------------------------------------------------------------- ligandi (domain/ligands.py)
export type LigandClass = "metal" | "cofactor" | "additive" | "ligand" | "modified";
export type LigandStatus = "buono" | "da controllare" | "problematico";

/** Gruppo eteroatomico di un template, candidato al trasferimento nel modello. */
export interface HeteroGroup {
  resname: string;
  chain: string;
  resnum: string;
  atoms: number;
  /** additivo comune di cristallizzazione (campo precedente, per compatibilità) */
  additive: boolean;
  /** assenti nelle analisi in cache delle versioni precedenti (si ricalcolano alla prima lettura) */
  class?: LigandClass;
  /** metallo, cofattore noto, additivo, altro ligando, residuo modificato */
  label?: string;
  /** scelto per default nella modellazione (no additivi, ioni monovalenti, residui modificati) */
  default?: boolean;
  transferable?: boolean;
  /** perché non è scelto per default */
  reason?: string;
  element?: string;
  description?: string;
}

/** Gruppo da trasferire (opzione `ligands` della modellazione). */
export interface LigandRef {
  template?: string;
  chain: string;
  resnum: string;
  resname: string;
}

/** Donatore di un metallo nel sito del template (per il confronto dopo la modellazione). */
export interface TemplateSiteDonor {
  chain: string;
  resnum: string;
  resname: string;
  atom: string;
  protein: boolean;
  category: string;
  distance: number;
  water: boolean;
}

/** Gruppo trasferito da un template preparato, con il sito nel file originale. */
export interface TransferredGroup {
  resname: string;
  chain: string;
  resnum: string;
  atoms: number;
  class: LigandClass;
  label: string;
  metals: { atom: string; element: string; donors: TemplateSiteDonor[] }[];
  links: { atom: string; partner: { chain: string; resnum: string; resname: string; atom: string }; distance: number }[];
  /** residui della proteina entro 4 Å nel template */
  contacts?: { chain: string; resnum: string; resname: string }[];
}

export interface LigandDonor {
  chain: string;
  resnum: string;
  resname: string;
  atom: string;
  protein: boolean;
  label: string;
  category: string;
  donor: string;
  distance: number;
  /** distanza attesa (Harding 2006); null = donatore insolito o metallo senza riferimenti */
  expected: number | null;
  deviation?: number;
  status: "nella norma" | "lunga" | "corta" | "troppo corta" | "insolito" | "senza riferimento";
  water: boolean;
  same_group: boolean;
  coord: [number, number, number];
}

export interface MetalSite {
  atom: string;
  element: string;
  symbol: string;
  coord: [number, number, number];
  coordination_number: number;
  donors: LigandDonor[];
  geometry: { key: string; name: string; positions: number; grmsd: number; vacancy: number } | null;
  cn_range: [number, number] | null;
  reference: boolean;
  /** solo nei modelli: confronto con i donatori del template riportati sul bersaglio */
  template_site?: {
    donors: { template: string; atom: string; target: string | null; status: "conservato" | "perso" | "sostituito" | "non allineato" | "non modellato" }[];
    protein_donors: number;
    conserved: number;
    waters: number;
    new: string[];
  };
}

export interface LigandGroupCheck {
  resname: string;
  chain: string;
  resnum: string;
  atoms: number;
  class: LigandClass;
  label: string;
  description?: string;
  status: LigandStatus;
  summary: string;
  reasons: string[];
  contacts: { chain: string; resnum: string; resname: string; distance: number }[];
  clashes: { atom: string; partner: string; distance: number }[];
  links: { atom: string; partner: string; distance: number; confirmed: boolean }[];
  metals: MetalSite[];
  /** gruppo del template da cui viene (modelli) */
  template?: { code: string; group: string };
}

/** Verifica di ligandi, cofattori e metalli (campo `ligands` del rapporto di qualità). */
export interface LigandReport {
  version: number;
  groups: LigandGroupCheck[];
  counts: Record<LigandStatus, number>;
  status: LigandStatus;
  template_compared: boolean;
  thresholds: Record<string, number>;
  notes: string[];
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
  /** motore alphafold: modello predetto; `pdb_id` e `template_id` sono l'id della libreria (AF-<ACC>-F<n>) */
  predicted?: boolean;
  plddt?: number | null;
  uniprot_accession?: string;
  uniprot_start?: number | null;
  uniprot_end?: number | null;
  model_version?: number | null;
  /** metadati RCSB (motori sul PDB, dalla versione con la scelta ragionata dei template) */
  rfree?: number | null;
  /** data di rilascio AAAA-MM-GG, vuota se non nota */
  released?: string;
  /** codici chimici dei componenti non polimerici; null = non noti */
  ligands?: string[] | null;
  /** assemblaggio biologico 1: "Monomer", "Homo 2-mer"… */
  oligomeric_state?: string;
  oligomeric_count?: number | null;
  /** punteggio composito 0–100 (domain/template_rank.py); assente nei risultati delle versioni precedenti */
  rank_score?: number;
  rank_band?: RankBand;
  /** identità sotto la curva di Rost 1999 per la lunghezza allineata */
  rank_twilight?: boolean;
  rank_reasons?: RankReason[];
}

export type RankBand = "buona" | "intermedia" | "scarsa";

/** Una componente del punteggio di un template, con la motivazione. */
export interface RankReason {
  key: "identity" | "coverage" | "evalue" | "resolution" | "rfree" | "method" | "gaps" | "date" | "ligands" | "oligomer";
  label: string;
  /** 0–100; null = non disponibile, non applicabile o informativa (peso 0) */
  score: number | null;
  weight: number;
  text: string;
}

/** Template consigliato (il principale o uno complementare). */
export interface SuggestedTemplate {
  template_id: string;
  pdb_id: string;
  chain: string;
  rank_score: number;
  identity: number | null;
  coverage: number | null;
  predicted: boolean;
  role: "principale" | "complementare";
  reason: string;
  /** residui del bersaglio aggiunti (o con identità locale migliorata) e loro intervalli */
  new_residues: number;
  ranges: string;
}

/** Selezione dei template complementari (template_rank.select_complementary). */
export interface TemplateSuggestion {
  templates: SuggestedTemplate[];
  excluded: { template_id: string; reason: string }[];
  /** candidati non esaminati perché il numero massimo di template era già raggiunto */
  not_examined: number;
  /** copertura complessiva (%) e residui coperti dall'insieme */
  coverage: number;
  covered_residues: number;
  query_length: number;
  max_templates: number;
  min_new_residues: number;
  version: number;
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
  /** motore alphafold: accessione UniProt usata (vuota = proteine trovate con NCBI BLAST su Swiss-Prot) */
  uniprot?: string;
  /** motore alphafold: proteine senza modello, EBI non disponibile per alcune (risultato parziale) */
  notes?: string[];
  /** template consigliati con motivazione (assente nei risultati delle versioni precedenti) */
  suggested?: TemplateSuggestion;
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
  /** PDB per il viewer: catena A = struttura A (riferimento), catena B = struttura B sovrapposta */
  superposed_file?: string;
  note?: string;
  // confronto per residuo (strutturale; assente nei risultati precedenti o se la mappatura non è ricostruibile)
  /** coppie allineate nell'ordine di A, con distanza dei Cα dopo la sovrapposizione del TM-score e lDDT del residuo di A */
  pairs?: StructuralPair[];
  /** GDT per soglia (0,5, 1, 2, 4, 8 Å): Cα di A entro la soglia, in percentuale dei residui di A */
  gdt?: GdtCutoff[];
  gdt_ts?: number;
  gdt_ha?: number;
  /** lDDT sui Cα (0–1), riferimento A, raggio 15 Å */
  lddt?: number | null;
  lddt_distances?: number;
  /** sovrapposizione che porta B su A: x' = R·x + t */
  transform?: { rotation: number[][]; translation: number[] };
  /** versione del confronto strutturale (engines/align.STRUCTURAL_VERSION), anche se il confronto per residuo non è
   *  riuscito; assente nei risultati delle versioni precedenti */
  comparison_version?: number;
}

export interface StructuralPair {
  chain_a: string;
  resnum_a: string;
  aa_a: string;
  chain_b: string;
  resnum_b: string;
  aa_b: string;
  /** Å */
  distance: number;
  lddt: number | null;
}

export interface GdtCutoff {
  cutoff: number;
  count: number;
  percent: number;
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
  /** template AlphaFold (modello predetto) */
  predicted?: boolean;
  /** ritaglio dei residui a basso pLDDT; assente se non applicato */
  plddt?: PlddtTrim | null;
  /** con i ligandi attivi: gruppi trasferiti (ordine del file) e gruppi delle catene lasciati fuori, con il motivo */
  ligand_groups?: TransferredGroup[];
  ligands_excluded?: { resname: string; chain: string; resnum: string; class: LigandClass; label: string; reason: string }[];
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
  /** ligandi trasferiti: gruppi verificati ed esito peggiore */
  ligands?: { groups: number; status: LigandStatus; counts: Record<LigandStatus, number> } | null;
  /** modelli a più catene (F5.3): esito peggiore del confronto delle interfacce con il template, DockQ minimo */
  complex?: ComplexBrief | null;
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
  /** residui tolti dai template AlphaFold per pLDDT basso (somma su tutti i template) */
  plddt_removed?: number;
  /** solo con ligandi trasferiti: gruppi e verifica del modello migliore */
  ligands?: {
    /** un gruppo per ogni gruppo del modello: lo stesso gruppo di più template (stesso sito) una volta, con
     *  `template` = i codici separati da virgole e i gruppi di ciascun template in `sources` */
    transferred: {
      template: string; resname: string; chain: string; resnum: string; class: LigandClass; label: string;
      sources?: { template: string; chain: string; resnum: string; resname: string }[];
    }[];
    verification: LigandReport | null;
  };
  /** bersaglio a più catene (modalità Complesso o PIR a più catene, F5.3) */
  complex?: ComplexResult;
}

export interface Align2dResult {
  pir: string;
  pap: string;
  report: PirReport;
  templates: PreparedTemplate[];
  /** con più template: salign che ha spostato un template senza poter unire gli align2d, orientamento relativo non
   *  vincolato fra template di strutture diverse */
  warnings?: string[];
  /** modalità Complesso: catene del bersaglio nell'ordine del PIR (una per catena del template) */
  chains?: { template_chain: string; name: string; length: number; same_as: string | null }[];
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
  /** SHA-256 del file analizzato (job di qualità; assente nei rapporti dei modelli e in quelli precedenti) */
  source_sha256?: string;
  structure: {
    file: string;
    title: string;
    method: string;
    resolution: number | null;
    chains: number;
    residues: number;
    heavy_atoms: number;
    chains_detail: ChainSummary[];
    /** "plddt" = modello AlphaFold: la colonna B-factor contiene il pLDDT (0–100); assente nei rapporti precedenti */
    bfactor_kind?: BfactorKind;
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
  /** consenso tra i metodi (assente nei rapporti precedenti: si chiede alla rotta del consenso) */
  consensus?: ConsensusResult;
  /** ligandi, cofattori e metalli: solo se la struttura ha gruppi eteroatomici non d'acqua (il voto non cambia) */
  ligands?: LigandReport;
  /** modelli a più catene (F5.3): interfacce e confronto con quelle del template */
  complex?: ComplexAssessment;
}

export type BfactorKind = "plddt" | "bfactor";

// ---------------------------------------------------------------------------------- consenso (domain/consensus.py)
export type ConsensusMethodKey = "grade" | "zdope" | "ga341" | "qmean" | "prosa" | "procheck";
export type ConsensusSignalKey = "rama" | "clash" | "dope" | "qmean" | "prosa";
export type ConsensusBand = "buona" | "intermedia" | "scarsa";

export interface ConsensusMethod {
  key: ConsensusMethodKey;
  label: string;
  raw: number;
  /** valore sulla scala comune 0–100 */
  normalized: number;
  weight: number;
  band: ConsensusBand;
  measures: string;
  source: string;
  /** punti della scala lineare a tratti: [valore grezzo, punteggio] */
  points: [number, number][];
}

export interface ConsensusRegion {
  chain: string;
  start: string;
  end: string;
  /** indici (inclusi) in QualityReport.residues */
  start_index: number;
  end_index: number;
  length: number;
  flagged: number;
  methods: ConsensusSignalKey[];
  text: string;
  /** cade in una regione disordinata predetta (almeno metà dei residui): attesa poco affidabile (versione 2) */
  expected?: boolean;
  disorder_overlap?: number;
}

/** Disordine predetto dalla sequenza della struttura: segnale informativo, fuori dal punteggio (versione 2). */
export interface ConsensusDisorder {
  key: "disorder";
  label: string;
  rule: string;
  method: string;
  informative: true;
  /** FoldIndex per residuo (ordine di QualityReport.residues) */
  scores: (number | null)[];
  /** residuo in una regione disordinata lunga */
  flags: boolean[];
  flagged: number;
  fraction: number;
  regions: { chain: string; start: string; end: string; start_index: number; end_index: number; length: number; text: string }[];
  text: string;
  limits: string[];
}

export interface ConsensusResult {
  version: number;
  score: number | null;
  band: ConsensusBand | null;
  methods_used: number;
  methods_total: number;
  methods: ConsensusMethod[];
  missing: { key: ConsensusMethodKey; label: string; reason: string }[];
  discordances: { good: ConsensusMethodKey; poor: ConsensusMethodKey; text: string }[];
  per_residue: {
    signals: { key: ConsensusSignalKey; label: string; rule: string; flagged: number }[];
    /** per ogni residuo (ordine di QualityReport.residues) i metodi che lo segnalano */
    flags: ConsensusSignalKey[][];
    counts: number[];
    values: Partial<Record<"dope" | "qmean" | "prosa", (number | null)[]>>;
    dope_threshold: number | null;
  };
  regions: ConsensusRegion[];
  /** assente nei consensi della versione 1 (rapporti salvati prima di F5.5) */
  disorder?: ConsensusDisorder | null;
  thresholds: Record<string, number>;
  warnings: string[];
  /** solo dalla rotta del consenso */
  inputs?: {
    source: StructureSource;
    modeller: { ga341: number | null; zdope: number | null } | null;
    qmean: { job_id: string; source: string | null; method: string | null } | null;
  };
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
  /** metriche del modello; `search_cached_at` = ricerca del template riusata dalla cache (data ISO);
   * `template_predicted`/`plddt_removed` = template AlphaFold e residui tolti per pLDDT basso; modalità multi:
   * `templates` (scelti, con ruolo e motivazione), `combined_coverage` (anche nella colonna `coverage`) e
   * `excluded_templates` (candidati scartati o non esaminati) */
  metrics: Record<string, unknown> & {
    search_cached_at?: string; template_predicted?: boolean; plddt_removed?: number;
    templates?: PipelineTemplateChoice[]; combined_coverage?: number; excluded_templates?: number;
  };
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/** Template scelto per un elemento della pipeline in modalità multi. */
export type PipelineTemplateChoice = Pick<SuggestedTemplate, "template_id" | "role" | "reason" | "identity" | "coverage" | "rank_score" | "predicted">;

export type TemplateMode = "auto" | "fixed" | "multi";

/** Corpo di POST /api/pipeline (api/compute.PipelineIn). */
export interface PipelineRequest {
  project_id?: string;
  name?: string;
  fasta?: string;
  records?: { name: string; sequence: string }[];
  template_mode?: TemplateMode;
  /** solo modalità multi: template complementari al massimo per sequenza (2–5, predefinito 3) */
  max_templates?: number;
  templates?: TemplateRef[];
  engine?: SearchEngine;
  evalue?: number;
  twilight?: { filter: boolean; identity: number; coverage: number };
  options?: Record<string, unknown>;
  quality?: boolean;
  extended_quality?: boolean;
  parallel?: number;
  use_cache?: boolean;
}

export interface PipelineResult {
  total: number;
  counts: Record<string, number>;
  /** elementi la cui ricerca del template viene dalla cache dei calcoli */
  search_cache_hits?: number;
  /** template preparati per MODELLER in questa esecuzione e preparazioni riusate da altri elementi (F1.2) */
  templates_prepared?: number;
  templates_reused?: number;
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

// ---------------------------------------------------------------------------------- report (domain/report.py)
/** Corpo facoltativo di POST /api/jobs/{id}/report[/{modello}] (la GET genera il report senza immagine). */
export interface ReportRequest {
  /** vista 3D in PNG: data URI data:image/png;base64,… (3Dmol `pngURI`) o solo base64; al massimo 3 MB */
  image_png_base64?: string | null;
}

/** Report HTML autonomo ricevuto dal server, pronto da salvare. */
export interface ReportFile {
  blob: Blob;
  /** da Content-Disposition, es. report_yeast.B99990001.html */
  filename: string;
}

// ---------------------------------------------------------------------------------- analisi strutturale (F7)
/** Estremità di un'interazione: residuo e atomo (o gruppo: "anello", "anello a 5", "anello a 6"). */
export interface InteractionEnd {
  chain: string;
  resnum: string;
  resname: string;
  atom: string;
}

/** Legame idrogeno: `a` = donatore, `b` = accettore (domain/interactions.py). */
export interface HBond {
  a: InteractionEnd;
  b: InteractionEnd;
  /** D…A (Å) */
  distance: number;
  /** H…A (Å) e angolo D–H…A (°) con l'idrogeno (del file o posizionato); null per i gruppi che ruotano */
  ha_distance: number | null;
  angle: number | null;
  /** AA–D…A (°) senza idrogeno */
  donor_angle: number | null;
  /** D…A–AA (°) */
  acceptor_angle: number;
  hydrogen: "explicit" | "placed" | null;
  kind: "backbone" | "mixed" | "sidechain";
  inter_chain: boolean;
}

/** Ponte salino: `a` = catione (Lys, Arg, His), `b` = anione (Asp, Glu); coppia di atomi più vicina. */
export interface SaltBridge {
  a: InteractionEnd;
  b: InteractionEnd;
  distance: number;
  /** coppie di atomi entro 4,0 Å */
  pairs: number;
  /** istidina: carica solo se protonata */
  his: boolean;
  inter_chain: boolean;
}

export interface Disulfide {
  a: InteractionEnd;
  b: InteractionEnd;
  /** SG–SG (Å) */
  distance: number;
  ca_distance: number;
  /** diedro CB–SG–SG–CB (°) */
  chi3: number | null;
  inter_chain: boolean;
}

/** π-stacking (parallelo o a T) o catione-π (`a` = catione NZ/CZ, `b` = anello). */
export interface AromaticInteraction {
  type: "pi_parallel" | "pi_tshaped" | "cation_pi";
  a: InteractionEnd;
  b: InteractionEnd;
  /** fra i centroidi, o fra catione e centroide (Å) */
  distance: number;
  /** fra i piani degli anelli, o fra la normale e la direzione del catione (°) */
  angle: number;
  offset: number;
  inter_chain: boolean;
}

export interface InteractionsResult {
  version: number;
  hydrogens: "explicit" | "placed";
  criteria: Record<string, Record<string, number | string>>;
  summary: {
    residues: number;
    chains: number;
    hbonds: number;
    hbonds_backbone: number;
    hbonds_mixed: number;
    hbonds_sidechain: number;
    salt_bridges: number;
    disulfides: number;
    pi_stacking: number;
    cation_pi: number;
    inter_chain: { hbonds: number; salt_bridges: number; disulfides: number; aromatic: number };
  };
  hbonds: HBond[];
  salt_bridges: SaltBridge[];
  disulfides: Disulfide[];
  aromatic: AromaticInteraction[];
}

export interface InterfaceResidue {
  chain: string;
  resnum: string;
  resname: string;
  aa: string;
  /** ΔSASA fra catena isolata e complesso (o coppia), Å² */
  delta: number;
  apolar_delta: number;
  /** solo nell'elenco di tutti i residui: SASA nel complesso e nella catena isolata */
  sasa?: number;
  sasa_isolated?: number;
}

export type PlausibilityLevel = "bassa" | "incerta" | "compatibile";

export interface InterfacePair {
  chains: [string, string];
  /** area d'interfaccia = area sepolta totale / 2 (Å²) */
  area: number;
  buried: number;
  apolar_fraction: number | null;
  /** residui d'interfaccia (ΔSASA > 1 Å²) per catena */
  residues: Record<string, InterfaceResidue[]>;
  counts: Record<string, number>;
  composition: { counts: Record<string, number>; percent: Record<string, number> };
  interactions: { hbonds: number; salt_bridges: number; disulfides: number; pi_stacking: number; cation_pi: number };
  plausibility: { level: PlausibilityLevel; text: string; limits: string; thresholds: { low: number; high: number } };
}

export interface InterfacesResult {
  version: number;
  method: string;
  chains: string[];
  min_delta: number;
  pairs: InterfacePair[];
  /** tutti i residui del complesso con ΔSASA (vuoto con una sola catena) */
  residues: InterfaceResidue[];
  interface_residues?: number;
  note?: string;
}

export interface ConservationResidue {
  chain: string;
  resnum: string;
  aa: string;
  /** colonna dell'allineamento (da 1), null se il residuo non è allineato alla riga */
  column: number | null;
  row_aa: string | null;
  /** 0–1; null oltre il 30% di gap o senza colonna */
  score: number | null;
  entropy: number | null;
  gap_fraction: number | null;
}

export interface ConservationResult {
  version: number;
  source: string;
  sequences: number;
  columns: number;
  method: string;
  gap_cutoff: number;
  chains: { chain: string; row: string; identity: number; coverage: number; residues: number; mapped: number; scored: number; mean: number | null }[];
  unmatched: { chain: string; best_row: string; identity: number; coverage: number }[];
  residues: ConservationResidue[];
  warnings: string[];
}

/** Risultato di un job `analysis` (services/analysis.py). */
export interface AnalysisResult {
  source: StructureSource;
  label: string;
  source_sha256: string;
  structure: { file: string; chains: number; residues: number; heavy_atoms: number; chains_detail: ChainSummary[] };
  interactions: InteractionsResult;
  interfaces: InterfacesResult;
  conservation: ConservationResult | null;
  warnings: string[];
}

/** Corpo di POST /api/analysis: allineamento facoltativo da un job `align` o da testo (FASTA, Clustal, …). */
export interface AnalysisRequest {
  project_id?: string;
  source: StructureSource;
  chains?: string[];
  msa?: { job_id?: string; text?: string; row?: string; tool?: "auto" | "builtin" | "clustalo" | "mafft" | "muscle" } | null;
}

// ---------------------------------------------------------------------------------- complessi (F5.3)
/** Catena di un assemblaggio biologico: nuovo nome, catena d'origine dell'autore e operatore (es. "2" o "1×61"). */
export interface AssemblyChain {
  chain: string;
  source: string;
  operator: string;
  /** l'operatore (o la combinazione) è l'identità: coordinate del file */
  identity: boolean;
  protein: boolean;
}

/** Assemblaggio dichiarato nel file (REMARK 350 del PDB o pdbx_struct_assembly del mmCIF), domain/assembly.py. */
export interface AssemblySummary {
  id: string;
  /** author_defined_assembly | software_defined_assembly */
  details: string;
  method: string;
  /** es. "dimeric", "tetrameric" */
  oligomeric_details: string;
  oligomeric_count: number | null;
  generators: { chains: string[]; operators: string }[];
  chains: AssemblyChain[];
  chain_count: number;
  protein_chains: number;
  source_chains?: string[];
  atoms?: number;
  /** solo l'operatore identità: le coordinate sono quelle del file */
  identity_only?: boolean;
  /** false oltre 62 catene o 99 999 atomi (formato PDB) */
  buildable: boolean;
  note: string;
}

export interface AssemblyList {
  template: string;
  format: "mmcif" | "pdb";
  assemblies: AssemblySummary[];
  note: string;
}

/** Metadati di un template che è un assemblaggio importato. */
export interface TemplateAssemblyMeta {
  source_id: string;
  assembly_id: string;
  details: string;
  method: string;
  oligomeric_details: string;
  oligomeric_count: number | null;
  chains: AssemblyChain[];
  format: string;
  source_sha256: string;
}

/** Catena del bersaglio di un complesso: la sequenza oppure `same_as` = stessa sequenza della catena indicata. */
export interface ComplexChainRequest {
  template_chain: string;
  sequence?: string;
  name?: string;
  same_as?: string | null;
}

/** Bersaglio a più catene (POST /api/model e /api/align2d, campo `complex`): un solo template, catene nell'ordine. */
export interface ComplexRequest {
  name: string;
  chains: ComplexChainRequest[];
}

/** Esito del confronto di un'interfaccia con il template: classi di DockQ (Basu & Wallner 2016), più "nuova" (assente
 *  nel template) e "non valutabile". */
export type InterfaceStatus = "alta" | "media" | "accettabile" | "non riprodotta" | "nuova" | "non valutabile";

export interface InterfaceComparison {
  /** null per un'interfaccia che il template non ha */
  template_chains: [string, string] | null;
  model_chains: string[];
  status: InterfaceStatus;
  tone: "ok" | "warn" | "err";
  area_template: number;
  area_model: number;
  /** area del modello / area del template (%) */
  area_ratio: number | null;
  template_residues: number;
  conserved: number;
  conserved_percent: number | null;
  unaligned: number;
  /** residui del modello ("A57") d'interfaccia anche nel template */
  conserved_residues: string[];
  /** residui del modello allineati a residui d'interfaccia del template che nel modello non lo sono */
  lost_residues: string[];
  fnat: number | null;
  irms: number | null;
  lrms: number | null;
  dockq: number | null;
  dockq_class: string | null;
  native_contacts: number;
  reproduced_contacts: number;
  receptor: string | null;
  ligand: string | null;
  text: string;
}

/** Interfacce di un modello a più catene e confronto con quelle del template (domain/complexes.assess). */
export interface ComplexAssessment {
  version: number;
  template: string | null;
  chains: { template: string; model: string }[];
  /** interfacce del modello, senza l'elenco di tutti i residui */
  interfaces: Omit<InterfacesResult, "residues">;
  template_interfaces: Omit<InterfacesResult, "residues"> | null;
  comparison: InterfaceComparison[];
  status: InterfaceStatus;
  tone: "ok" | "warn" | "err";
  summary: string;
  notes: string[];
  limits: string;
}

export interface ComplexBrief {
  status: InterfaceStatus;
  tone: "ok" | "warn" | "err";
  dockq: number | null;
  interfaces: number;
}

export interface ComplexChainInfo {
  index: number;
  model_chain: string;
  template_chain: string | null;
  length: number;
  name: string | null;
  same_as: string | null;
}

export interface ComplexResult {
  chains: ComplexChainInfo[];
  symmetry: { requested: boolean; applied: boolean; groups: string[][]; weight: number; atoms?: string };
  template: string | null;
  mode: "complex" | "pir";
  /** interfacce del modello migliore (anche senza analisi di qualità) */
  assessment: ComplexAssessment | null;
}
