// Logica pura per lo stato di licenze, database e QMEAN (testata con vitest).
import type { DatabaseStatus, LicenseStatus, Settings } from "../api/types";

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

/** Stima leggibile di una durata in secondi. */
export function fmtSeconds(s: number): string {
  if (s < 1) return "< 1 s";
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}
