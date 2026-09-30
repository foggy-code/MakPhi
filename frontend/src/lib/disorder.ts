// Disordine, bassa complessità e domini del bersaglio (domain/disorder.py, F5.5): tracce della mappa delle
// caratteristiche (pagina Sequenze), richieste e avvisi di copertura prima di modellare (Modellazione, Ricerca).
import type {
  CoverageAssessment, CoverageCheck, CoverageRequest, CoverageTemplate, CoverageWarning, DisorderPrediction, SearchHit, Sequence,
  SSSegment, TargetDomain, TemplateRef,
} from "../api/types";
import type { Track } from "../components/charts";
import { accessionFromName, sequenceAccession } from "./alphafold";
import { fmtNum } from "./format";
import { parsePir } from "./pir";

export const TRACK_COLOR = {
  disorder: "#f87171",
  lowComplexity: "#fbbf24",
  domain: "#22d3ee",
  helix: "var(--helix)",
  strand: "var(--strand)",
} as const;

/** "96–140" (1-based). */
export const span = (start: number, end: number) => (start === end ? `${start}` : `${start}–${end}`);

const cleanSequence = (s: string) => s.replace(/[^A-Za-z]/g, "").toUpperCase();

export function disorderTrack(pred: DisorderPrediction): Track {
  return {
    label: "Disordine (FoldIndex)",
    color: TRACK_COLOR.disorder,
    empty: `nessuna regione di almeno ${pred.params.min_region} residui`,
    items: pred.regions.map((r) => ({
      start: r.start,
      end: r.end,
      title: `Regione disordinata predetta ${span(r.start, r.end)} (${r.length} residui, FoldIndex medio ${fmtNum(r.mean_score, 2)})`,
    })),
  };
}

export function lowComplexityTrack(pred: DisorderPrediction): Track {
  return {
    label: "Bassa complessità (SEG)",
    color: TRACK_COLOR.lowComplexity,
    empty: "nessuna",
    items: pred.low_complexity.regions.map((r) => ({ start: r.start, end: r.end, title: `Bassa complessità ${span(r.start, r.end)}: ${r.composition}` })),
  };
}

export function domainTrack(domains: TargetDomain[]): Track {
  return {
    label: "Domini (InterPro)",
    color: TRACK_COLOR.domain,
    empty: "nessun dominio annotato",
    items: domains.map((d) => ({ start: d.start, end: d.end, title: `${d.name} (${(d.accessions?.length ? d.accessions : [d.accession]).join(", ")})` })),
  };
}

/** Eliche e filamenti predetti (GOR) in una traccia sola, con il colore dello stato. */
export function gorTrack(segments: SSSegment[]): Track {
  return {
    label: "Str. secondaria (GOR)", // la mappa mostra al massimo 24 caratteri dell'etichetta
    color: TRACK_COLOR.helix,
    empty: "solo coil",
    items: segments.map((s) => ({
      start: s.start,
      end: s.end,
      color: s.type === "helix" ? TRACK_COLOR.helix : TRACK_COLOR.strand,
      title: `${s.type === "helix" ? "Elica" : "Filamento β"} predetto ${span(s.start, s.end)}`,
    })),
  };
}

/**
 * Tracce della mappa delle caratteristiche di una sequenza: disordine e bassa complessità sempre (anche vuote: "nessuna
 * regione" è un'informazione), i domini solo se noti (sequenza con accessione UniProt: `domains` non null), la
 * struttura secondaria predetta se c'è.
 */
export function featureTracks({ disorder, domains, gor }: { disorder?: DisorderPrediction | null; domains?: TargetDomain[] | null; gor?: SSSegment[] | null }): Track[] {
  const tracks: Track[] = [];
  if (disorder) tracks.push(disorderTrack(disorder), lowComplexityTrack(disorder));
  if (domains) tracks.push(domainTrack(domains));
  if (gor) tracks.push(gorTrack(gor));
  return tracks;
}

export function disorderSummary(pred: DisorderPrediction): string {
  if (!pred.regions.length) return `Nessuna regione disordinata lunga (almeno ${pred.params.min_region} residui con FoldIndex < 0).`;
  const n = pred.regions.length;
  return `${n === 1 ? "Regione disordinata predetta" : `${n} regioni disordinate predette`}: ${pred.regions.map((r) => span(r.start, r.end)).join(", ")} (${fmtNum(pred.fraction * 100, 0)}% dei residui).`;
}

export function lowComplexitySummary(pred: DisorderPrediction): string {
  const regions = pred.low_complexity.regions;
  if (!regions.length) return "Nessuna regione a bassa complessità.";
  return `Bassa complessità: ${regions.map((r) => `${span(r.start, r.end)} (${r.composition})`).join(", ")}.`;
}

/**
 * Accessione UniProt del bersaglio, per i domini: quella della sequenza del progetto con la stessa sequenza (importata da
 * UniProt), altrimenti quella di un nome con l'intestazione FASTA di UniProt (sp|ACC|NOME). "" se non c'è: niente domini.
 */
export function targetAccession(target: { name: string; sequence: string }, projectSeqs?: Pick<Sequence, "sequence" | "meta">[] | null): string {
  const seq = cleanSequence(target.sequence);
  const match = seq ? projectSeqs?.find((s) => cleanSequence(s.sequence) === seq && sequenceAccession(s)) : undefined;
  return match ? sequenceAccession(match) : accessionFromName(target.name);
}

/** Accessione del bersaglio della Modellazione. In modalità PIR è quella della sequenza bersaglio del documento (voce
 *  `sequence`, con il suo codice come nome), non quella del selettore della modalità automatica, che è nascosto e può
 *  contenere un'altra proteina (o niente, arrivando dall'Editor PIR). */
export function modelingAccession(o: { mode: "auto" | "pir"; target: { name: string; sequence: string }; pirText: string | null },
  projectSeqs?: Pick<Sequence, "sequence" | "meta">[] | null): string {
  if (o.mode === "auto") return targetAccession(o.target, projectSeqs);
  const entry = o.pirText ? parsePir(o.pirText).find((e) => e.kind === "sequence") : undefined;
  return entry ? targetAccession({ name: entry.code, sequence: entry.sequence }, projectSeqs) : "";
}

/** Richiesta di verifica per la Modellazione; null finché bersaglio e template (o PIR) non sono pronti. */
export function modelingCoverageRequest(o: {
  mode: "auto" | "pir";
  target: { name: string; sequence: string };
  templates: TemplateRef[];
  pirText: string | null;
  accession: string;
  plddtMin: number | null;
}): CoverageRequest | null {
  const accession = o.accession || null;
  if (o.mode === "pir") return o.pirText && o.pirText.includes(">P1;") ? { alignment: o.pirText, accession, plddt_min: o.plddtMin } : null;
  const sequence = cleanSequence(o.target.sequence);
  const templates = o.templates.filter((t) => t.id && t.chains.length).map((t) => ({ id: t.id, chains: t.chains }));
  if (!sequence || !templates.length) return null;
  return { sequence, accession, templates, plddt_min: o.plddtMin };
}

/** Intervalli del bersaglio allineati dagli hit della ricerca, uno per hit (candidati alternativi). */
export function hitRanges(hits: Pick<SearchHit, "template_id" | "query_start" | "query_end">[]): { label: string; start: number; end: number }[] {
  return hits
    .filter((h) => Number.isFinite(h.query_start) && Number.isFinite(h.query_end) && h.query_end >= h.query_start)
    .map((h) => ({ label: h.template_id, start: h.query_start, end: h.query_end }));
}

/** Valutazione di un hit (per `template_id`) nella risposta della verifica. */
export function hitAssessment(check: CoverageCheck | null | undefined, templateId: string): CoverageTemplate | undefined {
  return check?.templates.find((t) => t.label === templateId && !t.error);
}

/** Valutazioni per etichetta (una ricerca può avere 500 hit: una sola scansione per la tabella). */
export function assessmentMap(check: CoverageCheck | null | undefined): Map<string, CoverageTemplate> {
  const out = new Map<string, CoverageTemplate>();
  for (const t of check?.templates ?? []) if (!t.error && !out.has(t.label)) out.set(t.label, t);
  return out;
}

const KIND_SHORT: Record<CoverageWarning["kind"], string> = {
  disorder_uncovered: "disordine scoperto",
  domain_uncovered: "dominio scoperto",
  domain_partial: "dominio in parte",
  disorder_covered: "disordine coperto",
};

/** Avvisi in ordine: prima quelli da considerare (warn), poi le informazioni, ciascun gruppo per posizione. */
export function sortedWarnings(a: Partial<CoverageAssessment> | null | undefined): CoverageWarning[] {
  const rank = (w: CoverageWarning) => (w.tone === "warn" ? 0 : 1);
  return [...(a?.warnings ?? [])].sort((x, y) => rank(x) - rank(y) || x.start - y.start);
}

export interface CoverageBadge {
  label: string;
  tone: "warn";
  title: string;
}

/** Etichetta breve per la riga di un hit ("disordine scoperto · dominio scoperto"); null senza avvisi da considerare. */
export function coverageBadge(a: Partial<CoverageAssessment> | null | undefined): CoverageBadge | null {
  const warn = sortedWarnings(a).filter((w) => w.tone === "warn");
  if (!warn.length) return null;
  const labels = [...new Set(warn.map((w) => KIND_SHORT[w.kind]))];
  return { label: labels.join(" · "), tone: "warn", title: warn.map((w) => w.text).join("\n") };
}

/** Riga di sintesi sotto gli avvisi: metodo, domini (da dove o perché mancano). */
export function coverageFootnote(check: CoverageCheck): string {
  const parts: string[] = [];
  if (check.disorder) parts.push(`${disorderSummary(check.disorder)} Metodo: ${check.disorder.method}, dalla sola sequenza (indicativo).`);
  const info = check.domains_info;
  if (info?.available) parts.push(`Domini da ${info.source} (${info.accession}): ${check.domains.length ? check.domains.map((d) => `${d.name} ${span(d.start, d.end)}`).join(", ") : "nessuno annotato"}.`);
  if (info?.note) parts.push(info.note);
  return parts.join(" ");
}
