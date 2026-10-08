// Avviso per email alla fine delle pipeline: controlli e testi usati da Impostazioni e pagina Pipeline.
import type { NotifyStatus } from "../api/types";

/** Stessa regola del server (services/notify.EMAIL_RE): niente spazi né separatori, dominio con estensione. */
const EMAIL_RE = /^[^@\s<>,;"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;

export function isEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

export const SECURITY_LABEL: Record<NotifyStatus["security"], string> = {
  starttls: "STARTTLS (porta 587)",
  ssl: "SSL/TLS (porta 465)",
  none: "Nessuna (solo reti fidate)",
};

/** Porta tipica per la sicurezza scelta: si propone al cambio, se la porta era quella tipica dell'altra scelta. */
export const DEFAULT_PORT: Record<NotifyStatus["security"], number> = { starttls: 587, ssl: 465, none: 25 };

/** Riga di stato del server di posta (scheda Impostazioni e nota della pagina Pipeline). */
export function notifySummary(s: NotifyStatus | null | undefined): string {
  if (!s) return "Configurazione della posta non letta.";
  if (!s.configured) {
    return s.source === "server"
      ? "Avviso via email non disponibile: l'amministratore non ha configurato il server di posta (BIOLOOM_SMTP_* in server.env)."
      : "Avviso via email non disponibile: configura il server di posta in Impostazioni → Notifiche email.";
  }
  const who = s.source === "server" ? " (configurato dall'amministratore)" : "";
  return `Server di posta ${s.host}:${s.port}, mittente ${s.sender}${who}.`;
}
