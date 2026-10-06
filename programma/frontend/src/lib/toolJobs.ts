// Job seguito dalle schede Tasche ed Elettrostatica della pagina Qualità (F7b), che si smontano cambiando scheda: la
// pagina conserva il job per struttura, e un calcolo in coda o in corso si ritrova da `/api/<tipo>/latest`.
// Logica pura (stessa regola della scheda Interazioni, lib/interactions.followedAnalysis).

import type { SystemInfo } from "../api/types";

export interface ToolJobMemory {
  sourceKey: string;
  jobId: string | null;
}

/** Job da cui ripartire: quello del link (`initialJob`), altrimenti quello conservato per la stessa struttura. */
export function rememberedJob(sourceKey: string, memory: ToolJobMemory | null | undefined, initialJob?: string | null): string | null {
  if (initialJob) return initialJob;
  return memory && memory.sourceKey === sourceKey ? memory.jobId : null;
}

/** Strumenti che mancano fra quelli richiesti (etichette di `/api/system/info`); vuoto se ci sono tutti o se le
 *  informazioni non sono ancora arrivate (il job lo direbbe comunque con un errore chiaro). */
export function missingTools(info: SystemInfo | null | undefined, keys: string[]): string[] {
  if (!info) return [];
  return keys.filter((k) => !info.tools[k]?.available).map((k) => info.tools[k]?.label ?? k);
}

/** Avviso per gli strumenti mancanti, con l'indicazione di dove si trovano. */
export function missingToolsText(missing: string[], purpose: string): string | null {
  if (!missing.length) return null;
  return `${purpose} non disponibile: ${missing.length === 1 ? "manca" : "mancano"} ${missing.join(", ")} in questa installazione. ` +
    "fpocket, APBS, PDB2PQR, OpenMM e PDBFixer sono nell'immagine Docker aggiornata di BioLoom: ricostruiscila dall'avviatore.";
}
