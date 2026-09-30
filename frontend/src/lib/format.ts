import type { JobKind, JobStatus } from "../api/types";

export const STATUS_LABEL: Record<string, string> = {
  queued: "In coda",
  running: "In esecuzione",
  cancelling: "Annullamento",
  completed: "Completato",
  failed: "Fallito",
  cancelled: "Annullato",
  pending: "In attesa",
  searching: "Ricerca template",
  modeling: "Modellazione",
  filtered: "Filtrato",
};

export const KIND_LABEL: Record<JobKind, string> = {
  search: "Ricerca template",
  align: "Allineamento",
  align2d: "Allineamento MODELLER",
  model: "Modellazione",
  loop: "Raffinamento loop",
  quality: "Analisi qualità",
  qmean: "QMEAN",
  pipeline: "Pipeline batch",
  selftest: "Verifica installazione",
  db_update_pdbaa: "Aggiornamento pdbaa",
  db_build_mmseqs: "Database MMseqs2",
  db_index_mmseqs: "Indice MMseqs2",
  db_convert_uniclust: "Conversione UniClust30",
  db_local_uniclust: "Copia locale UniClust30",
};

export const ACTIVE_STATUSES: JobStatus[] = ["queued", "running", "cancelling"];
export const isActive = (s: string) => (ACTIVE_STATUSES as string[]).includes(s);

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("it-IT", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("it-IT");
}

export function fmtEvalue(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v === 0) return "0";
  return v < 0.001 ? v.toExponential(1).replace("e", "·10^") : v.toPrecision(2);
}

export function fmtBytes(n: number | null | undefined): string {
  if (!n && n !== 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toLocaleString("it-IT", { maximumFractionDigits: v < 10 && i > 0 ? 1 : 0 })} ${units[i]}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("it-IT", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** Nota "dalla cache (data)" per un risultato riusato dalla cache dei calcoli; null se calcolato ora. */
export function cachedLabel(result: { cached?: boolean; cached_at?: string | null } | null | undefined): string | null {
  if (!result?.cached) return null;
  const when = result.cached_at ? new Date(result.cached_at) : null;
  return when && !Number.isNaN(when.getTime()) ? `dalla cache (${fmtDate(result.cached_at)})` : "dalla cache";
}

/** Nota sulle ricerche di template riusate dalla cache in una pipeline; null se nessuna. */
export function searchCacheHitsLabel(hits: number | null | undefined): string | null {
  if (!hits) return null;
  return `${hits} ${hits === 1 ? "ricerca" : "ricerche"} di template dalla cache`;
}

export function fmtDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const sec = Math.max(0, Math.round((e - s) / 1000));
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} min ${sec % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

/**
 * Tempo residuo stimato dal server (`eta_seconds`), approssimato come la stima: "circa 3 min rimanenti",
 * "meno di un minuto rimanente", "circa 1 h 20 min rimanenti". null se non c'è una stima.
 */
export function fmtEta(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return "meno di un minuto rimanente";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `circa ${minutes} min ${minutes === 1 ? "rimanente" : "rimanenti"}`;
  // oltre l'ora la stima è a passi di 5 minuti
  const total = Math.round(seconds / 300) * 5;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return `circa ${h} h${m ? ` ${m} min` : ""} ${h === 1 && !m ? "rimanente" : "rimanenti"}`;
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "adesso";
  if (diff < 3600) return `${Math.floor(diff / 60)} min fa`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h fa`;
  return fmtDate(iso);
}

export function gradeTone(grade?: string | null): "ok" | "info" | "warn" | "err" | "muted" {
  switch (grade) {
    case "A":
      return "ok";
    case "B":
      return "info";
    case "C":
      return "warn";
    case "D":
      return "err";
    default:
      return "muted";
  }
}

export function downloadText(filename: string, text: string, type = "text/plain") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function wrapSequence(seq: string, width = 60): string {
  const out: string[] = [];
  for (let i = 0; i < seq.length; i += width) out.push(seq.slice(i, i + width));
  return out.join("\n");
}
