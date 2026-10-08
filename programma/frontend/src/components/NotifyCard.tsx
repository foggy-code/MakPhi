// Impostazioni → Notifiche email: server di posta (SMTP) per l'avviso alla fine delle pipeline, con email di prova.
// Salvataggio proprio (non con il pulsante Salva della pagina): la password va a parte e non torna mai indietro.
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { NotifyStatus } from "../api/types";
import { useToast } from "../lib/context";
import { DEFAULT_PORT, isEmail, notifySummary, SECURITY_LABEL } from "../lib/notify";
import { Icon } from "./Icon";
import { Alert, Badge, Card, Disclosure, Field, Spinner } from "./ui";

type Form = Pick<NotifyStatus, "host" | "port" | "security" | "user" | "sender" | "public_url">;

function formOf(s: NotifyStatus): Form {
  return { host: s.host, port: s.port, security: s.security, user: s.user, sender: s.sender, public_url: s.public_url };
}

export function NotifyCard({ contactEmail }: { contactEmail: string }) {
  const toast = useToast();
  const [status, setStatus] = useState<NotifyStatus | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [password, setPassword] = useState("");
  const [to, setTo] = useState(contactEmail);
  const [busy, setBusy] = useState<"" | "save" | "test">("");
  useEffect(() => {
    api.notify().then((s) => { setStatus(s); setForm(formOf(s)); }).catch(toast.error);
  }, [toast]);
  useEffect(() => { if (!to && contactEmail) setTo(contactEmail); }, [contactEmail, to]);
  if (!status || !form) return null;
  const locked = status.source === "server";
  const dirty = !!password || JSON.stringify(form) !== JSON.stringify(formOf(status));
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => f && { ...f, [key]: value });
  const setSecurity = (security: Form["security"]) => setForm((f) => f && {
    ...f, security, port: Object.values(DEFAULT_PORT).includes(f.port) ? DEFAULT_PORT[security] : f.port,
  });
  const save = async (extra: { clear_password?: boolean } = {}) => {
    setBusy("save");
    try {
      const next = await api.saveNotify({ ...form, ...(password ? { password } : {}), ...extra });
      setStatus(next);
      setForm(formOf(next));
      setPassword("");
      toast.push(extra.clear_password ? "Password della posta rimossa" : "Server di posta salvato");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy("");
    }
  };
  const test = async () => {
    setBusy("test");
    try {
      const r = await api.testNotify(to.trim());
      toast.push(`Email di prova inviata a ${r.to}: controlla la casella (anche lo spam)`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy("");
    }
  };
  return (
    <Card icon="mail" title="Notifiche email" collapsible storageKey="settings.notify"
      right={<Badge tone={status.configured ? "ok" : "warn"}>{status.configured ? "pronte" : "da configurare"}</Badge>}>
      <div className="stack">
        <p className="small text-2">
          Alla fine di una pipeline con «Avvisami per email» BioLoom invia un messaggio con esito, durata e numero di elementi elaborati. Non contiene
          risultati, sequenze, nomi delle sequenze né file.
        </p>
        {locked && <Alert tone="info">In modalità server il server di posta lo configura l'amministratore (variabili BIOLOOM_SMTP_* e BIOLOOM_PUBLIC_URL in server.env).</Alert>}
        <div className="grid grid-2">
          <Field label="Server SMTP" hint="Per esempio quello dell'università o del provider di posta.">
            <input className="input mono" value={form.host} maxLength={253} readOnly={locked} placeholder="smtp.example.org" onChange={(e) => set("host", e.target.value)} />
          </Field>
          <Field label="Sicurezza">
            <select className="input" value={form.security} disabled={locked} onChange={(e) => setSecurity(e.target.value as Form["security"])}>
              {(Object.keys(SECURITY_LABEL) as Form["security"][]).map((k) => <option key={k} value={k}>{SECURITY_LABEL[k]}</option>)}
            </select>
          </Field>
          <Field label="Porta">
            <input className="input" type="number" min={1} max={65535} value={form.port} readOnly={locked} onChange={(e) => set("port", Number(e.target.value))} />
          </Field>
          <Field label="Mittente" hint="Indirizzo da cui partono i messaggi (spesso deve coincidere con l'utente).">
            <input className="input" value={form.sender} maxLength={254} readOnly={locked} placeholder="bioloom@example.org" onChange={(e) => set("sender", e.target.value)} />
          </Field>
          <Field label="Utente" hint="Vuoto se il server non chiede l'accesso.">
            <input className="input" value={form.user} maxLength={254} readOnly={locked} autoComplete="off" onChange={(e) => set("user", e.target.value)} />
          </Field>
          {!locked && (
            <Field label="Password" hint={status.password_set ? "Salvata: lascia vuoto per non cambiarla." : "Non salvata."}>
              <input className="input" type="password" value={password} maxLength={500} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} placeholder={status.password_set ? "••••••••" : ""} />
            </Field>
          )}
          <Field label="Indirizzo di BioLoom (facoltativo)" hint="Per il link al job nel messaggio, se BioLoom è raggiungibile da altri computer.">
            <input className="input mono" value={form.public_url} maxLength={300} readOnly={locked} placeholder="https://bioloom.universita.it" onChange={(e) => set("public_url", e.target.value)} />
          </Field>
        </div>
        {!locked && (
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn btn-primary" disabled={!dirty || !!busy} onClick={() => save()}>{busy === "save" ? <Spinner /> : <Icon name="save" size={14} />} Salva la posta</button>
            {status.password_set && <button type="button" className="btn btn-ghost" disabled={!!busy} onClick={() => save({ clear_password: true })}>Rimuovi la password</button>}
          </div>
        )}
        <p className="small text-2">{notifySummary(status)}</p>
        {status.configured && (
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <Field label="Email di prova a">
              <input className="input" value={to} maxLength={254} onChange={(e) => setTo(e.target.value)} placeholder="nome@example.org" />
            </Field>
            <button type="button" className="btn" disabled={!isEmail(to) || !!busy || dirty} title={dirty ? "Salva prima le modifiche" : undefined} onClick={test}>
              {busy === "test" ? <Spinner /> : <Icon name="send" size={14} />} Invia email di prova
            </button>
          </div>
        )}
        <Disclosure title="Che cosa esce dal computer">
          Solo il messaggio di avviso, verso il server di posta indicato: titolo della pipeline, esito (completata, fallita o annullata), orari, durata e
          numero di elementi per stato, più l'eventuale motivo dell'errore e il link al job. La password resta nella cartella dati (licenses/smtp_password),
          leggibile solo dal proprietario, e non viene mai mostrata né esportata.
        </Disclosure>
      </div>
    </Card>
  );
}
