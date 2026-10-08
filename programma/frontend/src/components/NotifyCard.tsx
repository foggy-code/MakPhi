// Impostazioni → Notifiche email: stato del server di posta per l'avviso alla fine delle pipeline, in sola lettura
// (si configura nel file licenses/posta.env della cartella dati o in server.env, mai da qui), con l'email di prova.
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { NotifyStatus } from "../api/types";
import { useToast } from "../lib/context";
import { isEmail, SECURITY_LABEL, SOURCE_LABEL } from "../lib/notify";
import { Icon } from "./Icon";
import { Alert, Badge, Card, DetailGrid, Disclosure, Field, Spinner } from "./ui";

export function NotifyCard({ contactEmail }: { contactEmail: string }) {
  const toast = useToast();
  const [status, setStatus] = useState<NotifyStatus | null>(null);
  const [to, setTo] = useState(contactEmail);
  const [busy, setBusy] = useState(false);
  const reload = () => api.notify().then(setStatus).catch(toast.error);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, []);
  useEffect(() => { if (!to && contactEmail) setTo(contactEmail); }, [contactEmail, to]);
  if (!status) return null;
  const test = async () => {
    setBusy(true);
    try {
      const r = await api.testNotify(to.trim());
      toast.push(`Email di prova inviata a ${r.to}: controlla la casella (anche lo spam)`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card icon="mail" title="Notifiche email" collapsible storageKey="settings.notify"
      right={<Badge tone={status.configured ? "ok" : "warn"}>{status.configured ? "pronte" : "non configurate"}</Badge>}>
      <div className="stack">
        <p className="small text-2">
          Alla fine di una pipeline con «Avvisami per email» BioLoom invia un messaggio con esito, durata e numero di elementi elaborati. Non contiene
          risultati, sequenze, nomi delle sequenze né file.
        </p>
        {status.error ? <Alert tone="err">{status.error}</Alert> : !status.configured && (
          <Alert tone="info">
            Il server di posta non si imposta da qui: lo configura chi gestisce BioLoom, copiando <span className="mono">programma/launcher/posta.env.example</span>
            {" "}nella cartella dati come <span className="mono">{status.file}</span> e compilandolo (sul server, in alternativa, le variabili BIOLOOM_SMTP_* in
            server.env). Le modifiche valgono subito: poi premi «Rileggi».
          </Alert>
        )}
        {status.configured && (
          <DetailGrid items={[
            { label: "Server SMTP", value: <span className="mono">{status.host}:{status.port}</span> },
            { label: "Sicurezza", value: SECURITY_LABEL[status.security] },
            { label: "Mittente", value: status.sender },
            { label: "Accesso", value: status.user ? `utente ${status.user}${status.password_set ? ", password impostata" : ", senza password"}` : "senza accesso" },
            { label: "Link nel messaggio", value: status.public_url || "nessuno" },
            { label: "Configurazione", value: SOURCE_LABEL[status.source] },
          ]} />
        )}
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          {status.configured && (
            <>
              <Field label="Email di prova a">
                <input className="input" value={to} maxLength={254} onChange={(e) => setTo(e.target.value)} placeholder="nome@example.org" />
              </Field>
              <button type="button" className="btn" disabled={!isEmail(to) || busy} onClick={test}>
                {busy ? <Spinner /> : <Icon name="send" size={14} />} Invia email di prova
              </button>
            </>
          )}
          <button type="button" className="btn btn-ghost" onClick={reload}><Icon name="refresh" size={14} /> Rileggi</button>
        </div>
        <Disclosure title="Che cosa esce dal computer">
          Solo il messaggio di avviso, verso il server di posta configurato: titolo della pipeline, esito (completata, fallita o annullata), orari, durata e
          numero di elementi per stato, più l'eventuale motivo dell'errore e il link al job. La password resta nel file di configurazione e non viene mai
          mostrata né esportata.
        </Disclosure>
      </div>
    </Card>
  );
}
