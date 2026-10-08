// Avviso per email alla fine delle pipeline: controlli e testi usati da Impostazioni e pagina Pipeline.
import type { NotifyStatus } from "../api/types";

/** Stessa regola del server (services/notify.EMAIL_RE): niente spazi né separatori, dominio con estensione. */
const EMAIL_RE = /^[^@\s<>,;"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;

export function isEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

export const SECURITY_LABEL: Record<NotifyStatus["security"], string> = {
  starttls: "STARTTLS",
  ssl: "SSL/TLS",
  none: "nessuna",
};

export const SOURCE_LABEL: Record<NotifyStatus["source"], string> = {
  env: "variabili BIOLOOM_SMTP_* (server.env)",
  file: "file licenses/posta.env della cartella dati",
  none: "nessuna configurazione",
};

/** Riga di stato del server di posta (scheda Impostazioni e nota della pagina Pipeline). */
export function notifySummary(s: NotifyStatus | null | undefined): string {
  if (!s) return "Configurazione della posta non letta.";
  if (s.error) return `Avviso via email non disponibile: ${s.error}`;
  if (!s.configured) {
    return `Avviso via email non disponibile: il server di posta lo configura chi gestisce BioLoom, nel file ${s.file} della cartella dati (o in server.env sul server).`;
  }
  return `Server di posta ${s.host}:${s.port}, mittente ${s.sender}.`;
}
