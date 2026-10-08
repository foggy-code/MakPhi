// Presentazione del rapporto di qualità (pagina Qualità): esito delle metriche, passaggi del voto, scontri di un
// residuo, tratti del profilo ProSA, stati DSSP. Logica pura, testata in qualityView.test.ts.
import type { ConsensusBand, ConsensusMethod, ConsensusResult, ProsaResult, QualityReport, ResidueRow } from "../api/types";
import type { ChartData } from "./exportImage";
import { fmtNum, type Tone } from "./format";

export type { Tone } from "./format";

/** Tono delle fasce del consenso (buona ≥ 70, scarsa < 40; domain/consensus): badge, statistica, tabelle. */
export const BAND_TONE: Record<ConsensusBand, Tone> = { buona: "ok", intermedia: "warn", scarsa: "err" };

/** Outlier di Ramachandran: nessuna penalità nel voto fino allo 0,5% (domain/grading), oltre il 2% problematico. */
export function outlierTone(pct: number): Tone {
  return pct > 2 ? "err" : pct > 0.5 ? "warn" : "ok";
}

/** Residui in regioni favorite: obiettivo MolProbity oltre il 98%, sotto il 90% problematico. */
export function favoredTone(pct: number): Tone {
  return pct >= 98 ? "ok" : pct >= 90 ? "warn" : "err";
}

/** Clashscore sugli atomi pesanti (penalità del voto: clashscore / 1,5): fino a 5 buono, oltre 20 problematico. */
export function clashTone(score: number): Tone {
  return score > 20 ? "err" : score > 5 ? "warn" : "ok";
}

/** zDOPE con le soglie del consenso e del voto: fino a −1 simile a strutture native (nessuna penalità), sopra 0 probabilmente scarso. */
export function zdopeTone(z: number | null | undefined): Tone | undefined {
  if (z === null || z === undefined) return undefined;
  return z <= -1 ? "ok" : z <= 0 ? "warn" : "err";
}

/** QMEAN4 Z-score (SWISS-MODEL): da −2 in su buono, sotto −4 bassa qualità. */
export function qmeanTone(z: number | null | undefined): Tone | undefined {
  if (z === null || z === undefined) return undefined;
  return z >= -2 ? "ok" : z >= -4 ? "warn" : "err";
}

/** ProSA z-score con le fasce del consenso (−6 buona, −2 confine dei problemi); dipende dalla lunghezza della catena. */
export function prosaTone(z: number | null | undefined): Tone | undefined {
  if (z === null || z === undefined) return undefined;
  return z <= -6 ? "ok" : z <= -2 ? "warn" : "err";
}

/** G-factor globale di PROCHECK: sopra −0,5 normale, sotto −1 insolito. */
export function gFactorTone(v: number | null | undefined): Tone | undefined {
  if (v === null || v === undefined) return undefined;
  return v > -0.5 ? "ok" : v >= -1 ? "warn" : "err";
}

// ---------------------------------------------------------------------------------- voto
export const GRADE_RULE = "A da 85, B da 70, C da 55, D sotto";

export interface GradeStep {
  label: string;
  /** punti tolti (null: metrica non calcolata) */
  points: number | null;
  rule: string;
}

/** Penalità del voto, una per riga, con la regola di domain/grading applicata ai valori del rapporto. */
export function gradeSteps(report: Pick<QualityReport, "grade" | "ramachandran" | "clashes" | "dope">): GradeStep[] {
  const pen = report.grade.penalties;
  const z = report.dope?.zscore ?? null;
  return [
    {
      label: "Ramachandran",
      points: pen.ramachandran,
      rule: `${fmtNum(report.ramachandran.summary.outlier_pct)}% di outlier: 8 punti per punto percentuale oltre lo 0,5% (al massimo 40)`,
    },
    { label: "Contatti sterici", points: pen.clashes, rule: `clashscore ${fmtNum(report.clashes.clashscore, 2)} / 1,5 (al massimo 30)` },
    {
      label: "Energia zDOPE",
      points: z === null ? null : pen.dope,
      rule: z === null ? "zDOPE non calcolato: nessuna penalità" : `zDOPE ${fmtNum(z, 2)}: 10 punti per unità sopra −1 (al massimo 20)`,
    },
  ];
}

/**
 * Analisi assenti dal rapporto che una nuova analisi con le caselle correnti aggiungerebbe (se i programmi sono
 * installati): DOPE ("Calcola il profilo DOPE") e PROCHECK, ProSA ("PROCHECK e ProSA II"). I rapporti salvati dei
 * modelli spesso non hanno PROCHECK e ProSA.
 */
export function missingAnalyses(report: Pick<QualityReport, "dope" | "procheck" | "prosa">, options: { dope: boolean; extended: boolean }): string[] {
  const out: string[] = [];
  if (options.dope && (report.dope?.score === null || report.dope?.score === undefined)) out.push("DOPE");
  if (options.extended && !report.procheck) out.push("PROCHECK");
  if (options.extended && !report.prosa) out.push("ProSA");
  return out;
}

/** Elenco a parole: "DOPE", "DOPE e ProSA", "DOPE, PROCHECK e ProSA". */
export function andList(items: string[]): string {
  return items.length > 1 ? `${items.slice(0, -1).join(", ")} e ${items[items.length - 1]}` : items[0] ?? "";
}

/** Punti tolti con il segno meno tipografico ("−8,0"); zero senza segno. */
export function fmtPenalty(points: number | null): string {
  if (points === null) return "—";
  return points > 0 ? `−${fmtNum(points, 1)}` : fmtNum(0, 1);
}

// ---------------------------------------------------------------------------------- scontri
export interface AtomRef {
  chain: string;
  resnum: string;
  resname: string;
  atom: string;
}

/** Etichetta di un atomo degli scontri ("A 43 LEU CD1", domain/clashes): catena (anche vuota), residuo, nome, atomo. */
export function parseAtom(label: string): AtomRef | null {
  const m = /^(.*?) ?(\S+) (\S+) (\S+)$/.exec(label);
  return m ? { chain: m[1].trim(), resnum: m[2], resname: m[3], atom: m[4] } : null;
}

export type Clash = QualityReport["clashes"]["clashes"][number];

export interface ResidueClash {
  /** atomo del residuo */
  own: string;
  /** atomo dell'altro residuo, come nell'etichetta */
  partner: string;
  distance: number;
  overlap: number;
}

/** Scontri elencati nel rapporto (i primi per sovrapposizione) che coinvolgono il residuo. */
export function residueClashes(clashes: Clash[], r: { chain: string; resnum: string }): ResidueClash[] {
  const out: ResidueClash[] = [];
  for (const c of clashes) {
    const a = parseAtom(c.atom1);
    const b = parseAtom(c.atom2);
    if (a && a.chain === r.chain && a.resnum === r.resnum) out.push({ own: a.atom, partner: c.atom2, distance: c.distance, overlap: c.overlap });
    else if (b && b.chain === r.chain && b.resnum === r.resnum) out.push({ own: b.atom, partner: c.atom1, distance: c.distance, overlap: c.overlap });
  }
  return out;
}

// ---------------------------------------------------------------------------------- superficie
/** Residui sepolti (RSA < 0,2, come in engines/quality) su quelli con l'RSA calcolata. */
export function buriedCount(residues: Pick<ResidueRow, "rsa">[]): { buried: number; measured: number } {
  let buried = 0;
  let measured = 0;
  for (const r of residues) {
    if (r.rsa === null || r.rsa === undefined) continue;
    measured++;
    if (r.rsa < 0.2) buried++;
  }
  return { buried, measured };
}

// ---------------------------------------------------------------------------------- struttura secondaria
/** Stati DSSP nell'ordine di priorità (domain/secondary.SS_LABELS). */
export const DSSP_STATES: { code: string; label: string; group: "H" | "E" | "C" }[] = [
  { code: "H", label: "α-elica", group: "H" },
  { code: "G", label: "elica 3₁₀", group: "H" },
  { code: "I", label: "π-elica", group: "H" },
  { code: "E", label: "filamento β", group: "E" },
  { code: "B", label: "ponte β isolato", group: "E" },
  { code: "T", label: "turn", group: "C" },
  { code: "S", label: "bend", group: "C" },
  { code: "-", label: "coil", group: "C" },
];

export function dsspLabel(code: string): string {
  return DSSP_STATES.find((s) => s.code === (code.trim() || "-"))?.label ?? code;
}

/** Residui per stato DSSP (8 stati), con la percentuale sul totale; gli stati assenti restano fuori. */
export function dsspBreakdown(detail: Record<string, number>): { code: string; label: string; count: number; pct: number }[] {
  const merged: Record<string, number> = {};
  for (const [k, v] of Object.entries(detail)) merged[k.trim() || "-"] = (merged[k.trim() || "-"] ?? 0) + v;
  const total = Object.values(merged).reduce((s, v) => s + v, 0) || 1;
  const known = DSSP_STATES.filter((s) => merged[s.code]).map((s) => ({ code: s.code, label: s.label, count: merged[s.code], pct: (100 * merged[s.code]) / total }));
  const other = Object.keys(merged).filter((k) => !DSSP_STATES.some((s) => s.code === k)).map((k) => ({ code: k, label: k, count: merged[k], pct: (100 * merged[k]) / total }));
  return [...known, ...other];
}

// ---------------------------------------------------------------------------------- consenso
/** Somma dei pesi dei metodi usati (denominatore della media pesata, domain/consensus). */
export function totalWeight(c: Pick<ConsensusResult, "methods">): number {
  return c.methods.reduce((s, m) => s + m.weight, 0);
}

/** Punti che il metodo porta al consenso: normalizzato × peso / somma dei pesi (la somma è il punteggio). */
export function methodContribution(c: Pick<ConsensusResult, "methods">, m: Pick<ConsensusMethod, "normalized" | "weight">): number {
  const total = totalWeight(c);
  return total ? (m.normalized * m.weight) / total : 0;
}

/** Scala lineare a tratti di un metodo: "−2,0 → 100 · −1,0 → 70 · …". */
export function scaleText(m: Pick<ConsensusMethod, "key" | "points">): string {
  const digits = m.key === "grade" || m.key === "procheck" ? 0 : 1;
  return m.points.map(([raw, score]) => `${fmtNum(raw, digits).replace("-", "−")} → ${score}`).join(" · ");
}

// ---------------------------------------------------------------------------------- profilo ProSA
export interface ProsaPoint {
  index: number;
  energy: number;
  energy_w10: number;
}

/** Punti del profilo con l'energia sulla finestra di 10 residui (alle estremità manca), ordinati per indice. */
export function prosaPoints(profile: ProsaResult["profile"]): ProsaPoint[] {
  return profile
    .filter((p): p is ProsaPoint => typeof p.energy_w10 === "number" && Number.isFinite(p.energy_w10))
    .sort((a, b) => a.index - b.index);
}

/** Tratti consecutivi sopra lo zero (indici inclusi) con il valore massimo: le parti potenzialmente problematiche. */
export function positiveRuns(points: ProsaPoint[]): { start: number; end: number; max: number }[] {
  const out: { start: number; end: number; max: number }[] = [];
  let prev: number | null = null;
  for (const p of points) {
    if (p.energy_w10 <= 0) {
      prev = null;
      continue;
    }
    const last = out[out.length - 1];
    if (last && prev !== null && p.index === prev + 1) {
      last.end = p.index;
      last.max = Math.max(last.max, p.energy_w10);
    } else out.push({ start: p.index, end: p.index, max: p.energy_w10 });
    prev = p.index;
  }
  return out;
}

/** Posizione (in `points`, ordinati) del punto con l'indice più vicino a `value`; -1 se non ci sono punti. */
export function nearestPoint(points: { index: number }[], value: number): number {
  if (!points.length) return -1;
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].index < value) lo = mid + 1;
    else hi = mid;
  }
  return lo > 0 && value - points[lo - 1].index <= points[lo].index - value ? lo - 1 : lo;
}

/**
 * Residui del rapporto attribuibili al profilo ProSA, con gli stessi controlli di domain/perresidue.prosa_window: stesso
 * numero di righe e indici esattamente 1…N (l'indice i è il residuo i − 1); altrimenti null.
 */
export function prosaResidues<R>(profile: ProsaResult["profile"], residues: R[] | null | undefined): R[] | null {
  if (!residues || residues.length !== profile.length) return null;
  const seen = new Array<boolean>(residues.length + 1).fill(false);
  for (const p of profile) {
    if (!Number.isInteger(p.index) || p.index < 1 || p.index > residues.length || seen[p.index]) return null;
    seen[p.index] = true;
  }
  return residues;
}

/** Dati della scheda "Dati" e del CSV del profilo; con i residui attribuibili (prosaResidues) anche il nome. */
export function prosaData(profile: ProsaResult["profile"], residues: Pick<ResidueRow, "chain" | "resnum" | "resname">[] | null): ChartData {
  const named = prosaResidues(profile, residues);
  return {
    columns: [
      { key: "indice", label: "Indice", num: true },
      ...(named ? [{ key: "residuo", label: "Residuo" }] : []),
      { key: "w10", label: "Energia (finestra 10)", num: true },
      { key: "w1", label: "Energia (finestra 1)", num: true },
      { key: "sopra", label: "Sopra lo zero" },
    ],
    rows: profile.map((p) => {
      const r = named?.[p.index - 1];
      return {
        indice: p.index,
        ...(named ? { residuo: r ? `${r.resname} ${r.chain}${r.resnum}` : null } : {}),
        w10: p.energy_w10,
        w1: p.energy,
        sopra: p.energy_w10 === null ? null : p.energy_w10 > 0 ? "sì" : "no",
      };
    }),
  };
}

/** Nota sui grafici PostScript di PROCHECK conservati fra i file del job (Impostazioni → Esecuzione). */
export function procheckPlotsNote(files: string[]): string {
  const plots = files.filter((f) => f.toLowerCase().endsWith(".ps")).length;
  if (!plots) return "Grafici PostScript di PROCHECK non conservati (Impostazioni → Esecuzione): le metriche qui sopra vengono dal riepilogo.";
  return plots === 1 ? "Il grafico PostScript di PROCHECK (Ramachandran) è tra i file del job." : `I grafici PostScript di PROCHECK sono tra i file del job (${plots}).`;
}
