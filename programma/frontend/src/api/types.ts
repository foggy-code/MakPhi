// Tipi delle risposte dell'API BioLoom v4 (specchio dei payload del backend).

export type JobStatus = "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
export type JobKind =
  | "search" | "align" | "align2d" | "model" | "loop" | "quality" | "qmean" | "pipeline"
  | "selftest" | "db_update_pdbaa" | "db_build_mmseqs" | "db_index_mmseqs" | "db_convert_uniclust" | "db_local_uniclust"
  | "analysis"
  | "pockets" | "electrostatics" | "refine"
  | "rerun" | "mutate" | "alascan" | "predict";

export type SearchEngine = "rcsb" | "ncbi" | "local" | "mmseqs" | "alphafold";

export interface JobSummary extends Labelled {
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

export interface Project extends Labelled {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  sequences?: number;
  documents?: number;
  jobs?: number;
}

export interface Sequence extends Labelled {
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
  /** `esmfold_remote`: consenso all'invio delle sequenze a ESMFold (F9.3) */
  services: { qmean_remote: boolean; contact_email: boolean; esmfold_remote?: boolean };
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
  /** F9.3: consenso all'invio delle sequenze al servizio ESMFold (spento per default) */
  esmfold_remote: boolean;
  /** F9.3: indirizzo del servizio ESMFold; vuoto = API pubblica di ESM Atlas */
  esmfold_url: string;
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
  /** template della libreria o del PIR: identità (%) sulle coppie allineate e loro numero (controlli preventivi) */
  identity?: number | null;
  aligned?: number | null;
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

export interface TemplateItem extends Labelled {
  id: string;
  format: string;
  size: number;
  source: string;
  added_at: string | null;
  title: string;
  method: string;
  resolution: number | null;
  chains: string[];
  /** modello predetto (AlphaFold DB, file di AlphaFold o predizione di ESMFold caricati): non una struttura sperimentale */
  predicted?: boolean;
  /** predittore riconosciuto (domain/plddt.predictor); assente nei metadati precedenti a F9.3 */
  predictor?: PredictorName | null;
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
  /** template predetto (AlphaFold, ESMFold) */
  predicted?: boolean;
  predictor?: PredictorName | null;
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

/** Struttura secondaria per residuo di una voce del PIR (POST /api/pir/secondary). */
export interface PirSecondaryEntry {
  code: string;
  kind: string;
  residues: number;
  /** un carattere per lettera della voce: DSSP a 8 stati per i template, H/E/C per la predizione; "?" sconosciuto */
  ss: string | null;
  source: "dssp" | "gor" | null;
  method: string | null;
  template: { id: string; chains: string[] } | null;
  /** abbinamento al template della libreria: per codice, per posizione, dal solo codice */
  matched_by: "code" | "position" | "code_guess" | null;
  message: string | null;
  /** lettere del PIR diverse dal residuo del file (le prime 20) */
  mismatches: { index: number; letter: string; residue: string }[];
  mismatch_count: number;
  problems: string[];
}

export interface PirSecondary {
  entries: PirSecondaryEntry[];
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
    /** con il pLDDT: chi ha scritto la predizione (F9.3: anche ESMFold) */
    predictor?: PredictorName;
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
/** "predictor": accordo con una predizione esterna, solo se il confronto è stato eseguito (F9.3) */
export type ConsensusMethodKey = "grade" | "zdope" | "ga341" | "qmean" | "prosa" | "procheck" | "predictor";
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
  /** F9.3, solo con il confronto: residui in cui il modello diverge anche da una predizione sicura */
  predictor_overlap?: number;
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
  /** F9.3: accordo con una predizione esterna, solo se il confronto è stato chiesto (`predict_job`) */
  predictor?: ConsensusPredictor;
  thresholds: Record<string, number>;
  warnings: string[];
  /** solo dalla rotta del consenso */
  inputs?: {
    source: StructureSource;
    modeller: { ga341: number | null; zdope: number | null } | null;
    qmean: { job_id: string; source: string | null; method: string | null } | null;
    predictor?: { job_id: string; predictor: PredictorName; label: string; status: PlausibilityStatus };
  };
}

/** Accordo con una predizione per residuo (domain/consensus.predictor_signal): informativo, fuori dalle regioni. */
export interface ConsensusPredictor {
  key: "predictor";
  label: string;
  predictor: PredictorName;
  job_id: string | null;
  informative: true;
  status: PlausibilityStatus;
  tone: PlausibilityTone;
  text: string;
  rule: string;
  /** per residuo (ordine di QualityReport.residues): lDDT rispetto alla predizione e pLDDT della predizione */
  lddt: (number | null)[];
  plddt: (number | null)[];
  /** residuo che diverge dove la predizione è sicura */
  flags: boolean[];
  flagged: number;
  regions: (PlausibilityRegion & { start_index: number; end_index: number })[];
  tm_score: number | null;
  lddt_confident: number | null;
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
    /** pipeline M × N: zDOPE di MODELLER (criterio del migliore) e migliore della sequenza */
    zdope?: number | null; molpdf?: number | null; best_in_sequence?: boolean;
  };
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** solo pipeline M × N: sequenza e template della coppia (indici e nomi) */
  pair?: PipelinePair;
  /** solo pipeline M × N: modello con lo zDOPE più basso fra i template della sequenza */
  best_in_sequence?: boolean;
}

export interface PipelinePair {
  sequence_index: number;
  template_index: number;
  sequence: string;
  /** etichetta del template (id e catene, es. 1UBQ_A): cartella dello ZIP e colonna della matrice */
  template: string;
  template_id: string;
}

/** Nomi delle righe (sequenze) e delle colonne (template) di una pipeline M × N. */
export interface PipelinePairs {
  sequences: string[];
  templates: string[];
  template_ids: string[];
}

/** Cella della matrice M × N (GET /api/pipeline/{id}/matrix); null = coppia non (ancora) creata. */
export interface PipelineMatrixCell {
  idx: number;
  status: PipelineItem["status"];
  dope: number | null;
  zdope: number | null;
  ga341: number | null;
  molpdf: number | null;
  identity: number | null;
  coverage: number | null;
  grade: string | null;
  grade_score: number | null;
  best_model: string | null;
  best: boolean;
  error: string | null;
}

export interface PipelineMatrix extends PipelinePairs {
  cells: (PipelineMatrixCell | null)[][];
}

/** Uso dei template fissi: un modello con tutti i template, oppure uno per ogni coppia sequenza × template. */
export type FixedCombination = "combined" | "each";

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
  /** solo template fissi: "combined" (predefinito) = un modello con tutti i template; "each" = M × N */
  fixed_combination?: FixedCombination;
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
  /** solo pipeline M × N */
  pairs?: PipelinePairs;
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

// ---------------------------------------------------------------------------------- tasche, elettrostatica, rifinitura (F7b, F9.1)
/** Calcolo completato più recente su una struttura e quello in coda o in corso (`/api/pockets/latest`, `/api/electrostatics/latest`). */
export interface LatestToolJob {
  job_id: string | null;
  finished_at: string | null;
  active_job_id?: string | null;
}

/** Sfera alfa di fpocket: centro (Å) e raggio (3,4–6,2 Å). */
export interface PocketSphere {
  center: [number, number, number];
  radius: number;
}

/** Tasca di fpocket (domain/pockets.py): `id` = numero di fpocket (ordine per punteggio), `rank` = posto per druggability. */
export interface Pocket {
  id: number;
  rank: number;
  score?: number;
  /** 0–1, Schmidtke & Barril 2010: ≥ 0,5 probabilmente druggabile */
  druggability?: number;
  druggable: boolean;
  volume?: number;
  alpha_spheres?: number;
  sasa_total?: number;
  sasa_polar?: number;
  sasa_apolar?: number;
  hydrophobicity_score?: number;
  polarity_score?: number;
  charge_score?: number;
  polar_atoms_pct?: number;
  mean_sphere_radius?: number;
  flexibility?: number;
  center: [number, number, number] | null;
  spheres: PocketSphere[];
  residues: { chain: string; resnum: string; resname: string }[];
  atoms: number;
  other: Record<string, number | null>;
}

/** Risultato di un job `pockets` (services/structure_tools.py). */
export interface PocketsResult {
  source: StructureSource;
  label: string;
  source_sha256: string;
  pockets: Pocket[];
  summary: { count: number; druggable: number; threshold: number; best: { id: number; druggability: number | null; volume: number | null; residues: number } | null };
  structure: { residues: number; chains: number; heavy_atoms: number; chains_detail: ChainSummary[] };
  warnings: string[];
  files: string[];
  params: { program: string; options: string; druggable_threshold: number };
  /** PDB di fpocket con le sfere alfa (residui STP), fra i file del job */
  pdb: string;
}

export interface ElectrostaticsResidue {
  chain: string;
  resnum: string;
  resname: string;
  /** media sugli atomi esposti del potenziale sulla superficie accessibile (kT/e); null se il residuo è sepolto */
  potential: number | null;
  exposed_atoms: number;
  points: number;
  /** carica del residuo nel PQR (estremità comprese nel primo e nell'ultimo) */
  charge: number | null;
}

/** Risultato di un job `electrostatics` (services/structure_tools.py, domain/electrostatics.py). */
export interface ElectrostaticsResult {
  source: StructureSource;
  label: string;
  source_sha256: string;
  forcefield: "PARSE" | "AMBER";
  ph: number;
  net_charge: number;
  charges: { net_charge: number; positive: number; negative: number; by_type: Record<string, number>; charged: { chain: string; resnum: string; resname: string; charge: number }[] };
  grid: { dime: [number, number, number]; cglen: number[]; fglen: number[]; center: number[]; spacing: number[]; memory_mb: number; ceiling_mb: number; coarsened: boolean };
  parameters: Record<string, number | string>;
  sampling: { surface: string; probe: number; points_per_atom: number; residue_value: string };
  scale: { unit: string; limit: number; mv_per_unit: number };
  map: { file: string; min: number; max: number; points: number };
  residues: ElectrostaticsResidue[];
  summary: { residues: number; min: number | null; max: number | null; mean: number | null; positive: number; negative: number; threshold: number };
  structure: { residues: number; chains: number; heavy_atoms: number };
  warnings: string[];
  files: string[];
  timings: Record<string, number>;
}

export type RefineVerdict = "meglio" | "peggio" | "invariato" | null;

export interface RefineComparisonRow {
  key: "grade_score" | "rama_favored" | "rama_outliers" | "clashscore" | "dope" | "zdope";
  label: string;
  before: number | null;
  after: number | null;
  delta: number | null;
  verdict: RefineVerdict;
  higher_is_better: boolean;
}

export interface RefineEnergyComponents {
  bonds: number;
  angles: number;
  torsions: number;
  nonbonded: number;
  solvation: number;
  total: number;
}

/** Risultato di un job `refine` (services/refine.py, protocollo in domain/refinement.py). */
export interface RefineResult {
  source: StructureSource;
  label: string;
  source_sha256: string;
  /** modello rifinito fra i file del job, con il suo rapporto di qualità `<file>.quality.json` */
  refined_file: string;
  /** copia pulita data a OpenMM: la struttura "prima" del confronto */
  input_file: string;
  protocol: {
    forcefield: string; solvent: string; ph: number;
    restraint: { atoms: string[]; k_kcal_mol_A2: number; k_kj_mol_nm2: number; form: string };
    minimization: { tolerance_kj_mol_nm: number; max_iterations: number };
    md: { ps: number; temperature_k: number; friction_ps: number; timestep_fs: number; steps: number; seed: number } | null;
    cutoff: { atoms_over: number; nm: number };
    hydrogens_in_output: boolean;
  };
  openmm: { openmm: string; platform: string; threads: number; atoms: number; nonbonded: string; restrained_atoms: number; md_steps: number; timings: Record<string, number> };
  added: { heavy_atoms: number; terminal_atoms: number; hydrogens: number; nonstandard: string[] };
  input: { excluded: string[]; modified: string[]; breaks: { chain: string; after: string; before: string }[]; hydrogens_removed: number; residues: number };
  energy: {
    before: number | null; after: number | null; delta: number | null; decreased: boolean; restraint_final: number | null; unit: string;
    components: { before: RefineEnergyComponents; after_minimization: RefineEnergyComponents; after_md?: RefineEnergyComponents; final: RefineEnergyComponents; restraint_final: number };
  };
  rmsd: { atoms: number; rmsd: number | null; rmsd_no_fit: number | null; max_displacement: number | null; max_atom: string | null };
  quality: { before: QualitySummary; after: QualitySummary };
  comparison: RefineComparisonRow[];
  warnings: string[];
}

// ---------------------------------------------------------------------------------- mutagenesi e scansione di alanina (F9.2)
/** Classe qualitativa dell'effetto (domain/mutations.classify): non un ΔΔG. */
export type MutationClass = "destabilizing" | "uncertain" | "neutral";
/** Peso di un segnale: forte, moderato, possibile guadagno, informativo. */
export type MutationSignalLevel = "strong" | "moderate" | "gain" | "info";

export interface MutationSignal {
  level: MutationSignalLevel;
  /** codice stabile: proline_helix, dope, energy, buried_apolar_lost, … */
  code: string;
  text: string;
  /** con più siti: il sito del segnale ("A:K48R") */
  site?: string;
}

export interface MutationBinding {
  interface: boolean;
  /** hot spot d'interfaccia (Bogan & Thorn 1998): probabile, possibile o nessuno */
  hotspot: "probabile" | "possibile" | null;
  text: string;
  interchain_delta_eq: number | null;
  lost_inter_chain: number;
}

export interface MutationAssessment {
  code: MutationClass;
  label: string;
  summary: string;
  reasons: string[];
  signals: MutationSignal[];
  /** chiave di ordinamento (100 per segnale forte, 10 per moderato, più i residui equivalenti di ΔDOPE) */
  score: number;
  binding: MutationBinding | null;
}

export interface MutationRef {
  chain: string;
  resnum: string;
  wt: string;
  mut: string;
  /** "A:K48R" ("K48R" per la catena senza identificativo) */
  label: string;
  wt_name: string;
  mut_name: string;
  /** catena del residuo nei modelli costruiti: diversa da `chain` solo per le catene con un nome di più caratteri,
   *  che nei file PDB restano con la lettera di MODELLER */
  model_chain?: string;
}

export interface MutationInteraction {
  type: "hbond" | "salt_bridge" | "disulfide" | "aromatic";
  partner: string;
  role: string;
  atom: string | null;
  inter_chain: boolean;
  distance: number | null;
}

/** Indicatori strutturali di un sito (domain/mutations.site_indicators). */
export interface MutationSite {
  site: string;
  label: string;
  wt: string;
  mut: string;
  rsa_wt: number | null;
  rsa_mut: number | null;
  core: boolean;
  buried_wt: { total: number; apolar: number };
  buried_mut: { total: number; apolar: number };
  buried_delta: { total: number; apolar: number };
  /** DSSP a 8 stati del selvatico ottimizzato, con la posizione nel segmento */
  ss: string;
  ss_position: number;
  ss_length: number;
  rama: { phi: number; psi: number; wt: string; mut: string } | null;
  blosum62: number;
  /** solo in α-elica: ΔΔG di propensione (Pace & Scholtz 1998), kcal/mol */
  helix_propensity: number | null;
  interactions: { wild_type: number; mutant: number; lost: MutationInteraction[]; gained: MutationInteraction[] };
  clashes: { wild_type: number; mutant: number; worst: number | null };
  interface: { delta: number; rsa_complex: number | null; lost_inter_chain: number } | null;
}

export interface MutationDope {
  wild_type: number;
  mutant: number;
  delta: number;
  local_wild_type: number | null;
  local_mutant: number | null;
  delta_local: number | null;
  local_residues: number;
  local_radius: number;
  /** |DOPE del selvatico| / residui: l'unità dei "residui equivalenti" */
  per_residue: number | null;
  delta_eq: number | null;
  interchain_wild_type: number | null;
  interchain_mutant: number | null;
  interchain_delta: number | null;
  interchain_delta_eq: number | null;
  /** scarto del DOPE fra i modelli della stessa variante (rumore del protocollo) */
  spread: { wild_type?: number; mutant?: number };
}

/** Energia di OpenMM (facoltativa): ΔΔE = (E_mut − E_wt) − (E_rif,mut − E_rif,wt), kJ/mol; non un ΔΔG. */
export interface MutationEnergy {
  available: boolean;
  message?: string;
  error?: string;
  wild_type?: number | null;
  mutant?: number | null;
  reference_wild_type?: number | null;
  reference_mutant?: number | null;
  delta_folded?: number | null;
  delta_reference?: number | null;
  ddE?: number | null;
  ddE_kcal?: number | null;
  unit?: string;
  openmm?: string;
  files?: { wild_type: string; mutant: string };
  protocol?: string;
}

export interface MutationProtocol {
  method: string;
  radius: number;
  local_radius: number;
  models: number;
  seeds: number[];
  randomize: number;
  selection: string;
  wild_type: string;
  energy: boolean;
  core_rsa: number;
  thresholds: Record<string, number>;
}

/** Risultato di un job `mutate` (services/mutations.py). */
export interface MutateResult {
  version: number;
  source: StructureSource;
  label: string;
  source_sha256: string;
  mutations: MutationRef[];
  notation: string;
  /** modelli (con il loro `<file>.quality.json`) e, con l'energia, i minimizzati */
  files: { wild_type: string; mutant: string; wild_type_minimized?: string; mutant_minimized?: string };
  protocol: MutationProtocol;
  models: { variant: string; role: "wild_type" | "mutant"; seed: number; dope: number; local: number | null; interchain: number | null; chosen: boolean; moved_atoms: number }[];
  dope: MutationDope;
  energy: MutationEnergy | null;
  sites: MutationSite[];
  assessment: MutationAssessment;
  quality: { wild_type: QualitySummary; mutant: QualitySummary };
  comparison: RefineComparisonRow[];
  rmsd: { outside: number | null; outside_atoms: number; all: number | null; atoms: number };
  residues: number;
  warnings: string[];
  caveat: string;
}

export interface AlaScanRow {
  mutation: MutationRef;
  label: string;
  site: MutationSite;
  dope: MutationDope;
  energy: MutationEnergy | null;
  assessment: MutationAssessment;
  files: { wild_type: string; mutant: string };
}

/** Risultato di un job `alascan`: righe dalla sostituzione più destabilizzante. */
export interface AlaScanResult {
  version: number;
  source: StructureSource;
  label: string;
  source_sha256: string;
  selection: { mode: ScanMode; detail: string; chain?: string | null; pocket_job?: string; pocket?: number };
  protocol: MutationProtocol;
  rows: AlaScanRow[];
  skipped: { chain: string; resnum: string; resname: string; reason: string }[];
  failed: { label: string; error: string }[];
  summary: { residues: number; skipped: number; counts: Record<MutationClass, number>; hotspots: number };
  hotspots: { label: string; level: "probabile" | "possibile"; text: string }[];
  interface: boolean;
  residues: number;
  csv: string;
  warnings: string[];
  caveat: string;
}

export type ScanMode = "residues" | "interface" | "pocket";

export interface ScanSelection {
  mode: ScanMode;
  residues?: string;
  chain?: string | null;
  pocket_job?: string | null;
  pocket?: number | null;
}

/** Corpo di POST /api/mutate. */
export interface MutateRequest {
  project_id?: string;
  source: StructureSource;
  mutations: string;
  energy?: boolean;
  seed?: number;
}

/** Corpo di POST /api/alascan. */
export interface AlaScanRequest {
  project_id?: string;
  source: StructureSource;
  selection: ScanSelection;
  energy?: boolean;
  seed?: number;
}

/** Variante di UniProt portata sulla struttura (GET /api/mutations/variants). */
export interface MappedVariant {
  type: "naturale" | "mutagenesi";
  category: "malattia" | "naturale" | "mutagenesi";
  /** posizione sulla sequenza di UniProt */
  position: number;
  original: string;
  variation: string;
  description: string;
  diseases: string[];
  ids: string[];
  evidences: number;
  feature_id: string;
  chain: string;
  /** residuo della struttura corrispondente (null se la posizione non c'è) */
  resnum: string | null;
  structure_aa: string | null;
  applicable: boolean;
  /** notazione pronta ("A:K48R") se applicabile */
  notation: string | null;
  reason: string | null;
  /** con le ripetizioni (es. poliubiquitina): il tratto di UniProt della variante, da 1; null con un solo tratto */
  segment: number | null;
}

export interface VariantMapping {
  accession: string;
  entry_name: string;
  label: string;
  uniprot_length: number;
  chain: string;
  identity: number;
  coverage: number;
  /** numerazione di UniProt − numerazione della struttura, se costante (un solo tratto) */
  offset: number | null;
  /** tratti di UniProt allineati alla catena: più di uno con le ripetizioni */
  segments: { start: number; end: number; identity: number }[];
  variants: MappedVariant[];
  applicable: number;
  notes: string[];
  method: string;
}

export interface MutationHistoryItem {
  id: string;
  kind: "mutate" | "alascan";
  title: string;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
  mutations: string | null;
  energy: boolean;
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
  /** null per una catena del template allineata solo a gap (nessuna catena del modello) */
  model_chains: (string | null)[];
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

// ---------------------------------------------------------------------------------- controlli preventivi (F8a)
export type PreflightKind = "model" | "loop" | "align2d" | "pipeline" | "quality" | "qmean" | "search" | "analysis"
  | "rerun" | "mutate" | "alascan" | "predict";
/** error = l'avvio fallirebbe (pulsante disattivato); warning = conferma richiesta; info = da sapere */
export type PreflightLevel = "error" | "warning" | "info";

/** Un esito dei controlli preventivi (domain/preflight.py). */
export interface PreflightCheck {
  level: PreflightLevel;
  /** codice stabile: modeller_license_missing, twilight_zone, disk_low_data, … */
  code: string;
  message: string;
  suggestion: string | null;
  /** "coverage": avviso di copertura (disordine, domini), mostrato con CoverageAlerts */
  group?: "coverage";
  template?: string;
}

/** Tempo stimato in secondi, con l'intervallo e la base della stima. */
export interface PreflightEstimate {
  seconds: number;
  low: number;
  high: number;
  detail: string;
  basis: string;
}

/** Risposta di POST /api/compute/preflight. */
export interface PreflightResult {
  kind: PreflightKind;
  /** nessun errore */
  ok: boolean;
  counts: Record<PreflightLevel, number>;
  /** errori, poi avvisi, poi informazioni */
  checks: PreflightCheck[];
  estimate: PreflightEstimate | null;
  /** solo modellazione: la verifica di copertura di /api/bio/coverage */
  coverage: CoverageCheck | null;
}

/** Corpo di POST /api/compute/preflight: `params` è lo stesso corpo della rotta di avvio. */
export interface PreflightRequest {
  kind: PreflightKind;
  params: Record<string, unknown>;
  context?: { accession?: string | null; search_job?: string | null };
}

// ------------------------------------------------------------------ etichette, preferiti e ricerca globale (F8b)

/** Elementi che possono avere etichette e stella. */
export type EntityType = "project" | "job" | "sequence" | "template";

/** Campi aggiunti dagli elenchi di progetti, job, sequenze e template (`?tag=` e `?favorite=` li filtrano). */
export interface Labelled {
  /** etichette normalizzate (minuscole, al più 32 caratteri), in ordine alfabetico */
  tags?: string[];
  favorite?: boolean;
}

/** GET/POST/DELETE /api/tags/{tipo}/{id}, PUT/DELETE /api/favorites/{tipo}/{id}. */
export interface EntityLabels {
  entity_type: EntityType;
  entity_id: string;
  tags: string[];
  favorite: boolean;
}

/** GET /api/tags: etichetta in uso con il numero di elementi, in totale e per tipo. */
export interface TagCount {
  tag: string;
  count: number;
  types: Partial<Record<EntityType, number>>;
}

export interface FavoriteEntry {
  entity_type: EntityType;
  entity_id: string;
  created_at: string;
}

export type SearchGroupType = "project" | "sequence" | "document" | "job" | "template" | "tag";

/** Voce di un gruppo della ricerca globale. */
export interface SearchItem {
  type: SearchGroupType;
  /** id dell'elemento (per le etichette: l'etichetta) */
  id: string;
  title: string;
  subtitle: string;
  /** pertinenza (domain/textsearch.py), +1 con la stella */
  score: number;
  /** progetto dell'elemento (per un progetto: sé stesso); null per template ed etichette */
  project_id: string | null;
  project_name: string | null;
  tags?: string[];
  favorite?: boolean;
  /** job: tipo e stato */
  kind?: JobKind;
  status?: JobStatus;
  /** template: modello predetto e da quale predittore */
  predicted?: boolean;
  predictor?: PredictorName | null;
  /** etichette: numero di elementi */
  count?: number;
}

export interface SearchGroup {
  type: SearchGroupType;
  label: string;
  items: SearchItem[];
  /** il gruppo aveva più risultati del limite */
  truncated: boolean;
}

/** GET /api/search: gruppi ordinati per il miglior punteggio. */
export interface SearchResponse {
  query: string;
  /** parole cercate (minuscole, senza accenti) */
  terms: string[];
  tag: string | null;
  /** nessun testo né etichetta: i gruppi sono i preferiti */
  favorites: boolean;
  engine: "fts5" | "like";
  groups: SearchGroup[];
}

// ------------------------------------------------------------------ riproducibilità (F9.4)

/** Ruolo di un file nel pacchetto: ingressi (estratti per la riesecuzione), risultati, script, log, LEGGIMI. */
export type PackageRole =
  | "params" | "sequences" | "template" | "template_meta" | "template_prepared" | "alignment" | "structure" | "msa"
  | "output" | "script" | "log" | "documentation";

/** Strumento nel manifest (engines/versions.py): nessun percorso, solo nome e versione. */
export interface PackageTool {
  label: string;
  available: boolean;
  version: string | null;
}

export interface PackageEnvironment {
  python?: string;
  system?: string;
  machine?: string;
  libraries?: Record<string, string | null>;
  tools?: Record<string, PackageTool>;
  detected?: string;
}

export interface PackageVersion {
  version: string;
  /** BIOLOOM_IMAGE_VERSION del container; null fuori da Docker */
  image: string | null;
}

/** Livello degli esiti dell'anteprima: come i controlli preventivi. */
export interface PackageCheck {
  level: PreflightLevel;
  /** rerun_not_reproducible, rerun_unsupported, rerun_version_bioloom, rerun_version_tool, rerun_remote_input, … */
  code: string;
  message: string;
}

/** POST /api/jobs/rerun/inspect: anteprima di un pacchetto caricato e verificato (checksum compresi). */
export interface PackageSummary {
  /** pacchetto conservato nello scratch per 24 ore: controlli preventivi e avvio lo indicano */
  package_id: string;
  format_version: number;
  created_at: string;
  bioloom: PackageVersion;
  environment: PackageEnvironment;
  job: { id: string; kind: JobKind | string; title: string; status: string; created_at?: string | null;
    started_at?: string | null; finished_at?: string | null; duration_seconds?: number | null };
  kind_label: string;
  /** parametri completi del job originale */
  params: Record<string, unknown>;
  seeds: Record<string, unknown>;
  settings: { values: Record<string, unknown>; meaning: Record<string, string>; note: string } | null;
  cache: { use_cache?: boolean | null; result_from_cache: boolean; cached_at: string | null } | null;
  remote: { service: string; what: string; date: string | null; url?: string | null }[];
  databases: { name: string; stamp: unknown; note?: string }[];
  notes: string[];
  rerun: { supported: boolean; reason: string | null };
  files: { count: number; bytes: number; by_role: Partial<Record<PackageRole, { count: number; bytes: number }>>;
    list: { path: string; role: PackageRole; size: number }[] };
  /** versioni di questa installazione, per il confronto */
  current: { bioloom: PackageVersion; environment: PackageEnvironment };
  checks: PackageCheck[];
  manifest_sha256: string;
}

/** compatibile: entro le tolleranze scientifiche del tipo di calcolo (rumore numerico atteso, es. OpenMM su più thread) */
export type RerunVerdict = "identico" | "equivalente" | "compatibile" | "diverso";
export type RerunFileStatus = "identical" | "equivalent" | "different" | "missing" | "extra";

/** Confronto delle coordinate di un PDB, senza sovrapposizione. */
export interface RerunCoordinates {
  atoms: number;
  rerun_atoms: number;
  common: number;
  only_original: number;
  only_rerun: number;
  rmsd: number | null;
  /** RMSD della sola catena principale (N, CA, C, O), senza sovrapposizione */
  backbone_rmsd?: number | null;
  max_deviation: number | null;
  max_atom: string | null;
  within: boolean;
}

/** Un controllo con le tolleranze scientifiche del tipo di calcolo (domain/reproducibility.scientific_check). */
export interface RerunScientificCheck {
  what: string;
  /** numeri (energie) o testi (classe della mutagenesi); null per le strutture (conta `delta`) */
  original: number | string | null;
  rerun: number | string | null;
  delta: number | null;
  limit: number | null;
  within: boolean;
  detail?: string;
}

export interface RerunScientific {
  within: boolean;
  checks: RerunScientificCheck[];
  tolerance: Record<string, unknown>;
  text: string;
}

export interface RerunDiff {
  numbers: number;
  beyond_tolerance: number;
  max_delta: number;
  max_delta_at: string | null;
  text: number;
  structure: number;
  examples: { where: string; original: unknown; rerun: unknown }[];
}

export interface RerunFile {
  path: string;
  status: RerunFileStatus;
  role: PackageRole;
  /** conta per l'esito (non gli script né i file intermedi di MODELLER) */
  principal: boolean;
  detail?: string;
  coordinates?: RerunCoordinates;
  diff?: RerunDiff;
}

export interface RerunMetricRow {
  key: string;
  label: string;
  original: unknown;
  rerun: unknown;
  delta: number | null;
  within: boolean;
}

/** Risultato di un job `rerun` (file completi in confronto.json, rapporto in RAPPORTO_RIESECUZIONE.txt). */
export interface RerunResult {
  verdict: RerunVerdict;
  summary: string;
  kind: JobKind | string;
  original: { job_id: string; kind: string; title: string; finished_at: string | null; bioloom: string | null;
    image: string | null; package_created_at: string | null };
  counts: Record<RerunFileStatus, number>;
  structures: { path: string; coordinates: RerunCoordinates }[];
  metrics: { title: string; rows: RerunMetricRow[] }[];
  warnings: string[];
  tolerance: { coordinates_angstrom: number; score_abs: number; score_rel: number; ignored: string[] };
  /** tolleranze scientifiche del tipo di calcolo (null se non ne ha) */
  scientific?: RerunScientific | null;
  bioloom: string;
  image: string | null;
  created_at: string;
  duration_seconds: number;
  report_file: string;
  report_json: string;
  /** cartella dei file della riesecuzione nel job */
  folder: string;
  /** file non identici (al più 200) */
  files: RerunFile[];
}

// ------------------------------------------------------------- confronto con un predittore esterno (F9.3)

/** Predittore esterno: ESMFold (servizio ESM Atlas, la sequenza lascia il computer) o AlphaFold DB (l'accessione). */
export type PredictorName = "esmfold" | "alphafold";
export type PlausibilityStatus = "concordano" | "divergenze locali" | "ripiegamento diverso" | "non conclusivo";
export type PlausibilityTone = "ok" | "warn" | "err" | "info";
/** pLDDT medio della regione nella predizione: ≥ 70 sicura, 50–70 incerta, < 50 molto incerta */
export type PredictionConfidence = "sicura" | "incerta" | "molto incerta";

/** Corpo di POST /api/predict (anche `params` dei controlli preventivi `predict`). */
export interface PredictRequest {
  project_id?: string;
  predictor: PredictorName;
  /** modello da confrontare con la predizione (A, riferimento) */
  source?: StructureSource | null;
  chain?: string | null;
  /** sequenza da predire; vuota = quella della catena del modello */
  sequence?: { name: string; sequence: string } | null;
  /** solo alphafold: accessione UniProt o AF-<ACC>-F<n> */
  accession?: string | null;
}

/** Regione in cui modello e predizione divergono (domain/prediction.assess). */
export interface PlausibilityRegion {
  chain: string;
  start: string;
  end: string;
  /** indici nelle coppie del confronto (nel consenso: nei residui del rapporto) */
  start_index: number;
  end_index: number;
  length: number;
  divergent: number;
  lddt_mean: number | null;
  plddt_mean: number | null;
  confidence: PredictionConfidence | null;
  /** testi delle regioni problematiche del consenso che vi cadono */
  consensus: string[];
  where: string;
  text: string;
  tone: PlausibilityTone;
}

/** Esito motivato del confronto fra il modello e la predizione. */
export interface Plausibility {
  version: number;
  predictor: PredictorName;
  label: string;
  status: PlausibilityStatus;
  tone: PlausibilityTone;
  summary: string;
  tm_score: number | null;
  lddt: number | null;
  gdt_ts: number | null;
  rmsd: number | null;
  pairs: number;
  plddt_mean: number | null;
  confident_residues: number;
  /** lDDT medio sui residui a predizione sicura: il valore del metodo nel consenso */
  lddt_confident: number | null;
  divergent_residues: number;
  regions: PlausibilityRegion[];
  /** per coppia del confronto, nell'ordine del modello */
  /** `junction`: residuo vicino a una giunzione della sequenza inviata senza i residui mancanti (mai divergente) */
  residues: { chain: string; resnum: string; lddt: number | null; plddt: number | null; divergent: boolean; confident: boolean; junction?: boolean }[];
  consensus_available: boolean;
  notes: string[];
  limits: string;
  thresholds: Record<string, number>;
}

/** Risultato di un job `predict`. */
export interface PredictResult {
  predictor: PredictorName;
  label: string;
  origin: { service: string; cached: boolean; received?: string | null; scale?: "0-1" | "0-100"; entry?: string; version?: number | null };
  /** file della predizione tra i file del job (riconosciuto come modello predetto, pLDDT su 0–100) */
  prediction_file: string;
  recognized_as: PredictorName | null;
  /** sequenza predetta: indicata, della catena (`structure`) o completa dal file (`seqres`, catena interrotta) */
  sequence: {
    name: string;
    length: number;
    origin?: "input" | "structure" | "seqres" | null;
    gaps?: { after: string; before: string; missing: number | null }[];
    modified?: string[];
  };
  plddt: { mean: number | null; residues: number; fractions: Record<string, number> };
  accession: string | null;
  source: StructureSource | null;
  model_label: string | null;
  chain: string | null;
  source_sha256: string | null;
  /** confronto nel formato dell'allineamento strutturale (A = modello, B = predizione), per StructuralComparison */
  comparison: AlignResult | null;
  plausibility: Plausibility | null;
  warnings: string[];
  limits: string;
}

// ------------------------------------------------------------- eliminazione dei progetti e pulizia dei job

/** Stati che la pulizia dei job può eliminare: mai quelli attivi (in coda, in esecuzione, in annullamento). */
export type CleanupStatus = "completed" | "failed" | "cancelled";

/** Corpo di POST /api/jobs/cleanup/preview e /api/jobs/cleanup: ambito e stati sempre espliciti (nessun predefinito). */
export interface CleanupRequest {
  /** "all" = tutti i progetti, compresi i job senza progetto */
  scope: "project" | "all";
  /** obbligatorio con scope "project", vietato con "all" */
  project_id?: string;
  /** non vuoto */
  statuses: CleanupStatus[];
  /** assente o null = tutti i tipi; mai una lista vuota */
  kinds?: string[] | null;
  /** età = fine del job (o ultimo aggiornamento, o creazione); null = qualsiasi */
  older_than_days: number | null;
  keep_favorites: boolean;
  keep_tagged: boolean;
  /** false: escluse le pipeline fallite o annullate con almeno un elemento completato */
  include_resumable: boolean;
  /** solo /cleanup: durata massima della chiamata in secondi (il server la limita a 1–25) */
  max_seconds?: number;
  /**
   * istante UTC usato come «adesso» (`as_of` dell'anteprima confermata): l'età si misura da lì e un job finito dopo non
   * si elimina; assente = adesso, senza limite superiore (lo svuotamento di un progetto non lo invia mai)
   */
  as_of?: string;
}

/** Anteprima della pulizia: cosa verrebbe eliminato, senza eliminare nulla. */
export interface CleanupPreview {
  matched: number;
  /** byte delle cartelle dei job; con `bytes_complete` falso è un minimo (tempo di calcolo scaduto) */
  bytes: number;
  bytes_complete: boolean;
  by_status: Record<string, number>;
  by_kind: Record<string, number>;
  /** corrispondenti ai criteri ma risparmiati dalla stella o dalle etichette */
  protected: number;
  /** pipeline riprendibili risparmiate (0 con include_resumable) */
  resumable: number;
  /** i 5 job più vecchi che verrebbero eliminati */
  sample: { id: string; kind: string; title: string; status: string; finished_at: string | null }[];
  /** istante UTC usato come «adesso» (ISO 8601, +00:00): l'esecuzione confermata lo rimanda come `as_of` */
  as_of: string;
}

/** Esito di una chiamata della pulizia (a tempo: il client ripete finché `remaining` non è 0). */
export interface CleanupRun {
  /** eliminati in questa chiamata */
  deleted: number;
  /** in uso, ripresi, già eliminati altrove o non più corrispondenti (es. stella aggiunta nel frattempo): non sono errori */
  skipped: number;
  /** eccezioni inattese su singoli job */
  errors: number;
  freed_bytes: number;
  /** cartelle rimosse solo in parte: il resto lo toglie «Pulisci file temporanei» */
  leftover: number;
  /** corrispondenti non ancora esaminati (i saltati non contano) */
  remaining: number;
}

/** GET /api/projects/{id}/usage: anteprima di ciò che l'eliminazione del progetto porta via. */
export interface ProjectUsage {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  sequences: number;
  documents: number;
  jobs: { total: number; active: number; starred: number; tagged: number; resumable: number; by_status: Record<string, number> };
  bytes: number;
  bytes_complete: boolean;
}

/** DELETE /api/projects/{id}. */
export interface ProjectDeleteResult {
  deleted: string;
  jobs_deleted: number;
  freed_bytes: number;
  leftover: number;
  /** progetto vuoto creato perché era l'ultimo (BioLoom ne richiede almeno uno), altrimenti null */
  replacement: Project | null;
}

/** DELETE /api/jobs/{id}. */
export interface JobDeleteResult {
  deleted: string;
  freed_bytes: number;
  leftover: number;
}
