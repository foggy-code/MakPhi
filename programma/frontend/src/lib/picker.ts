// Scelta di una struttura prodotta da un job (StructurePicker in components/Pickers.tsx). Logica pura, testata in
// picker.test.ts.
import type { JobSummary } from "../api/types";

export interface Choice {
  value: string;
  label: string;
}

/**
 * Job della select «Modello»: gli ultimi job di modellazione completati, più il job della struttura scelta se non è
 * tra questi (link da una pipeline, da un job loop o da un job più vecchio), in cima e segnato «da un link».
 * Senza, la select mostrerebbe «— job di modellazione —» e la struttura scelta sembrerebbe mancare.
 */
export function jobChoices(jobs: Pick<JobSummary, "id" | "title">[] | null | undefined, selected: string | null | undefined, selectedTitle?: string | null): Choice[] {
  const out = (jobs ?? []).map((j) => ({ value: j.id, label: j.title || `job ${j.id}` }));
  if (selected && !out.some((c) => c.value === selected)) {
    out.unshift({ value: selected, label: `${selectedTitle || `job ${selected}`} (da un link)` });
  }
  return out;
}

/** File dei modelli riusciti nel risultato di un job (model, loop); nessuno se il risultato non li elenca (pipeline). */
export function modelFiles(result: unknown): string[] {
  const models = result && typeof result === "object" ? (result as { models?: unknown }).models : undefined;
  if (!Array.isArray(models)) return [];
  const files: string[] = [];
  for (const m of models) {
    if (!m || typeof m !== "object") continue;
    const { file, failure } = m as { file?: unknown; failure?: unknown };
    if (typeof file === "string" && file && !failure) files.push(file);
  }
  return files;
}

/** Modelli della select: quelli del job, più il file scelto se non è tra questi (modello di un elemento di pipeline). */
export function modelChoices(models: string[], file: string | null | undefined): string[] {
  return file && !models.includes(file) ? [file, ...models] : models;
}

export interface PastedSequence {
  name: string;
  sequence: string;
}

/**
 * Testo del campo «Incolla» della sequenza bersaglio (SequencePicker): FASTA (`>nome descrizione`, poi le righe della
 * sequenza) o sequenza semplice, che resta com'è (la convalida è del server).
 *
 * Il campo conserva il testo grezzo e questa funzione dice cosa diventa il valore: un'intestazione FASTA ancora senza
 * a capo (la si sta scrivendo) non cambia nulla, e il valore resta `current`. Prima il testo si analizzava a ogni
 * tasto: al primo '>' la sequenza diventava vuota e il campo, che la mostrava, cancellava quanto scritto. Con più
 * voci FASTA vale la prima.
 */
export function parsePastedSequence(text: string, current: PastedSequence): PastedSequence {
  const trimmed = (text ?? "").trimStart();
  if (!trimmed.startsWith(">")) return { name: current.name, sequence: text ?? "" };
  const [head, ...rest] = trimmed.split(/\r?\n/);
  if (!rest.length) return current; // intestazione in corso: niente da analizzare
  const end = rest.findIndex((line) => line.startsWith(">"));
  const body = end < 0 ? rest : rest.slice(0, end);
  return {
    name: head.slice(1).trim().split(/\s+/)[0] || current.name,
    sequence: body.join("").replace(/[^A-Za-z]/g, "").toUpperCase(),
  };
}

/** Il testo grezzo del campo corrisponde ancora al valore? Se no (valore cambiato da fuori: scelta dal progetto, da
 *  file, nuovo bersaglio) il campo mostra la sequenza del valore. Il solo nome non conta: si cambia anche nel suo
 *  campo, e un'intestazione FASTA scritta resta. */
export function pastedTextMatches(text: string, value: PastedSequence): boolean {
  return parsePastedSequence(text, value).sequence === value.sequence;
}
