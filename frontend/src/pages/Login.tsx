import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { Alert, Spinner } from "../components/ui";

/** Accesso con chiave: compare solo quando BioLoom gira su un server con accesso protetto. */
export default function Login({ onDone }: { onDone: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(key.trim());
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-back">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand" style={{ padding: 0, marginBottom: 12 }}>
          <img src="/favicon.svg" alt="" />
          <div>
            <b>BioLoom</b>
            <small>modellazione per omologia</small>
          </div>
        </div>
        <p className="small text-2">
          Questa installazione di BioLoom è condivisa in rete: inserisci la chiave d'accesso che ti ha dato chi la gestisce.
        </p>
        <label className="field">
          <span>Chiave d'accesso</span>
          <input className="input mono" type="password" autoFocus autoComplete="current-password" value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        {error && <Alert tone="err">{error}</Alert>}
        <button className="btn btn-primary" type="submit" disabled={busy || !key.trim()} style={{ marginTop: 12, width: "100%" }}>
          {busy ? <Spinner /> : <Icon name="check" size={14} />} Entra
        </button>
        <p className="tiny muted" style={{ marginTop: 12 }}>
          Sul server la chiave è in <span className="mono">server.env</span> (BIOLOOM_ACCESS_KEY) oppure, se non impostata lì, nel file <span className="mono">access_key</span> della cartella dati.
        </p>
      </form>
    </div>
  );
}
