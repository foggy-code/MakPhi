// Logica pura per lo stato di licenze, database, QMEAN e job (testata con vitest).
import type { DatabaseStatus, JobSummary, LicenseStatus, ResumeItems, Settings } from "../api/types";
import { isActive } from "./format";

export type QmeanRoute = {
  /** dove partirà il calcolo; null = non è possibile calcolare QMEAN */
  source: "local" | "remote" | null;
  label: string;
  hint: string;
  /** il fallback su SWISS-MODEL è permesso (consenso all'invio ed email presenti) */
  canFallback: boolean;
};

const LOCAL_MISSING = "Collega il disco con UniClust30/UniRef30 e avvia (o riavvia) BioLoom dall'avviatore.";
const SLOW_DISK = " Dal disco condiviso HHblits è lento: copia gli indici su questo computer (Impostazioni → Database).";

/** Il database in uso arriva da un disco condiviso (Docker Desktop, rete) senza la copia locale degli indici. */
export function uniclustSlow(local: Pick<DatabaseStatus["qmean"], "databases" | "selected"> | null): boolean {
  const db = local?.databases?.find((d) => d.id === local.selected);
  return !!db?.shared && db.local?.state !== "ready";
}

/** Dove verrà calcolato QMEAN con le impostazioni e lo stato attuali del servizio locale. */
export function qmeanRoute(
  settings: Pick<Settings, "qmean_mode" | "qmean_remote" | "contact_email" | "qmean_fallback_remote">,
  local: (Pick<DatabaseStatus["qmean"], "available" | "message"> & Partial<Pick<DatabaseStatus["qmean"], "databases" | "selected" | "uniclust">>) | null,
): QmeanRoute {
  const remoteOk = settings.qmean_remote && !!settings.contact_email;
  if (settings.qmean_mode === "remote") {
    return remoteOk
      ? { source: "remote", label: "Calcola QMEAN (SWISS-MODEL)", hint: "Il modello viene inviato ai server SWISS-MODEL.", canFallback: false }
      : { source: null, label: "QMEAN su SWISS-MODEL non consentito", hint: "Consenti l'invio a SWISS-MODEL e indica la tua email nelle Impostazioni.", canFallback: false };
  }
  if (local?.available) {
    const extra = settings.qmean_fallback_remote && remoteOk ? " Se il calcolo locale non riesce, si passa a SWISS-MODEL." : "";
    const slow = (uniclustSlow({ databases: local.databases ?? [], selected: local.selected ?? null }) ? SLOW_DISK : "")
      + (local.uniclust?.memory === "swap" ? " Memoria di Docker al limite: il calcolo può durare ore (dettagli in Impostazioni → Database)." : "");
    return { source: "local", label: "Calcola QMEAN (locale, UniClust30)", hint: `Il modello non lascia il computer.${extra}${slow}`, canFallback: remoteOk };
  }
  const reason = local?.message || LOCAL_MISSING;
  if (settings.qmean_fallback_remote && remoteOk) {
    return { source: "remote", label: "Calcola QMEAN (SWISS-MODEL, fallback)", hint: `${reason} Fallback attivo: il modello viene inviato a SWISS-MODEL.`, canFallback: true };
  }
  return {
    source: null,
    label: "QMEAN locale non disponibile",
    hint: remoteOk ? `${reason} Puoi usare il fallback su SWISS-MODEL.` : `${reason} In alternativa consenti SWISS-MODEL (con la tua email) nelle Impostazioni.`,
    canFallback: remoteOk,
  };
}

/** Avvisi da mostrare per le licenze (vuoto se tutto è a posto). */
export function licenseWarnings(s: LicenseStatus): string[] {
  const out: string[] = [];
  if (!s.modeller.available) {
    out.push(s.modeller.configured
      ? `MODELLER rifiuta la chiave attuale${s.modeller.message ? `: ${s.modeller.message}` : ""}.`
      : "Manca la chiave di licenza di MODELLER: senza, la modellazione non parte.");
  }
  if (s.prosa.installed && s.prosa.license && !s.prosa.mac_ok) {
    out.push("ProSA: il MAC del container non coincide con quello della licenza. Riavvia BioLoom dall'avviatore per applicarlo.");
  }
  return out;
}

export type ResumeOptions = {
  /** pulsante principale (ambito "incomplete"): tutti gli elementi non conclusi */
  incomplete: number;
  /** testo del pulsante principale: "Riprendi", oppure "Riprova i falliti" per una pipeline completata */
  label: string;
  /** suggerimento del pulsante principale */
  hint: string;
  /** "Riprova solo i falliti": solo se diverso da "Riprendi" (ci sono anche elementi annullati o interrotti) */
  failedOnly: boolean;
  text: string;
};

const fallite = (n: number) => `${n} ${n === 1 ? "fallita" : "fallite"}`;

/**
 * Pulsanti di ripresa di una pipeline terminata (fallita, annullata, oppure completata con elementi falliti);
 * null se non c'è nulla da riprendere o il job è attivo.
 */
export function resumeOptions(
  status: string,
  job: { resumable?: boolean; resumable_items?: ResumeItems | null } | null | undefined,
): ResumeOptions | null {
  const items = job?.resumable_items;
  if (isActive(status) || !job?.resumable || !items || items.incomplete <= 0) return null;
  const done = Math.max(0, items.total - items.incomplete);
  const head = `${done} di ${items.total} sequenze concluse`;
  if (status === "completed") {
    // completata: restano solo elementi falliti, e il pulsante principale riprova quelli
    return {
      incomplete: items.incomplete,
      label: "Riprova i falliti",
      hint: "Rielabora le sequenze fallite; quelle completate restano come sono",
      failedOnly: false,
      text: `${head} · ${fallite(items.incomplete)} da riprovare.`,
    };
  }
  return {
    incomplete: items.incomplete,
    label: "Riprendi",
    hint: "Rielabora le sequenze non concluse; quelle completate restano come sono",
    failedOnly: items.failed > 0 && items.failed < items.incomplete,
    text: `${head} · ${items.incomplete} da riprendere${items.failed > 0 ? ` (${fallite(items.failed)})` : ""}.`,
  };
}

/**
 * Il job va ricaricato per intero (risultato, file, log completo, riprendibilità)? Solo quando è
 * terminato e prima non lo era, oppure quando è cambiato di nuovo (es. ripreso e concluso fra due letture).
 */
export function needsFullReload(prev: Pick<JobSummary, "status" | "updated_at"> | null, next: Pick<JobSummary, "status" | "updated_at">): boolean {
  if (isActive(next.status)) return false;
  return !prev || isActive(prev.status) || prev.status !== next.status || prev.updated_at !== next.updated_at;
}

/** Stato di `useJob` usato da polling, annullamento e ripresa (senza React, per i test). */
export type JobSync = {
  /** ultimo stato visto, per ricaricare il job completo solo quando serve */
  last: { current: JobSummary | null };
  setSummary: (s: JobSummary) => void;
  /** ricarica il job completo: risultato, file, riprendibilità, log */
  loadFull: () => Promise<void>;
};

/** Registra uno stato arrivato dal server; true se il job completo va ricaricato (`needsFullReload`). */
export function recordStatus(sync: JobSync, s: JobSummary): boolean {
  const prev = sync.last.current;
  sync.last.current = s;
  sync.setSummary(s);
  return needsFullReload(prev, s);
}

/**
 * Annullamento. Un job in coda si chiude subito (la risposta è già "cancelled") e il polling si ferma: va
 * ricaricato per intero, altrimenti non compaiono i pulsanti di ripresa. Uno in esecuzione passa da
 * "cancelling" e lo segue il polling.
 */
export async function cancelAndSync(sync: JobSync, request: () => Promise<JobSummary>): Promise<void> {
  if (recordStatus(sync, await request())) await sync.loadFull();
}

/**
 * Ripresa. La risposta arriva a preparazione finita, quando gli elementi rimessi in attesa e i loro file sono
 * già stati azzerati: si ricarica subito il job completo (file, riprendibilità) invece di tenere quelli vecchi.
 */
export async function resumeAndSync(sync: JobSync, request: () => Promise<JobSummary>): Promise<void> {
  recordStatus(sync, await request());
  await sync.loadFull();
}

/** Stima leggibile di una durata in secondi. */
export function fmtSeconds(s: number): string {
  if (s < 1) return "< 1 s";
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}
