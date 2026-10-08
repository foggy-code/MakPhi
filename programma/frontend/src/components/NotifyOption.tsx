// Pagina Pipeline: «Avvisami per email alla fine», con l'indirizzo (predefinito: l'email di contatto delle Impostazioni).
// Senza server di posta la casella è spenta e la nota dice dove configurarlo.
import { Link } from "react-router-dom";
import type { NotifyStatus } from "../api/types";
import { isEmail, notifySummary } from "../lib/notify";

export function NotifyOption({ status, enabled, email, onEnabled, onEmail }: {
  status: NotifyStatus | null;
  enabled: boolean;
  email: string;
  onEnabled: (on: boolean) => void;
  onEmail: (email: string) => void;
}) {
  const ready = !!status?.configured;
  const bad = enabled && ready && !isEmail(email);
  return (
    <div className="stack-sm">
      <label className="check">
        <input type="checkbox" checked={enabled && ready} disabled={!ready} onChange={(e) => onEnabled(e.target.checked)} />
        Avvisami per email alla fine (solo esito e numero di elementi, senza risultati)
      </label>
      {enabled && ready && (
        <input className={`input${bad ? " invalid" : ""}`} type="email" value={email} maxLength={254} placeholder="nome@example.org" aria-label="Indirizzo per l'avviso"
          aria-invalid={bad || undefined} onChange={(e) => onEmail(e.target.value)} />
      )}
      {bad && <span className="small text-err">Indirizzo email non valido.</span>}
      {!ready && (
        <span className="small text-2">
          {notifySummary(status)}{status?.source !== "server" && <> <Link to="/settings">Apri le Impostazioni</Link>.</>}
        </span>
      )}
    </div>
  );
}
