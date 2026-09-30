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
